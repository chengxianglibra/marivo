"""Exact Event coverage values; value construction never consults a datasource."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from marivo.analysis.domains.errors import completeness_error
from marivo.refs import DatasourceKind, EventKind, Ref, SemanticKind, ref

if TYPE_CHECKING:
    from ibis.backends.duckdb import Backend

    from marivo.analysis.domains.contracts import EventDefinition


def aware(value: datetime, parameter: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise completeness_error(
            "an exact timezone-aware datetime", type(value).__name__, location=parameter
        )
    return value.astimezone(timezone.utc)


def _inputs(values: tuple[Ref[EventKind], ...]) -> None:
    if (
        type(values) is not tuple
        or not values
        or any(type(value) is not Ref or value.kind is not SemanticKind.EVENT for value in values)
    ):
        raise completeness_error(
            "a non-empty tuple of exact Event refs",
            "invalid inputs",
            location="completeness.inputs",
        )
    if len(set(values)) != len(values):
        raise completeness_error(
            "duplicate-free authored Event refs", "duplicate inputs", location="completeness.inputs"
        )


def _origin(value: Ref[DatasourceKind]) -> None:
    if type(value) is not Ref or value.kind is not SemanticKind.DATASOURCE:
        raise completeness_error(
            "an exact datasource Ref",
            "invalid source origin",
            location="completeness.source_origin_ref",
        )


def _text(value: str, parameter: str) -> str:
    if type(value) is not str or not value.strip():
        raise completeness_error("a non-empty string", "empty or invalid value", location=parameter)
    return value.strip()


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundedCompletenessDeclarationV1:
    inputs: tuple[Ref[EventKind], ...]
    complete_from: datetime
    complete_through: datetime
    rationale: str

    def __post_init__(self) -> None:
        _inputs(self.inputs)
        object.__setattr__(
            self, "complete_from", aware(self.complete_from, "completeness.complete_from")
        )
        object.__setattr__(
            self, "complete_through", aware(self.complete_through, "completeness.complete_through")
        )
        object.__setattr__(self, "rationale", _text(self.rationale, "completeness.rationale"))
        if self.complete_from > self.complete_through:
            raise completeness_error(
                "complete_from <= complete_through", "reversed coverage bounds"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceOriginCompletenessDeclarationV1:
    inputs: tuple[Ref[EventKind], ...]
    source_origin_ref: Ref[DatasourceKind]
    complete_through: datetime
    rationale: str

    def __post_init__(self) -> None:
        _inputs(self.inputs)
        _origin(self.source_origin_ref)
        object.__setattr__(
            self, "complete_through", aware(self.complete_through, "completeness.complete_through")
        )
        object.__setattr__(self, "rationale", _text(self.rationale, "completeness.rationale"))


CompletenessDeclaration: TypeAlias = (
    BoundedCompletenessDeclarationV1 | SourceOriginCompletenessDeclarationV1
)


def declarations_json(values: tuple[CompletenessDeclaration, ...]) -> str:
    return json.dumps(
        [
            {
                "kind": "bounded"
                if isinstance(value, BoundedCompletenessDeclarationV1)
                else "source_origin",
                "inputs": [item.key for item in value.inputs],
                "complete_from": value.complete_from.isoformat()
                if isinstance(value, BoundedCompletenessDeclarationV1)
                else None,
                "source_origin_ref": value.source_origin_ref.key
                if isinstance(value, SourceOriginCompletenessDeclarationV1)
                else None,
                "complete_through": value.complete_through.isoformat(),
                "rationale": value.rationale,
            }
            for value in values
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundedCoverageStartV1:
    complete_from: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "complete_from", aware(self.complete_from, "receipt.complete_from")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceOriginCoverageStartV1:
    source_origin_ref: Ref[DatasourceKind]

    def __post_init__(self) -> None:
        _origin(self.source_origin_ref)


@dataclass(frozen=True, slots=True, kw_only=True)
class EventCoverageRequestV1:
    event_ref: Ref[EventKind]
    event_fingerprint: str
    source_entity_ref: str
    source_origin_ref: Ref[DatasourceKind]
    occurred_at_ref: str
    required_from: datetime
    required_through: datetime
    source_binding_fingerprint: str = ""
    execution_domain_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class EventCoverageReceiptV1:
    event_ref: Ref[EventKind]
    event_fingerprint: str
    source_entity_ref: str
    source_origin_ref: Ref[DatasourceKind]
    occurred_at_ref: str
    coverage_start: BoundedCoverageStartV1 | SourceOriginCoverageStartV1
    complete_through: datetime
    authority: str
    observed_at: datetime
    source_revision: str | None = None
    source_binding_fingerprint: str = ""
    execution_domain_id: str = ""

    def __post_init__(self) -> None:
        _inputs((self.event_ref,))
        _origin(self.source_origin_ref)
        if type(self.coverage_start) not in (BoundedCoverageStartV1, SourceOriginCoverageStartV1):
            raise completeness_error("closed typed coverage start", "invalid provider receipt")
        object.__setattr__(
            self, "complete_through", aware(self.complete_through, "receipt.complete_through")
        )
        object.__setattr__(self, "observed_at", aware(self.observed_at, "receipt.observed_at"))
        for name in ("event_fingerprint", "source_entity_ref", "occurred_at_ref", "authority"):
            object.__setattr__(self, name, _text(getattr(self, name), "receipt." + name))
        if self.source_revision is not None:
            object.__setattr__(
                self, "source_revision", _text(self.source_revision, "receipt.source_revision")
            )
        if (
            isinstance(self.coverage_start, BoundedCoverageStartV1)
            and self.coverage_start.complete_from > self.complete_through
        ):
            raise completeness_error(
                "ordered provider coverage bounds", "reversed provider receipt"
            )


class EventCoverageProvider(Protocol):
    def __call__(
        self, backend: Backend, request: EventCoverageRequestV1
    ) -> EventCoverageReceiptV1 | None: ...


@dataclass(frozen=True, slots=True)
class EventObservedWatermarkV1:
    """Insufficient observed bounds supplemented by one decisive declaration."""

    complete_from: str | None
    complete_through: str
    authority: str
    observed_at: str
    source_revision: str | None


@dataclass(frozen=True, slots=True)
class EventCoverageFact:
    """Retained coverage values; normalized ISO instants cross the JSON codec boundary."""

    event_ref: str
    event_fingerprint: str
    source_origin_ref: str
    basis: Literal["observed", "declared", "unknown"]
    complete: bool
    complete_from: str | None
    complete_through: str | None
    authority: str | None
    observed_at: str | None
    source_revision: str | None
    rationale: str | None
    source_binding_fingerprint: str = ""
    execution_domain_id: str = ""
    supplemented_observation: EventObservedWatermarkV1 | None = None


@dataclass(frozen=True, slots=True)
class EventCoverageResolution:
    complete: bool
    basis: Literal["observed", "declared", "mixed", "unknown"]
    events: tuple[EventCoverageFact, ...]


def validate_declarations(definition: EventDefinition) -> None:
    if type(definition.completeness) is not tuple:
        raise completeness_error("an immutable declaration tuple", "invalid completeness")
    steps = {item.step.event: item for item in definition.steps}
    seen: set[Ref[EventKind]] = set()
    for value in definition.completeness:
        if type(value) not in (
            BoundedCompletenessDeclarationV1,
            SourceOriginCompletenessDeclarationV1,
        ):
            raise completeness_error("closed typed completeness declaration", "invalid declaration")
        if any(event in seen or event not in steps for event in value.inputs):
            raise completeness_error(
                "each consumed Event declared at most once", "unrelated or overlapping inputs"
            )
        seen.update(value.inputs)
        if value.complete_through < definition.completion_through or (
            isinstance(value, BoundedCompletenessDeclarationV1)
            and value.complete_from > definition.cohort_window.start
        ):
            raise completeness_error(
                "coverage spanning the complete Event evaluation range",
                "insufficient declared bounds",
            )
        if isinstance(value, SourceOriginCompletenessDeclarationV1) and any(
            value.source_origin_ref.path != steps[event].source.datasource_ref.path
            for event in value.inputs
        ):
            raise completeness_error(
                "the datasource authority bound by every consumed Event", "unrelated source origin"
            )


def resolve_event_coverage(
    definition: EventDefinition,
    *,
    provider: EventCoverageProvider | None = None,
    backend: Backend | None = None,
    source_binding_fingerprint: str = "",
    execution_domain_id: str = "",
    require_source_origin: bool = False,
) -> EventCoverageResolution:
    """Read each distinct Event receipt once within the caller-owned source action."""
    declarations = {event: value for value in definition.completeness for event in value.inputs}
    if provider is not None and (not source_binding_fingerprint or not execution_domain_id):
        raise completeness_error(
            "exact captured source binding and execution domain fingerprints",
            "missing provider request authority",
        )
    facts: list[EventCoverageFact] = []
    seen: set[Ref[EventKind]] = set()
    start = definition.cohort_window.start
    if not isinstance(start, datetime):
        raise completeness_error("aware Event cohort datetime", "invalid normalized window")
    for step in definition.steps:
        event = step.step.event
        if event in seen:
            continue
        seen.add(event)
        request = EventCoverageRequestV1(
            event_ref=event,
            event_fingerprint=step.event_fingerprint,
            source_entity_ref=step.source.ref.path,
            source_origin_ref=ref.datasource(step.source.datasource_ref.path),
            occurred_at_ref=step.occurred_at.ref.path,
            required_from=start,
            required_through=definition.completion_through,
            source_binding_fingerprint=source_binding_fingerprint,
            execution_domain_id=execution_domain_id,
        )
        receipt = None
        supplemented_observation = None
        if provider is not None:
            if backend is None:
                raise completeness_error(
                    "a caller-owned backend for coverage reads", "missing backend"
                )
            receipt = provider(backend, request)
        if receipt is not None:
            if type(receipt) is not EventCoverageReceiptV1 or any(
                getattr(receipt, name) != getattr(request, name)
                for name in (
                    "event_ref",
                    "event_fingerprint",
                    "source_entity_ref",
                    "source_origin_ref",
                    "occurred_at_ref",
                    "source_binding_fingerprint",
                    "execution_domain_id",
                )
            ):
                raise completeness_error(
                    "receipt bound to the exact requested Event and occurrence source",
                    "mismatched provider receipt",
                )
            if (
                isinstance(receipt.coverage_start, SourceOriginCoverageStartV1)
                and receipt.coverage_start.source_origin_ref != request.source_origin_ref
            ):
                raise completeness_error(
                    "the bound provider source origin", "mismatched provider coverage start"
                )
            lower = (
                receipt.coverage_start.complete_from
                if isinstance(receipt.coverage_start, BoundedCoverageStartV1)
                else None
            )
            complete = (
                lower is None or (not require_source_origin and lower <= start)
            ) and receipt.complete_through >= definition.completion_through
            if complete or event not in declarations:
                facts.append(
                    EventCoverageFact(
                        event_ref=event.key,
                        event_fingerprint=step.event_fingerprint,
                        source_origin_ref=request.source_origin_ref.key,
                        basis="observed",
                        complete=complete,
                        complete_from=lower.isoformat() if lower is not None else None,
                        complete_through=receipt.complete_through.isoformat(),
                        authority=receipt.authority,
                        observed_at=receipt.observed_at.isoformat(),
                        source_revision=receipt.source_revision,
                        rationale=None,
                        source_binding_fingerprint=source_binding_fingerprint,
                        execution_domain_id=execution_domain_id,
                    )
                )
                continue
            supplemented_observation = EventObservedWatermarkV1(
                lower.isoformat() if lower is not None else None,
                receipt.complete_through.isoformat(),
                receipt.authority,
                receipt.observed_at.isoformat(),
                receipt.source_revision,
            )
        declaration = declarations.get(event)
        if declaration is not None:
            lower = (
                declaration.complete_from
                if isinstance(declaration, BoundedCompletenessDeclarationV1)
                else None
            )
            facts.append(
                EventCoverageFact(
                    event_ref=event.key,
                    event_fingerprint=step.event_fingerprint,
                    source_origin_ref=request.source_origin_ref.key,
                    basis="declared",
                    complete=True,
                    complete_from=lower.isoformat() if lower is not None else None,
                    complete_through=declaration.complete_through.isoformat(),
                    authority=None,
                    observed_at=None,
                    source_revision=None,
                    rationale=declaration.rationale,
                    source_binding_fingerprint=source_binding_fingerprint,
                    execution_domain_id=execution_domain_id,
                    supplemented_observation=supplemented_observation,
                )
            )
        else:
            facts.append(
                EventCoverageFact(
                    event_ref=event.key,
                    event_fingerprint=step.event_fingerprint,
                    source_origin_ref=request.source_origin_ref.key,
                    basis="unknown",
                    complete=False,
                    complete_from=None,
                    complete_through=None,
                    authority=None,
                    observed_at=None,
                    source_revision=None,
                    rationale=None,
                    source_binding_fingerprint=source_binding_fingerprint,
                    execution_domain_id=execution_domain_id,
                )
            )
    complete = all(item.complete for item in facts)
    bases = {item.basis for item in facts}
    basis: Literal["observed", "declared", "mixed", "unknown"] = "unknown"
    if complete:
        basis = "mixed" if len(bases) > 1 else facts[0].basis
    return EventCoverageResolution(complete, basis, tuple(facts))
