"""Closed, identity-free Event reducer and subject-selection authority."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.domains.completeness import EventCoverageResolution
from marivo.analysis.domains.contracts import (
    EventFunnelSemantics,
    EventJourneySemantics,
    EventTimeToEventSemantics,
    encode_journey_semantics,
)
from marivo.analysis.event import FirstPerSubject, PatternStep
from marivo.analysis.materialization.contracts import (
    _array,
    _hash,
    _obj,
    _text,
    canonical_json,
    invalid,
    parse_json,
)
from marivo.analysis.materialization.event_codec import (
    _count,
    coverage_payload,
    decode_coverage,
)
from marivo.analysis.materialization.event_codec import (
    decode_semantics as decode_journey,
)
from marivo.analysis.materialization.event_codec import (
    semantics_payload as journey_payload,
)
from marivo.analysis.materialization.event_codec import (
    validate_semantics as validate_journey,
)
from marivo.refs import ref

_FUNNEL_COUNTS = ("row_count", "group_count", "cohort_count")
_TTE_COUNTS = (
    "row_count",
    "subject_count",
    "complete_count",
    "incomplete_count",
    "coverage_censored_count",
    "not_entered_count",
    "entry_unknown_count",
)
_SELECTION_COUNTS = ("input_subject_count", "selected_subject_count", "unknown_subject_count")


@dataclass(frozen=True, slots=True)
class EventFunnelEvidenceSummary:
    row_count: int
    group_count: int
    cohort_count: int
    coverage: EventCoverageResolution


@dataclass(frozen=True, slots=True)
class EventTimeToEventEvidenceSummary:
    row_count: int
    subject_count: int
    complete_count: int
    incomplete_count: int
    coverage_censored_count: int
    not_entered_count: int
    entry_unknown_count: int
    coverage: EventCoverageResolution


EventReducerEvidenceSummary = EventFunnelEvidenceSummary | EventTimeToEventEvidenceSummary


@dataclass(frozen=True, slots=True)
class EventSelectionEvidenceSummary:
    row_count: int
    input_subject_count: int
    selected_subject_count: int
    unknown_subject_count: int
    coverage: EventCoverageResolution
    journey: EventJourneySemantics
    step: PatternStep
    input_definition: str


def _step(value: object) -> PatternStep:
    try:
        return PatternStep.model_validate_json(canonical_json(value))
    except (TypeError, ValueError) as error:
        raise invalid("invalid retained Event step authority") from error


def step_index(journey: EventJourneySemantics, step: PatternStep) -> int:
    matches = tuple(index for index, member in enumerate(journey.pattern.steps) if member == step)
    if len(matches) != 1:
        raise invalid("Event step is not an exact unique retained Pattern member")
    return matches[0]


def semantics_payload(value: EventFunnelSemantics | EventTimeToEventSemantics) -> dict[str, object]:
    result: dict[str, object] = {"kind": value.kind, "journey": journey_payload(value.journey)}
    if isinstance(value, EventFunnelSemantics):
        result.update(
            axis_refs=value.axis_refs,
            axis_dependency_fingerprints=value.axis_dependency_fingerprints,
        )
    else:
        result.update(
            from_step=value.from_step.model_dump(mode="json"),
            to_step=value.to_step.model_dump(mode="json"),
        )
    return result


def decode_semantics(value: object) -> EventFunnelSemantics | EventTimeToEventSemantics:
    if not isinstance(value, dict):
        raise invalid("invalid Event reducer semantics")
    if value.get("kind") == "event/funnel@v1":
        obj = _obj(value, "kind journey axis_refs axis_dependency_fingerprints")
        result: EventFunnelSemantics | EventTimeToEventSemantics = EventFunnelSemantics(
            _token=d._CORE_TOKEN,
            journey_json=encode_journey_semantics(decode_journey(obj["journey"])),
            axis_refs=tuple(_text(item) for item in _array(obj["axis_refs"])),
            axis_dependency_fingerprints=tuple(
                _text(item) for item in _array(obj["axis_dependency_fingerprints"])
            ),
        )
    elif value.get("kind") == "event/time-to-event@v1":
        obj = _obj(value, "kind journey from_step to_step")
        result = EventTimeToEventSemantics(
            _token=d._CORE_TOKEN,
            journey_json=encode_journey_semantics(decode_journey(obj["journey"])),
            from_step_json=_step(obj["from_step"]).model_dump_json(),
            to_step_json=_step(obj["to_step"]).model_dump_json(),
        )
    else:
        raise invalid("unsupported Event reducer semantics kind")
    validate_semantics(result)
    return result


def validate_semantics(value: EventFunnelSemantics | EventTimeToEventSemantics) -> None:
    validate_journey(value.journey)
    if value.journey_json != encode_journey_semantics(value.journey):
        raise invalid("non-canonical Event reducer journey authority")
    if isinstance(value, EventFunnelSemantics):
        if (
            not isinstance(value.journey.matching, FirstPerSubject)
            or len(value.axis_refs) != len(value.axis_dependency_fingerprints)
            or len(set(value.axis_refs)) != len(value.axis_refs)
        ):
            raise invalid("invalid Event funnel matching or axis authority")
        for axis, fingerprint in zip(
            value.axis_refs, value.axis_dependency_fingerprints, strict=True
        ):
            ref.dimension(axis)
            _hash(fingerprint)
    elif (
        value.from_step_json != value.from_step.model_dump_json()
        or value.to_step_json != value.to_step.model_dump_json()
        or step_index(value.journey, value.from_step) >= step_index(value.journey, value.to_step)
    ):
        raise invalid("Event time-to-event requires an ordered exact retained step pair")


def evidence_payload(value: EventReducerEvidenceSummary) -> dict[str, object]:
    return {
        "schema": "marivo.event_funnel_evidence/v1"
        if isinstance(value, EventFunnelEvidenceSummary)
        else "marivo.event_time_to_event_evidence/v1",
        **asdict(value),
        "coverage": coverage_payload(value.coverage),
    }


def decode_evidence(value: object) -> EventReducerEvidenceSummary:
    if not isinstance(value, dict):
        raise invalid("invalid Event reducer Evidence")
    schema = value.get("schema")
    fields = _FUNNEL_COUNTS if schema == "marivo.event_funnel_evidence/v1" else _TTE_COUNTS
    obj = _obj(value, "schema " + " ".join(fields) + " coverage")
    coverage = decode_coverage(obj["coverage"])
    if schema == "marivo.event_funnel_evidence/v1":
        return EventFunnelEvidenceSummary(
            _count(obj["row_count"]),
            _count(obj["group_count"]),
            _count(obj["cohort_count"]),
            coverage,
        )
    if schema != "marivo.event_time_to_event_evidence/v1":
        raise invalid("unsupported Event reducer Evidence schema")
    result = EventTimeToEventEvidenceSummary(
        _count(obj["row_count"]),
        _count(obj["subject_count"]),
        _count(obj["complete_count"]),
        _count(obj["incomplete_count"]),
        _count(obj["coverage_censored_count"]),
        _count(obj["not_entered_count"]),
        _count(obj["entry_unknown_count"]),
        coverage,
    )
    if (
        result.row_count != sum(getattr(result, name) for name in _TTE_COUNTS[2:])
        or result.subject_count > result.row_count
        or (result.subject_count == 0) != (result.row_count == 0)
        or (coverage.complete and (result.coverage_censored_count or result.entry_unknown_count))
    ):
        raise invalid("Event time-to-event Evidence contradicts its status or coverage counts")
    return result


def summary_from_proof(
    shape_id: str, proof: Mapping[str, object], coverage: EventCoverageResolution
) -> EventReducerEvidenceSummary:
    if shape_id not in ("event/funnel@v1", "event/time-to-event@v1"):
        raise invalid("unsupported Event reducer native proof shape")
    fields = _FUNNEL_COUNTS if shape_id == "event/funnel@v1" else _TTE_COUNTS
    if set(proof) != {*fields, "violations"} or _count(proof["violations"]) != 0:
        raise invalid("Event reducer native output proof failed")
    return decode_evidence(
        {
            "schema": "marivo.event_funnel_evidence/v1"
            if shape_id == "event/funnel@v1"
            else "marivo.event_time_to_event_evidence/v1",
            **{name: proof[name] for name in fields},
            "coverage": coverage_payload(coverage),
        }
    )


def selection_evidence_payload(value: EventSelectionEvidenceSummary | None) -> object:
    if value is None:
        return None
    return {
        "schema": "marivo.event_subject_selection_evidence/v1",
        "row_count": value.row_count,
        **{name: getattr(value, name) for name in _SELECTION_COUNTS},
        "coverage": coverage_payload(value.coverage),
        "journey": parse_json(canonical_json(journey_payload(value.journey))),
        "step": value.step.model_dump(mode="json"),
        "input_definition": value.input_definition,
    }


def decode_selection_evidence(value: object) -> EventSelectionEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(
        value,
        "schema row_count "
        + " ".join(_SELECTION_COUNTS)
        + " coverage journey step input_definition",
    )
    if obj["schema"] != "marivo.event_subject_selection_evidence/v1":
        raise invalid("unsupported Event subject-selection Evidence schema")
    result = EventSelectionEvidenceSummary(
        _count(obj["row_count"]),
        _count(obj["input_subject_count"]),
        _count(obj["selected_subject_count"]),
        _count(obj["unknown_subject_count"]),
        decode_coverage(obj["coverage"]),
        decode_journey(obj["journey"]),
        _step(obj["step"]),
        _text(obj["input_definition"]),
    )
    if (
        not isinstance(result.journey.matching, FirstPerSubject)
        or step_index(result.journey, result.step) == 0
        or not re.fullmatch(r"ds_[0-9a-f]{64}", result.input_definition)
        or result.selected_subject_count > result.input_subject_count
        or result.row_count > result.selected_subject_count
        or result.unknown_subject_count
    ):
        raise invalid("incomplete or inconsistent Event subject-selection authority")
    return result


def selection_summary_from_proof(
    proof: Mapping[str, object],
    coverage: EventCoverageResolution,
    *,
    journey: EventJourneySemantics,
    step: PatternStep,
    input_definition: str,
    row_count: int | None = None,
) -> EventSelectionEvidenceSummary:
    if set(proof) != {*_SELECTION_COUNTS, "violations"} or _count(proof["violations"]) != 0:
        raise invalid("Event subject-selection native output proof failed")
    result = decode_selection_evidence(
        parse_json(
            canonical_json(
                {
                    "schema": "marivo.event_subject_selection_evidence/v1",
                    "row_count": proof["selected_subject_count"]
                    if row_count is None
                    else row_count,
                    **{name: proof[name] for name in _SELECTION_COUNTS},
                    "coverage": coverage_payload(coverage),
                    "journey": journey_payload(journey),
                    "step": step.model_dump(mode="json"),
                    "input_definition": input_definition,
                }
            )
        )
    )
    assert result is not None
    return result
