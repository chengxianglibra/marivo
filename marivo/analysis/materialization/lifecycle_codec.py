"""Closed catalog-free Lifecycle history and bounded Evidence codecs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.materialization.contracts import ArtifactDescriptor
    from marivo.analysis.materialization.lifecycle_reducer_codec import ContinuationEvidence

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.domains.completeness import EventCoverageResolution
from marivo.analysis.domains.lifecycle import (
    ROLES,
    LifecycleSemantics,
)
from marivo.analysis.materialization.contracts import _obj
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.event_codec import (
    coverage_payload,
    decode_coverage,
)
from marivo.analysis.materialization.event_codec import validate_semantics as validate_source


def invalid(detail: str) -> MaterializationError:
    from marivo.analysis.materialization.contracts import invalid as fail

    return fail(detail)


def decode_semantics(value: object) -> LifecycleSemantics:
    from marivo.analysis.domains.lifecycle import decode_lifecycle_semantics

    try:
        result = decode_lifecycle_semantics(value)
    except DatasetConstructionError:
        raise invalid("invalid retained Lifecycle model authority") from None
    validate_source(result.source)
    return result


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceSummary:
    coverage: EventCoverageResolution
    row_count: int
    subject_count: int
    seeded_count: int
    not_incepted_count: int
    coverage_censored_count: int
    transition_count: int
    violation_count: int
    left_clipped_count: int


def evidence_payload(value: LifecycleEvidenceSummary | ContinuationEvidence | None) -> object:
    if value is not None and not isinstance(value, LifecycleEvidenceSummary):
        from marivo.analysis.materialization.lifecycle_reducer_codec import (
            evidence_payload as continuation_payload,
        )

        return continuation_payload(value)
    return (
        None if value is None else {**asdict(value), "coverage": coverage_payload(value.coverage)}
    )


def decode_evidence(value: object) -> LifecycleEvidenceSummary | ContinuationEvidence | None:
    if isinstance(value, dict) and "kind" in value:
        from marivo.analysis.materialization.lifecycle_reducer_codec import (
            decode_evidence as decode_continuation,
        )

        return decode_continuation(value)
    if value is None:
        return None
    fields = "row_count subject_count seeded_count not_incepted_count coverage_censored_count transition_count violation_count left_clipped_count"
    obj = _obj(value, "coverage " + fields)
    counts: list[int] = []
    for name in fields.split():
        item = obj[name]
        if type(item) is not int or item < 0:
            raise invalid("invalid bounded Lifecycle count")
        counts.append(item)
    result = LifecycleEvidenceSummary(decode_coverage(obj["coverage"]), *counts)
    if (
        result.subject_count
        != result.seeded_count + result.not_incepted_count + result.coverage_censored_count
        or result.left_clipped_count > result.row_count
    ):
        raise invalid("inconsistent Lifecycle Evidence counts")
    return result


def validate_descriptor(descriptor: ArtifactDescriptor) -> None:
    if str(descriptor.row_contract.shape_id) != "lifecycle/history@v1":
        from marivo.analysis.materialization.lifecycle_reducer_codec import (
            validate_descriptor as validate_continuation,
        )

        validate_continuation(descriptor)
        return
    semantics = descriptor.row_contract.family_semantics
    if not isinstance(semantics, LifecycleSemantics):
        raise invalid("missing Lifecycle history semantics")
    decode_semantics(json.loads(json.dumps(asdict(semantics))))
    summary = decode_evidence(evidence_payload(descriptor.lifecycle_evidence))
    if (
        not isinstance(summary, LifecycleEvidenceSummary)
        or summary.row_count != descriptor.storage_receipt.realized_row_count
    ):
        raise invalid("missing or inconsistent Lifecycle Evidence")
    if (
        descriptor.population_authority.entity_ref != semantics.source.subject_entity_ref
        or descriptor.population_authority.identity_signature
        != semantics.source.subject_identity_signature
    ):
        raise invalid("Lifecycle subject differs from retained membership authority")
    parts = tuple(p for p in descriptor.retained_parts if p.role != "population_sampling_state")
    expected_counts = (summary.transition_count, summary.subject_count, summary.violation_count)
    if len(parts) != 3 or {p.role for p in parts} != set(ROLES):
        raise invalid("missing exact required Lifecycle retained roles")
    for role, count in zip(ROLES, expected_counts, strict=True):
        part = next(p for p in parts if p.role == role)
        if (
            part.contract_id != role
            or part.contract_version != 1
            or part.storage_receipt.realized_row_count != count
        ):
            raise invalid("Lifecycle part contract or count differs from history authority")
    expected = tuple(
        dict.fromkeys(
            zip(
                (s.event.key for s in semantics.source.pattern.steps),
                semantics.source.step_event_fingerprints,
                semantics.source.source_origins,
                strict=True,
            )
        )
    )
    if (
        tuple(
            (f.event_ref, f.event_fingerprint, f.source_origin_ref) for f in summary.coverage.events
        )
        != expected
    ):
        raise invalid("Lifecycle coverage differs from exact retained trigger authority")
    from datetime import datetime

    from marivo.analysis.domains.completeness import SourceOriginCompletenessDeclarationV1
    from marivo.analysis.materialization.event_codec import retained_declarations

    declarations = {
        event.key: declaration
        for declaration in retained_declarations(semantics.source)
        for event in declaration.inputs
    }
    end = datetime.fromisoformat(semantics.source.cohort_end)
    for fact in summary.coverage.events:
        complete = (
            fact.basis != "unknown"
            and fact.complete_from is None
            and fact.complete_through is not None
            and datetime.fromisoformat(fact.complete_through) >= end
        )
        if fact.complete != complete:
            raise invalid("Lifecycle completeness lacks exact source-origin authority")
        declaration = declarations.get(fact.event_ref)
        if fact.basis == "declared" and (
            not isinstance(declaration, SourceOriginCompletenessDeclarationV1)
            or fact.complete_from is not None
            or fact.complete_through != declaration.complete_through.isoformat()
            or fact.rationale != declaration.rationale
        ):
            raise invalid("Lifecycle coverage differs from its retained declaration")
        if declaration is not None and not complete:
            raise invalid("Lifecycle coverage discards a complete source-origin declaration")
    if (summary.coverage.complete and summary.coverage_censored_count) or (
        not summary.coverage.complete and (summary.seeded_count or summary.not_incepted_count)
    ):
        raise invalid("Lifecycle subject classifications contradict source coverage")
