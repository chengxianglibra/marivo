"""Rule-based static authoring-input check and post-reload object inspection.

Backend-free. Each rule is explicit and cites the evidence and rule_id that
produced it. Does not parse metric formulas or compile expressions.
"""

from __future__ import annotations

from collections.abc import Sequence

from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringObjectKind,
    AuthoringSourceInput,
    DatasetSource,
    EvidenceFact,
    SourceEvidencePack,
    derive_status,
)
from marivo.semantic.ir import DimensionIR, EntityIR, MetricIR, RelationshipIR, source_label
from marivo.semantic.ledger import LedgerStore

# decision kinds that are auto-recorded for dangerous authored objects
_DANGEROUS_DECISION_BY_KIND = {
    "metric": "metric_decomposition",
    "time_dimension": "time_dimension_identity",
}

_SOURCE_REQUIRED_KINDS = frozenset(
    ("entity", "dimension", "time_dimension", "metric", "relationship")
)
_REQUIRED_SOURCE_ROLES_BY_KIND = {
    "entity": ("primary",),
    "dimension": ("primary",),
    "time_dimension": ("primary",),
    "metric": ("primary",),
    "relationship": ("from", "to"),
}


def _source_pack(
    packs: Sequence[SourceEvidencePack], datasource: str, source: DatasetSource
) -> SourceEvidencePack | None:
    for pack in packs:
        if pack.datasource == datasource and pack.source == source:
            return pack
    return None


def _source_ref(source_input: AuthoringSourceInput) -> str:
    return f"{source_input.datasource}.{source_label(source_input.source.to_ir())}"


def check_authoring_inputs(
    *,
    packs: Sequence[SourceEvidencePack],
    object_kind: AuthoringObjectKind,
    subject_ref: str,
    sources: Sequence[AuthoringSourceInput] = (),
    semantic_refs: Sequence[str] = (),
) -> AuthoringAssessment:
    facts: list[EvidenceFact] = []
    issues: list[AssessmentIssue] = []

    if object_kind in _SOURCE_REQUIRED_KINDS and not sources:
        issues.append(
            AssessmentIssue(
                kind="missing_source",
                severity="warning",
                refs=(subject_ref,),
                message=f"{object_kind} authoring requires at least one source input.",
                rule_id="source_evidence_present",
            )
        )
        return AuthoringAssessment(
            status=derive_status(tuple(issues), ()),
            facts=tuple(facts),
            issues=tuple(issues),
            questions=(),
        )

    present_roles = {source.role for source in sources}
    for required_role in _REQUIRED_SOURCE_ROLES_BY_KIND.get(object_kind, ()):
        if required_role not in present_roles:
            issues.append(
                AssessmentIssue(
                    kind="missing_source",
                    severity="warning",
                    refs=(subject_ref, f"role:{required_role}"),
                    message=f"{object_kind} authoring requires a {required_role!r} source.",
                    rule_id="source_role_present",
                )
            )

    for source_input in sources:
        source_ref = _source_ref(source_input)
        refs = (subject_ref, f"role:{source_input.role}", source_ref)
        pack = _source_pack(packs, source_input.datasource, source_input.source)
        if pack is None:
            issues.append(
                AssessmentIssue(
                    kind="missing_source",
                    severity="warning",
                    refs=refs,
                    message=(
                        f"No source evidence for role {source_input.role!r} source "
                        f"{source_ref}. Collect it before authoring."
                    ),
                    rule_id="source_evidence_present",
                )
            )
            continue

        facts.append(
            EvidenceFact(
                id=f"source:{subject_ref}:{source_input.role}:{source_ref}",
                label="source_context",
                value={
                    "role": source_input.role,
                    "datasource": source_input.datasource,
                    "source": source_input.source.to_dict(),
                },
            )
        )

        # referenced physical columns must exist in this role's source schema
        schema_columns = {name for name, _type in pack.schema}
        missing = [column for column in source_input.columns if column not in schema_columns]
        for column in missing:
            issues.append(
                AssessmentIssue(
                    kind="missing_column",
                    severity="blocker",
                    refs=refs,
                    message=(
                        f"Column {column!r} for role {source_input.role!r} source "
                        f"{source_ref} is not in the source schema."
                    ),
                    rule_id="referenced_column_exists",
                )
            )
        present = [column for column in source_input.columns if column in schema_columns]
        if present:
            facts.append(
                EvidenceFact(
                    id=f"columns:{subject_ref}:{source_input.role}:{source_ref}",
                    label="referenced_columns",
                    value={"role": source_input.role, "columns": present},
                )
            )

    if semantic_refs:
        facts.append(
            EvidenceFact(
                id=f"semantic:{subject_ref}",
                label="semantic_dependencies",
                value=list(semantic_refs),
            )
        )

    return AuthoringAssessment(
        status=derive_status(tuple(issues), ()),
        facts=tuple(facts),
        issues=tuple(issues),
        questions=(),
    )


