"""Exact Event coverage values; value construction never consults a datasource."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, TypeAlias

from marivo.analysis.domains.errors import completeness_error
from marivo.refs import DatasourceKind, EventKind, Ref, SemanticKind

if TYPE_CHECKING:
    from ibis.backends.duckdb import Backend


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
