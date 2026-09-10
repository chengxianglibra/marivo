"""Immutable Event source definitions and catalog-free dense journey meaning."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from marivo._temporal import TimeScope
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.domains.completeness import CompletenessDeclaration, declarations_json
from marivo.analysis.event import EventPattern, EveryStart, FirstPerSubject, PatternStep
from marivo.analysis.observation.contracts import dimension_payload, entity_payload, scope_payload
from marivo.analysis.observation.source_bindings import BoundSourceParametersV1
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
