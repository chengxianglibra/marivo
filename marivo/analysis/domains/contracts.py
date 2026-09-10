"""Immutable Event source definitions and catalog-free dense journey meaning."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from marivo._temporal import TimeScope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.domains.completeness import CompletenessDeclaration, declarations_json
from marivo.analysis.event import EventPattern, EveryStart, FirstPerSubject, PatternStep
from marivo.analysis.observation.contracts import dimension_payload, entity_payload, scope_payload
from marivo.analysis.observation.source_bindings import BoundSourceParametersV1
from marivo.analysis.subject import DroppedBefore
from marivo.semantic.ir import TargetDimensionContract, TargetEntityContract


@dataclass(frozen=True, slots=True, repr=False)
class EventStepBinding:
    step: PatternStep
    source: TargetEntityContract
    identity: tuple[TargetDimensionContract, ...]
    occurred_at: TargetDimensionContract
    subject: TargetEntityContract
    participant_path: tuple[str, ...]
    event_fingerprint: str

    def identity_payload(self) -> CanonicalValue:
        return (
            self.step.fingerprint,
            entity_payload(self.source),
            tuple(dimension_payload(item) for item in self.identity),
            dimension_payload(self.occurred_at),
            entity_payload(self.subject),
            self.participant_path,
            self.event_fingerprint,
        )


@dataclass(frozen=True, slots=True, repr=False)
class EventDefinition:
    entity: TargetEntityContract
    pattern: EventPattern
    steps: tuple[EventStepBinding, ...]
    cohort_window: TimeScope
    completion_through: datetime
    matching: FirstPerSubject | EveryStart
    population_definition: str
    completeness: tuple[CompletenessDeclaration, ...]
    source_dependency_fingerprint: str
    sampling_authority: str = "exact"

    def identity_payload(self) -> CanonicalValue:
        return (
            "event.definition@v1",
            entity_payload(self.entity),
            self.pattern.fingerprint,
            tuple(step.identity_payload() for step in self.steps),
            scope_payload(self.cohort_window),
            self.completion_through.isoformat(),
            self.matching.model_dump_json(),
            self.population_definition,
            declarations_json(self.completeness),
            self.source_dependency_fingerprint,
            self.sampling_authority,
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class EventPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    definition: EventDefinition
    captures: tuple[BoundSourceParametersV1, ...]

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            self.definition.identity_payload(),
            tuple(item.identity_payload() for item in self.captures),
        )


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class EventJourneySemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    pattern_json: str
    matching_json: str
    subject_entity_ref: str
    subject_identity_signature: tuple[tuple[str, str], ...]
    occurrence_identity_types: tuple[str, ...]
    cohort_start: str
    cohort_end: str
    completion_through: str
    population_definition: str
    completeness_json: str
    source_dependency_fingerprint: str
    step_event_fingerprints: tuple[str, ...]
    source_origins: tuple[str, ...]
    sampling_authority: str = "exact"
    kind: Literal["event/journey@v1"] = field(default="event/journey@v1", init=False)

    @property
    def pattern(self) -> EventPattern:
        return EventPattern.model_validate_json(self.pattern_json)

    @property
    def matching(self) -> FirstPerSubject | EveryStart:
        value: object = json.loads(self.matching_json)
        if isinstance(value, dict) and value.get("kind") == "first_per_subject":
            return FirstPerSubject.model_validate(value)
        return EveryStart.model_validate(value)


def journey_semantics(definition: EventDefinition) -> EventJourneySemantics:
    return EventJourneySemantics(
        _token=d._CORE_TOKEN,
        pattern_json=definition.pattern.model_dump_json(),
        matching_json=definition.matching.model_dump_json(),
        subject_entity_ref=definition.entity.ref.path,
        subject_identity_signature=definition.entity.identity_signature,
        occurrence_identity_types=tuple(item.logical_type for item in definition.steps[0].identity),
        cohort_start=definition.cohort_window.start.isoformat(),
        cohort_end=definition.cohort_window.end.isoformat(),
        completion_through=definition.completion_through.isoformat(),
        population_definition=definition.population_definition,
        completeness_json=declarations_json(definition.completeness),
        source_dependency_fingerprint=definition.source_dependency_fingerprint,
        step_event_fingerprints=tuple(item.event_fingerprint for item in definition.steps),
        source_origins=tuple(
            f"datasource:{item.source.datasource_ref.path}" for item in definition.steps
        ),
        sampling_authority=definition.sampling_authority,
    )


def journey_identity_digest(semantics: EventJourneySemantics) -> str:
    """Bind opaque journey coordinates to the retained exact Pattern and policy."""
    return d._canonical_digest(
        (
            "events.match@v1",
            semantics.pattern.fingerprint,
            semantics.matching.model_dump_json(),
            semantics.step_event_fingerprints,
        )
    )


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class EventFunnelSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    journey_json: str
    axis_refs: tuple[str, ...] = ()
    axis_dependency_fingerprints: tuple[str, ...] = ()
    kind: Literal["event/funnel@v1"] = field(default="event/funnel@v1", init=False)

    @property
    def journey(self) -> EventJourneySemantics:
        return decode_journey_semantics(self.journey_json)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class EventTimeToEventSemantics(d.DatasetFamilyRowSemantics, _token=d._CORE_TOKEN):
    journey_json: str
    from_step_json: str
    to_step_json: str
    kind: Literal["event/time-to-event@v1"] = field(default="event/time-to-event@v1", init=False)

    @property
    def journey(self) -> EventJourneySemantics:
        return decode_journey_semantics(self.journey_json)

    @property
    def from_step(self) -> PatternStep:
        return PatternStep.model_validate_json(self.from_step_json)

    @property
    def to_step(self) -> PatternStep:
        return PatternStep.model_validate_json(self.to_step_json)


@dataclass(frozen=True, slots=True, repr=False)
class EventAxisBinding:
    dimension: TargetDimensionContract
    subject: TargetEntityContract
    path: tuple[str, ...]
    dependency_fingerprint: str

    def identity_payload(self) -> CanonicalValue:
        return (
            dimension_payload(self.dimension),
            entity_payload(self.subject),
            self.path,
            self.dependency_fingerprint,
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class EventFunnelPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    semantics: EventFunnelSemantics
    axes: tuple[EventAxisBinding, ...] = ()
    captures: tuple[BoundSourceParametersV1, ...] = ()

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            d._descriptor_payload(self.semantics),
            tuple(axis.identity_payload() for axis in self.axes),
            tuple(item.identity_payload() for item in self.captures),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class EventTimeToEventPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    semantics: EventTimeToEventSemantics

    @property
    def identity_payload(self) -> CanonicalValue:
        return (d._descriptor_payload(self.semantics),)


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class EventSelectionPayload(_LogicalNodePayload, _token=d._CORE_TOKEN):
    journey: EventJourneySemantics
    selection: DroppedBefore

    @property
    def identity_payload(self) -> CanonicalValue:
        return (d._descriptor_payload(self.journey), self.selection.fingerprint)


def encode_journey_semantics(value: EventJourneySemantics) -> str:
    return json.dumps(asdict(value), sort_keys=True, separators=(",", ":"))


def decode_journey_semantics(value: str) -> EventJourneySemantics:
    from marivo.analysis.domains.errors import reducer_error

    raw: object = json.loads(value)
    if not isinstance(raw, dict) or raw.get("kind") != "event/journey@v1":
        raise reducer_error("closed retained journey authority", "invalid journey encoding")

    def text(name: str) -> str:
        result: object = raw.get(name)
        if not isinstance(result, str):
            raise reducer_error("a retained journey text fact", "invalid journey field")
        return result

    def texts(name: str) -> tuple[str, ...]:
        result: object = raw.get(name)
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise reducer_error("retained ordered text facts", "invalid journey sequence")
        return tuple(item for item in result if isinstance(item, str))

    signature: object = raw.get("subject_identity_signature")
    if not isinstance(signature, list):
        raise reducer_error("a complete retained subject signature", "invalid identity signature")
    pairs: list[tuple[str, str]] = []
    for pair in signature:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(part, str) for part in pair)
        ):
            raise reducer_error("ordered name and type pairs", "invalid identity signature")
        first: object = pair[0]
        second: object = pair[1]
        if isinstance(first, str) and isinstance(second, str):
            pairs.append((first, second))
    return EventJourneySemantics(
        _token=d._CORE_TOKEN,
        pattern_json=text("pattern_json"),
        matching_json=text("matching_json"),
        subject_entity_ref=text("subject_entity_ref"),
        subject_identity_signature=tuple(pairs),
        occurrence_identity_types=texts("occurrence_identity_types"),
        cohort_start=text("cohort_start"),
        cohort_end=text("cohort_end"),
        completion_through=text("completion_through"),
        population_definition=text("population_definition"),
        completeness_json=text("completeness_json"),
        source_dependency_fingerprint=text("source_dependency_fingerprint"),
        step_event_fingerprints=texts("step_event_fingerprints"),
        source_origins=texts("source_origins"),
        sampling_authority=text("sampling_authority"),
    )
