"""Rule-based static authoring-input check and post-reload object inspection.

Backend-free. Each rule is explicit and cites the evidence and rule_id that
produced it. Does not parse metric formulas or compile expressions.
"""

from __future__ import annotations

from collections.abc import Sequence

from marivo.semantic.evidence import (
    AiContextInput,
    AssessmentIssue,
    AssessmentResult,
    AuthoringObjectKind,
    DatasetSource,
    EvidenceFact,
    NextCheck,
    SourceEvidencePack,
    derive_status,
)
from marivo.semantic.evidence_store import EvidenceStore
from marivo.semantic.ir import DatasetIR, FieldIR, MetricIR, RelationshipIR
from marivo.semantic.ledger import LedgerStore

# evidence kinds that establish business meaning for a metric
_ESTABLISHING_KINDS = ("source_sql", "knowledge_document", "user_confirmation")

# decision kinds that are auto-recorded for dangerous authored objects
_DANGEROUS_DECISION_BY_KIND = {
    "metric": "metric_decomposition",
    "time_field": "time_field_identity",
}


def _source_pack(
    store: EvidenceStore, datasource: str, source: DatasetSource
) -> SourceEvidencePack | None:
    for ref in store.list_evidence(datasource=datasource, source=source):
        pack = store.read_pack(ref.id)
        if isinstance(pack, SourceEvidencePack):
            return pack
    return None


def check_authoring_inputs(
    *,
    store: EvidenceStore,
    object_kind: AuthoringObjectKind,
    subject_ref: str,
    datasource: str,
    source: DatasetSource,
    columns: Sequence[str] = (),
    semantic_refs: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    ai_context: AiContextInput | None = None,
) -> AssessmentResult:
    facts: list[EvidenceFact] = []
    issues: list[AssessmentIssue] = []
    next_checks: list[NextCheck] = []

    pack = _source_pack(store, datasource, source)
    if pack is None:
        issues.append(
            AssessmentIssue(
                kind="missing_source",
                severity="warning",
                refs=(subject_ref,),
                message=(
                    f"No source evidence for {datasource} / {source.table or source.path}. "
                    "Collect it before authoring."
                ),
                rule_id="source_evidence_present",
                evidence_refs=(),
                next_checks=("inspect_source_context",),
            )
        )
        next_checks.append("inspect_source_context")
        return AssessmentResult(
            status=derive_status(tuple(issues), ()),
            facts=tuple(facts),
            issues=tuple(issues),
            questions=(),
            next_checks=tuple(dict.fromkeys(next_checks)),
        )

    facts.append(
        EvidenceFact(
            id=f"source:{subject_ref}",
            label="source_evidence",
            value=source.to_dict(),
            evidence_refs=tuple(ref.id for ref in pack.evidence_refs),
        )
    )

    # referenced physical columns must exist in the source schema
    schema_columns = {name for name, _type in pack.schema}
    missing = [c for c in columns if c not in schema_columns]
    for column in missing:
        issues.append(
            AssessmentIssue(
                kind="missing_column",
                severity="blocker",
                refs=(subject_ref,),
                message=f"Column {column!r} is not in the source schema.",
                rule_id="referenced_column_exists",
                evidence_refs=tuple(ref.id for ref in pack.evidence_refs),
                next_checks=("inspect_source_context",),
            )
        )
    present = [c for c in columns if c in schema_columns]
    if present:
        facts.append(
            EvidenceFact(
                id=f"columns:{subject_ref}",
                label="referenced_columns",
                value=present,
                evidence_refs=tuple(ref.id for ref in pack.evidence_refs),
            )
        )

    # metric & relationship require establishing evidence for business meaning
    if object_kind in ("metric", "relationship"):
        cited = set(evidence_refs)
        establishing = [
            ref
            for ref in store.list_evidence(subject_refs=(subject_ref,))
            if ref.kind in _ESTABLISHING_KINDS and ref.id in cited
        ]
        if not establishing:
            kind_word = "formula" if object_kind == "metric" else "relationship intent"
            issues.append(
                AssessmentIssue(
                    kind="missing_evidence",
                    severity="warning",
                    refs=(subject_ref,),
                    message=(
                        f"No cited source SQL, BI definition, or user confirmation for the "
                        f"{kind_word}. Record it with record_authoring_evidence(...) and cite it."
                    ),
                    rule_id="establishing_evidence_cited",
                    evidence_refs=(),
                    next_checks=("ask_user",),
                )
            )

    # handoff objects should carry a business_definition
    if object_kind in ("metric", "field", "time_field") and (
        ai_context is None or not ai_context.business_definition
    ):
        issues.append(
            AssessmentIssue(
                kind="missing_evidence",
                severity="info",
                refs=(subject_ref,),
                message="ai_context.business_definition is empty for a handoff object.",
                rule_id="business_definition_present",
                evidence_refs=(),
            )
        )

    return AssessmentResult(
        status=derive_status(tuple(issues), ()),
        facts=tuple(facts),
        issues=tuple(issues),
        questions=(),
        next_checks=tuple(dict.fromkeys(next_checks)),
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
            next_checks=("reload_project",),
        )
        return AssessmentResult(
            status=derive_status((issue,), ()),
            facts=(),
            issues=(issue,),
            questions=(),
            next_checks=("reload_project",),
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
                    next_checks=("reload_project",),
                )
            )

    return AssessmentResult(
        status=derive_status(tuple(issues), ()),
        facts=tuple(facts),
        issues=tuple(issues),
        questions=(),
        next_checks=(),
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