def inspect_authored_object(
    *,
    registry: object,
    ledger_store: LedgerStore,
    ref: str,
) -> AuthoringAssessment:
    """Cheap, backend-free inspection of a loaded authored object."""
    obj = _find_loaded(registry, ref)
    if obj is None:
        issue = AssessmentIssue(
            kind="authored_object_invalid",
            severity="blocker",
            refs=(ref,),
            message=f"{ref!r} is not in the loaded registry. Reload the project after authoring.",
            rule_id="authored_object_loaded",
        )
        return AuthoringAssessment(
            status=derive_status((issue,), ()),
            facts=(),
            issues=(issue,),
            questions=(),
        )

    facts: list[EvidenceFact] = [
        EvidenceFact(id=f"kind:{ref}", label="object_kind", value=_kind_of(obj))
    ]
    issues: list[AssessmentIssue] = []

    business_definition = getattr(getattr(obj, "ai_context", None), "business_definition", None)
    is_time_field = isinstance(obj, DimensionIR) and obj.is_time_field
    handoff = isinstance(obj, (MetricIR, DimensionIR))
    if handoff and not business_definition:
        issues.append(
            AssessmentIssue(
                kind="missing_evidence",
                severity="info",
                refs=(ref,),
                message="ai_context.business_definition is empty for a handoff object.",
                rule_id="business_definition_present",
            )
        )

    object_kind = "time_dimension" if is_time_field else _kind_of(obj)
    dangerous_kind = _DANGEROUS_DECISION_BY_KIND.get(object_kind)
    if dangerous_kind is not None:
        recorded = ledger_store.read_object(ref)
        has_decision = recorded is not None and any(
            d.decision_kind == dangerous_kind for d in recorded.decisions
        )
        if not has_decision:
            issues.append(
                AssessmentIssue(
                    kind="missing_evidence",
                    severity="warning",
                    refs=(ref,),
                    message=(
                        f"No {dangerous_kind} decision recorded. Reload so Marivo can "
                        "auto-record it, or record it explicitly."
                    ),
                    rule_id="dangerous_decision_recorded",
                )
            )

    return AuthoringAssessment(
        status=derive_status(tuple(issues), ()),
        facts=tuple(facts),
        issues=tuple(issues),
        questions=(),
    )


def _find_loaded(registry: object, ref: str) -> object | None:
    for attr in ("datasets", "fields", "metrics", "relationships"):
        collection: dict[str, object] = getattr(registry, attr, {})
        if ref in collection:
            return collection[ref]
    return None


def _kind_of(obj: object) -> str:
    if isinstance(obj, EntityIR):
        return "entity"
    if isinstance(obj, DimensionIR):
        return "dimension"
    if isinstance(obj, MetricIR):
        return "metric"
    if isinstance(obj, RelationshipIR):
        return "relationship"
    return "unknown"
