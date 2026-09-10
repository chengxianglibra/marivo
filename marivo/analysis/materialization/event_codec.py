"""Closed Event journey authority and identity-free Evidence codecs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import timedelta

import ibis.expr.datatypes as dt

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    CompletenessDeclaration,
    EventCoverageFact,
    EventCoverageResolution,
    EventObservedWatermarkV1,
    SourceOriginCompletenessDeclarationV1,
    declarations_json,
)
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.materialization.contracts import (
    _array,
    _hash,
    _int,
    _obj,
    _signature,
    _text,
    invalid,
    parse_json,
    parse_timestamp,
)
from marivo.refs import ref

_COUNT_FIELDS = (
    "row_count",
    "journey_count",
    "subject_count",
    "matched_row_count",
    "missing_row_count",
    "complete_journey_count",
    "incomplete_journey_count",
    "censored_journey_count",
)
_SEMANTIC_FIELDS = (
    "kind",
    "pattern_json",
    "matching_json",
    "subject_entity_ref",
    "subject_identity_signature",
    "occurrence_identity_types",
    "cohort_start",
    "cohort_end",
    "completion_through",
    "population_definition",
    "completeness_json",
    "source_dependency_fingerprint",
    "step_event_fingerprints",
    "source_origins",
    "sampling_authority",
)


@dataclass(frozen=True, slots=True)
class EventEvidenceSummary:
    row_count: int
    journey_count: int
    subject_count: int
    matched_row_count: int
    missing_row_count: int
    complete_journey_count: int
    incomplete_journey_count: int
    censored_journey_count: int
    coverage: EventCoverageResolution


def semantics_payload(value: EventJourneySemantics) -> dict[str, object]:
    return {name: getattr(value, name) for name in _SEMANTIC_FIELDS}


def decode_semantics(value: object) -> EventJourneySemantics:
    obj = _obj(value, " ".join(_SEMANTIC_FIELDS))
    if obj["kind"] != "event/journey@v1":
        raise invalid("invalid Event journey semantics kind")
    try:
        result = EventJourneySemantics(
            _token=d._CORE_TOKEN,
            pattern_json=_text(obj["pattern_json"]),
            matching_json=_text(obj["matching_json"]),
            subject_entity_ref=_text(obj["subject_entity_ref"]),
            subject_identity_signature=_signature(obj["subject_identity_signature"]),
            occurrence_identity_types=tuple(
                _text(item) for item in _array(obj["occurrence_identity_types"])
            ),
            cohort_start=_text(obj["cohort_start"]),
            cohort_end=_text(obj["cohort_end"]),
            completion_through=_text(obj["completion_through"]),
            population_definition=_text(obj["population_definition"]),
            completeness_json=_text(obj["completeness_json"]),
            source_dependency_fingerprint=_text(obj["source_dependency_fingerprint"]),
            step_event_fingerprints=tuple(
                _text(item) for item in _array(obj["step_event_fingerprints"])
            ),
            source_origins=tuple(_text(item) for item in _array(obj["source_origins"])),
            sampling_authority=_text(obj["sampling_authority"]),
        )
        validate_semantics(result)
        return result
    except (TypeError, ValueError) as error:
        raise invalid("invalid closed Event journey authority") from error


def retained_declarations(semantics: EventJourneySemantics) -> tuple[CompletenessDeclaration, ...]:
    """Decode the authored assumptions without consulting a current catalog."""
    result: list[CompletenessDeclaration] = []
    consumed = {step.event.key for step in semantics.pattern.steps}
    seen: set[str] = set()
    for value in _array(parse_json(semantics.completeness_json)):
        item = _obj(value, "kind inputs complete_from source_origin_ref complete_through rationale")
        inputs = tuple(_text(event) for event in _array(item["inputs"]))
        if (
            not inputs
            or len(set(inputs)) != len(inputs)
            or any(event not in consumed or event in seen for event in inputs)
        ):
            raise invalid("Event completeness declaration has unrelated or repeated inputs")
        seen.update(inputs)
        refs = tuple(ref.event(event.removeprefix("event:")) for event in inputs)
        through = parse_timestamp(_text(item["complete_through"]))
        rationale = _text(item["rationale"])
        if item["kind"] == "bounded" and item["source_origin_ref"] is None:
            result.append(
                BoundedCompletenessDeclarationV1(
                    inputs=refs,
                    complete_from=parse_timestamp(_text(item["complete_from"])),
                    complete_through=through,
                    rationale=rationale,
                )
            )
        elif item["kind"] == "source_origin" and item["complete_from"] is None:
            origin = _text(item["source_origin_ref"])
            if not origin.startswith("datasource:"):
                raise invalid("invalid Event completeness source origin")
            result.append(
                SourceOriginCompletenessDeclarationV1(
                    inputs=refs,
                    source_origin_ref=ref.datasource(origin.removeprefix("datasource:")),
                    complete_through=through,
                    rationale=rationale,
                )
            )
        else:
            raise invalid("invalid closed Event completeness declaration")
    return tuple(result)


def validate_semantics(semantics: EventJourneySemantics) -> None:
    pattern, matching = semantics.pattern, semantics.matching
    if (
        pattern.model_dump_json() != semantics.pattern_json
        or matching.model_dump_json() != semantics.matching_json
    ):
        raise invalid("non-canonical Event Pattern or matching declaration")
    steps = pattern.steps
    if (
        not 1 <= len(steps) <= 4096
        or len(steps) != len(semantics.step_event_fingerprints)
        or len(steps) != len(semantics.source_origins)
        or not semantics.subject_identity_signature
        or len({name for name, _ in semantics.subject_identity_signature})
        != len(semantics.subject_identity_signature)
        or not semantics.occurrence_identity_types
        or semantics.sampling_authority not in ("exact", "sampled", "inherited_materialized")
    ):
        raise invalid("incomplete or unsupported Event source authority")
    ref.entity(semantics.subject_entity_ref)
    _hash(semantics.source_dependency_fingerprint)
    for fingerprint in semantics.step_event_fingerprints:
        _hash(fingerprint)
    for origin in semantics.source_origins:
        if not origin.startswith("datasource:"):
            raise invalid("invalid retained Event source origin")
        ref.datasource(origin.removeprefix("datasource:"))
    for logical in (
        *semantics.occurrence_identity_types,
        *(kind for _, kind in semantics.subject_identity_signature),
    ):
        value = dt.dtype(logical)
        if not (
            value.is_boolean()
            or value.is_integer()
            or value.is_floating()
            or value.is_decimal()
            or value.is_string()
            or value.is_date()
            or value.is_timestamp()
        ):
            raise invalid("unsupported Event governed identity component type")
    start, end, through = (
        parse_timestamp(value)
        for value in (semantics.cohort_start, semantics.cohort_end, semantics.completion_through)
    )
    if not start < end <= through or any(
        value.utcoffset() != timedelta(0) for value in (start, end, through)
    ):
        raise invalid("invalid normalized Event cohort or follow-up bounds")
    declarations = retained_declarations(semantics)
    if declarations_json(declarations) != semantics.completeness_json:
        raise invalid("non-canonical Event completeness declaration")
    origins = dict(zip((step.event.key for step in steps), semantics.source_origins, strict=True))
    fingerprints = dict(
        zip((step.event.key for step in steps), semantics.step_event_fingerprints, strict=True)
    )
    if any(
        fingerprints[step.event.key] != fingerprint or origins[step.event.key] != origin
        for step, fingerprint, origin in zip(
            steps, semantics.step_event_fingerprints, semantics.source_origins, strict=True
        )
    ):
        raise invalid("repeated Event has inconsistent retained source authority")
    for declaration in declarations:
        if (
            declaration.complete_through < through
            or (
                isinstance(declaration, BoundedCompletenessDeclarationV1)
                and declaration.complete_from > start
            )
            or (
                isinstance(declaration, SourceOriginCompletenessDeclarationV1)
                and any(
                    origins[event.key] != declaration.source_origin_ref.key
                    for event in declaration.inputs
                )
            )
        ):
            raise invalid("retained Event completeness does not cover its exact source range")


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise invalid("invalid Event coverage boolean")
    return value


def _utc_timestamp(value: str) -> None:
    if parse_timestamp(value).utcoffset() != timedelta(0):
        raise invalid("Event coverage instant is not normalized to UTC")


def _supplemented_observation(value: object) -> EventObservedWatermarkV1 | None:
    if value is None:
        return None
    obj = _obj(value, "complete_from complete_through authority observed_at source_revision")
    result = EventObservedWatermarkV1(
        complete_from=_optional_text(obj["complete_from"]),
        complete_through=_text(obj["complete_through"]),
        authority=_text(obj["authority"]),
        observed_at=_text(obj["observed_at"]),
        source_revision=_optional_text(obj["source_revision"]),
    )
    for timestamp in (result.complete_from, result.complete_through, result.observed_at):
        if timestamp is not None:
            _utc_timestamp(timestamp)
    if result.complete_from is not None and parse_timestamp(result.complete_from) > parse_timestamp(
        result.complete_through
    ):
        raise invalid("supplemented Event observation has reversed coverage bounds")
    return result


def _coverage_fact(value: object) -> EventCoverageFact:
    obj = _obj(
        value,
        "event_ref event_fingerprint source_origin_ref basis complete complete_from complete_through authority observed_at source_revision rationale source_binding_fingerprint execution_domain_id supplemented_observation",
    )
    basis = obj["basis"]
    if basis not in ("observed", "declared", "unknown"):
        raise invalid("invalid Event coverage basis")
    return EventCoverageFact(
        event_ref=_text(obj["event_ref"]),
        event_fingerprint=_text(obj["event_fingerprint"]),
        source_origin_ref=_text(obj["source_origin_ref"]),
        basis="observed"
        if basis == "observed"
        else "declared"
        if basis == "declared"
        else "unknown",
        complete=_boolean(obj["complete"]),
        complete_from=_optional_text(obj["complete_from"]),
        complete_through=_optional_text(obj["complete_through"]),
        authority=_optional_text(obj["authority"]),
        observed_at=_optional_text(obj["observed_at"]),
        source_revision=_optional_text(obj["source_revision"]),
        rationale=_optional_text(obj["rationale"]),
        source_binding_fingerprint=_text(obj["source_binding_fingerprint"], empty=True),
        execution_domain_id=_text(obj["execution_domain_id"], empty=True),
        supplemented_observation=_supplemented_observation(obj["supplemented_observation"]),
    )


def decode_coverage(value: object) -> EventCoverageResolution:
    obj = _obj(value, "complete basis events")
    basis = obj["basis"]
    if basis not in ("observed", "declared", "mixed", "unknown"):
        raise invalid("invalid combined Event coverage basis")
    result = EventCoverageResolution(
        complete=_boolean(obj["complete"]),
        basis="observed"
        if basis == "observed"
        else "declared"
        if basis == "declared"
        else "mixed"
        if basis == "mixed"
        else "unknown",
        events=tuple(_coverage_fact(item) for item in _array(obj["events"])),
    )
    if not result.events or len({item.event_ref for item in result.events}) != len(result.events):
        raise invalid("invalid distinct Event coverage scope")
    if result.complete != all(item.complete for item in result.events):
        raise invalid("Event coverage summary contradicts its per-Event facts")
    bases = {item.basis for item in result.events}
    expected_basis = (
        "unknown" if not result.complete else next(iter(bases)) if len(bases) == 1 else "mixed"
    )
    if result.basis != expected_basis:
        raise invalid("Event coverage basis contradicts its per-Event facts")
    for fact in result.events:
        for binding in (fact.source_binding_fingerprint, fact.execution_domain_id):
            if binding:
                _hash(binding)
        for timestamp in (fact.complete_from, fact.complete_through, fact.observed_at):
            if timestamp is not None:
                _utc_timestamp(timestamp)
        if (
            fact.complete_from is not None
            and fact.complete_through is not None
            and parse_timestamp(fact.complete_from) > parse_timestamp(fact.complete_through)
        ):
            raise invalid("Event coverage fact has reversed bounds")
        if fact.basis == "unknown":
            if fact.complete or any(
                item is not None
                for item in (
                    fact.complete_from,
                    fact.complete_through,
                    fact.authority,
                    fact.observed_at,
                    fact.source_revision,
                    fact.rationale,
                    fact.supplemented_observation,
                )
            ):
                raise invalid("unknown Event coverage carries unsupported authority")
        elif fact.complete_through is None:
            raise invalid("Event coverage lacks its follow-up boundary")
        if fact.basis == "declared" and (
            not fact.rationale
            or any(
                item is not None
                for item in (fact.authority, fact.observed_at, fact.source_revision)
            )
        ):
            raise invalid("declared Event coverage has invalid assumption authority")
        if fact.basis == "observed" and (
            not fact.authority
            or fact.rationale is not None
            or fact.observed_at is None
            or not fact.source_binding_fingerprint
            or not fact.execution_domain_id
        ):
            raise invalid("observed Event coverage lacks exact provider authority")
        if fact.supplemented_observation is not None:
            if fact.basis != "declared":
                raise invalid("supplemented Event observation requires declared authority")
            _hash(fact.source_binding_fingerprint)
            _hash(fact.execution_domain_id)
    return result


def evidence_payload(value: EventEvidenceSummary | None) -> object:
    return (
        None
        if value is None
        else {
            "schema": "marivo.event_journey_evidence/v1",
            **asdict(value),
            "coverage": coverage_payload(value.coverage),
        }
    )


def coverage_payload(value: EventCoverageResolution) -> dict[str, object]:
    return {
        "complete": value.complete,
        "basis": value.basis,
        "events": [asdict(fact) for fact in value.events],
    }


def _count(value: object) -> int:
    result = _int(value)
    if result > 2**63 - 1:
        raise invalid("Event count exceeds the exact signed int64 range")
    return result


def decode_evidence(value: object) -> EventEvidenceSummary | None:
    if value is None:
        return None
    obj = _obj(value, "schema " + " ".join(_COUNT_FIELDS) + " coverage")
    if obj["schema"] != "marivo.event_journey_evidence/v1":
        raise invalid("invalid Event journey Evidence schema")
    return EventEvidenceSummary(
        _count(obj["row_count"]),
        _count(obj["journey_count"]),
        _count(obj["subject_count"]),
        _count(obj["matched_row_count"]),
        _count(obj["missing_row_count"]),
        _count(obj["complete_journey_count"]),
        _count(obj["incomplete_journey_count"]),
        _count(obj["censored_journey_count"]),
        decode_coverage(obj["coverage"]),
    )


def summary_from_proof(
    proof: Mapping[str, object], coverage: EventCoverageResolution
) -> EventEvidenceSummary:
    """Accept only the registered native scalar proof, never identity-bearing rows."""
    if set(proof) != {*_COUNT_FIELDS, "violations"} or _int(proof["violations"]) != 0:
        raise invalid("Event journey native output proof failed")
    return EventEvidenceSummary(
        _count(proof["row_count"]),
        _count(proof["journey_count"]),
        _count(proof["subject_count"]),
        _count(proof["matched_row_count"]),
        _count(proof["missing_row_count"]),
        _count(proof["complete_journey_count"]),
        _count(proof["incomplete_journey_count"]),
        _count(proof["censored_journey_count"]),
        decode_coverage(coverage_payload(coverage)),
    )
