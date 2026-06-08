"""Rule-based static authoring-input check and post-reload object inspection.

Backend-free. Each rule is explicit and cites the evidence and rule_id that
produced it. Does not parse metric formulas or compile expressions.
"""

from __future__ import annotations

from collections.abc import Sequence

from marivo.semantic.evidence import (
    AssessmentIssue,
    AssessmentResult,
    AuthoringObjectKind,
    AuthoringSourceInput,
    DatasetSource,
    EvidenceFact,
    SourceEvidencePack,
    derive_status,
)
from marivo.semantic.evidence_store import EvidenceStore
from marivo.semantic.ir import DatasetIR, FieldIR, MetricIR, RelationshipIR, source_label
from marivo.semantic.ledger import LedgerStore

# decision kinds that are auto-recorded for dangerous authored objects
_DANGEROUS_DECISION_BY_KIND = {
    "metric": "metric_decomposition",
    "time_field": "time_field_identity",
}

_SOURCE_REQUIRED_KINDS = frozenset(("dataset", "field", "time_field", "metric", "relationship"))
_REQUIRED_SOURCE_ROLES_BY_KIND = {
    "dataset": ("primary",),
    "field": ("primary",),
    "time_field": ("primary",),
    "metric": ("primary",),
    "relationship": ("from", "to"),
}


def _source_pack(
    store: EvidenceStore, datasource: str, source: DatasetSource
) -> SourceEvidencePack | None:
    for ref in store.list_evidence(datasource=datasource, source=source):
        pack = store.read_pack(ref.id)
        if isinstance(pack, SourceEvidencePack):
            return pack
    return None


def _source_ref(source_input: AuthoringSourceInput) -> str:
    return f"{source_input.datasource}.{source_label(source_input.source.to_ir())}"


def check_authoring_inputs(
    *,
    store: EvidenceStore,
    object_kind: AuthoringObjectKind,
    subject_ref: str,
    sources: Sequence[AuthoringSourceInput] = (),
    semantic_refs: Sequence[str] = (),
) -> AssessmentResult:
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
                evidence_refs=(),
            )
        )
        return AssessmentResult(
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
                    evidence_refs=(),
                )
            )

    for source_input in sources:
        source_ref = _source_ref(source_input)
        refs = (subject_ref, f"role:{source_input.role}", source_ref)
        pack = _source_pack(store, source_input.datasource, source_input.source)
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
                    evidence_refs=(),
                )
            )
            continue

        pack_evidence_refs = tuple(ref.id for ref in pack.evidence_refs)
        facts.append(
            EvidenceFact(
                id=f"source:{subject_ref}:{source_input.role}:{source_ref}",
                label="source_context",
                value={
                    "role": source_input.role,
                    "datasource": source_input.datasource,
                    "source": source_input.source.to_dict(),
                },
                evidence_refs=pack_evidence_refs,
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
                    evidence_refs=pack_evidence_refs,
                )
            )
        present = [column for column in source_input.columns if column in schema_columns]
        if present:
            facts.append(
                EvidenceFact(
                    id=f"columns:{subject_ref}:{source_input.role}:{source_ref}",
                    label="referenced_columns",
                    value={"role": source_input.role, "columns": present},
                    evidence_refs=pack_evidence_refs,
                )
            )

    if semantic_refs:
        facts.append(
            EvidenceFact(
                id=f"semantic:{subject_ref}",
                label="semantic_dependencies",
                value=list(semantic_refs),
                evidence_refs=(),
            )
        )

    return AssessmentResult(
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
) -> AssessmentResult:
    """Cheap, backend-free inspection of a loaded authored object."""
    obj = _find_loaded(registry, ref)
    if obj is None:
        issue = AssessmentIssue(
            kind="authored_object_invalid",
            severity="blocker",
            refs=(ref,),
            message=f"{ref!r} is not in the loaded registry. Reload the project after authoring.",
            rule_id="authored_object_loaded",
            evidence_refs=(),
        )
        return AssessmentResult(
            status=derive_status((issue,), ()),
            facts=(),
            issues=(issue,),
            questions=(),
        )

    facts: list[EvidenceFact] = [
        EvidenceFact(id=f"kind:{ref}", label="object_kind", value=_kind_of(obj), evidence_refs=())
    ]
    issues: list[AssessmentIssue] = []

    business_definition = getattr(getattr(obj, "ai_context", None), "business_definition", None)
    is_time_field = isinstance(obj, FieldIR) and obj.is_time_field
    handoff = isinstance(obj, (MetricIR, FieldIR))
    if handoff and not business_definition:
        issues.append(
            AssessmentIssue(
                kind="missing_evidence",
                severity="info",
                refs=(ref,),
                message="ai_context.business_definition is empty for a handoff object.",
                rule_id="business_definition_present",
                evidence_refs=(),
            )
        )

    object_kind = "time_field" if is_time_field else _kind_of(obj)
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
                    evidence_refs=(),
                )
            )

    return AssessmentResult(
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
    if isinstance(obj, DatasetIR):
        return "dataset"
    if isinstance(obj, FieldIR):
        return "field"
    if isinstance(obj, MetricIR):
        return "metric"
    if isinstance(obj, RelationshipIR):
        return "relationship"
    return "unknown"
