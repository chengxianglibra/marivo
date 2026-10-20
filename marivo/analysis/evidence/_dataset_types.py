"""Immutable terminal Evidence values and committed output Findings."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from types import UnionType
from typing import Literal, TypeAlias, Union, get_args, get_origin, get_type_hints

from marivo.analysis._pages import _BoundedPage
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.refs import ArtifactRef
from marivo.render import Card, RenderableResult

Scalar: TypeAlias = str | int | float | bool | Decimal | date | datetime | None
Number: TypeAlias = int | float | Decimal
FindingType: TypeAlias = Literal["association", "forecast_point"]


def _text(value: str, *, limit: int = 4096) -> None:
    if not value or len(value.encode("utf-8")) > limit or "\n" in value or "\r" in value:
        raise invalid("invalid bounded Evidence text")


def _count(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise invalid("invalid Evidence count")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise invalid("Evidence timestamp is not timezone-aware")


def _matches(value: object, annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(_matches(value, item) for item in get_args(annotation))
    if origin is Literal:
        return any(type(value) is type(item) and value == item for item in get_args(annotation))
    if origin is tuple:
        arguments = get_args(annotation)
        return (
            type(value) is tuple
            and len(value) <= 4096
            and (
                all(_matches(item, arguments[0]) for item in value)
                if len(arguments) == 2 and arguments[1] is Ellipsis
                else len(value) == len(arguments)
                and all(
                    _matches(item, annotation)
                    for item, annotation in zip(value, arguments, strict=True)
                )
            )
        )
    if annotation is type(None):
        return value is None
    return isinstance(annotation, type) and type(value) is annotation


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _Value(RenderableResult):
    """Validate exact annotations without coercion, retaining scalar identity."""

    def __post_init__(self) -> None:
        hints = get_type_hints(type(self))
        for field in fields(self):
            value: object = getattr(self, field.name)
            if not _matches(value, hints[field.name]):
                raise invalid("Evidence value does not match its closed field type")
            if type(value) is str:
                _text(value)
            if type(value) is float and not math.isfinite(value):
                raise invalid("non-finite Evidence number")
            if isinstance(value, Decimal) and not value.is_finite():
                raise invalid("non-finite Evidence decimal")

    def _repr_identity(self) -> str:
        identity: object = getattr(self, "finding_id", getattr(self, "artifact_ref", None))
        if identity is not None:
            return type(self).__name__ + f" ref={str(identity)[:100]}"
        for name in ("evidence_digest", "kind"):
            value: object = getattr(self, name, None)
            if isinstance(value, str):
                return type(self).__name__ + f" {name}={value[:100]}"
        return type(self).__name__ + " immutable"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for field in fields(self):
            card.field(field.name, repr(getattr(self, field.name))[:256])
        return card


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactEvidenceSummary(_Value):
    quality_summary_digest: str
    typed_issue_digest: str
    evidence_digest: str
    finding_count: int
    finding_set_digest: str

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.finding_count)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactIssueCounts(_Value):
    warning: int
    blocking: int

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.warning)
        _count(self.blocking)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class NoAssociationLagV1(_Value):
    kind: Literal["none"] = "none"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AssociationLagV1(_Value):
    lag_offset: int
    selected_for_pair: bool
    matched_observation_count: int
    lag_boundary_drop_count: int
    kind: Literal["lag"] = "lag"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.matched_observation_count)
        _count(self.lag_boundary_drop_count)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AssociationFindingValueV1(_Value):
    method: Literal["pearson", "spearman", "kendall"]
    coefficient: Number
    input_observation_count: int
    null_pair_count: int
    complete_pair_count: int
    lag: NoAssociationLagV1 | AssociationLagV1
    causal_claim: Literal["none"] = "none"
    kind: Literal["association"] = "association"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.input_observation_count)
        _count(self.null_pair_count)
        _count(self.complete_pair_count, minimum=2)
        if self.null_pair_count + self.complete_pair_count > self.input_observation_count:
            raise invalid("inconsistent Association pair counts")
        if not -1 <= self.coefficient <= 1:
            raise invalid("Association coefficient outside [-1, 1]")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ForecastPointFindingValueV1(_Value):
    model: Literal["naive@v1", "drift@v1", "seasonal_naive@v1"]
    interval_level: Number
    horizon_ordinal: int
    forecast_value: Number
    interval_lower: Number
    interval_upper: Number
    training_row_count: int
    interval_method: Literal["normal_residual@v1"] = "normal_residual@v1"
    kind: Literal["forecast_point"] = "forecast_point"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.horizon_ordinal, minimum=1)
        _count(self.training_row_count, minimum=1)
        if not 0 < self.interval_level < 1 or not (
            self.interval_lower <= self.forecast_value <= self.interval_upper
        ):
            raise invalid("invalid Forecast interval")


FindingValueV1: TypeAlias = AssociationFindingValueV1 | ForecastPointFindingValueV1


@dataclass(frozen=True, slots=True, repr=False)
class OutputCoordinate(_Value):
    slot_id: str
    value: Scalar

    def __post_init__(self) -> None:
        _output_text(self.slot_id, "output.coordinate")
        _scalar(self.value)

    def _repr_identity(self) -> str:
        return f"OutputCoordinate slot={self.slot_id[:100]}"


def _output_text(value: str, location: str) -> None:
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        reject(
            "nonempty single-line data text", repr(value), "Use exact output metadata.", location
        )


def _scalar(value: Scalar) -> None:
    if (
        type(value) not in (str, int, float, bool, Decimal, date, datetime, type(None))
        or (isinstance(value, float) and not math.isfinite(value))
        or (isinstance(value, Decimal) and not value.is_finite())
    ):
        reject(
            "a finite output scalar without semantic identity",
            type(value).__name__,
            "Save an explicit output value, not a semantic object or graph.",
            "output.value",
        )


def _coordinates(coordinates: tuple[OutputCoordinate, ...]) -> None:
    if type(coordinates) is not tuple or any(type(c) is not OutputCoordinate for c in coordinates):
        reject(
            "immutable data coordinates",
            type(coordinates).__name__,
            "Reference output slots and scalar values only.",
            "output.coordinates",
        )


@dataclass(frozen=True, slots=True, repr=False)
class OutputEvidence(_Value):
    """A declared, read or checked output fact, not an executable proof chain."""

    kind: Literal["declaration", "read", "check"]
    summary: str
    values: tuple[tuple[str, Scalar], ...] = ()
    coordinates: tuple[OutputCoordinate, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("declaration", "read", "check"):
            reject(
                "a declaration/read/check output fact",
                str(self.kind),
                "Report only actual declarations, reads or checks.",
                "output.evidence",
            )
        _output_text(self.summary, "output.evidence.summary")
        if type(self.values) is not tuple:
            reject(
                "immutable named scalar facts",
                type(self.values).__name__,
                "Save explicit output facts only.",
                "output.evidence",
            )
        for name, value in self.values:
            _output_text(name, "output.evidence.value")
            _scalar(value)
        _coordinates(self.coordinates)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class Finding(_Value):
    """A committed method output, readable without semantic subjects or derivations."""

    finding_id: str
    artifact_ref: ArtifactRef
    session_id: str
    value: FindingValueV1
    coordinates: tuple[OutputCoordinate, ...]
    committed_at: datetime

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _aware(self.committed_at)
        _coordinates(self.coordinates)

    @property
    def finding_type(self) -> FindingType:
        return self.value.kind

    def _card(self) -> Card:
        card = (
            Card(identity=self._repr_identity(), available=(".value", ".coordinates", ".show()"))
            .field("artifact", str(self.artifact_ref))
            .field("session", self.session_id)
            .field("committed_at", self.committed_at.isoformat())
        )
        for field in fields(self.value):
            card.field(field.name, repr(getattr(self.value, field.name))[:256])
        for coordinate in self.coordinates:
            card.field(coordinate.slot_id, repr(coordinate.value)[:256])
        return card


@dataclass(frozen=True, slots=True, repr=False)
class FindingPage(_BoundedPage[Finding]):
    """Exact Artifact-scoped bounded page of self-contained output Findings."""

    def __post_init__(self) -> None:
        if (
            type(self.limit) is not int
            or type(self.items) is not tuple
            or type(self.has_more) is not bool
            or (self.next_cursor is not None and type(self.next_cursor) is not str)
        ):
            raise invalid("invalid output Finding page type")
        _BoundedPage.__post_init__(self)
        if any(type(item) is not Finding for item in self.items):
            raise invalid("invalid output Finding page item")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactDigest(ArtifactEvidenceSummary):
    """Committed collection digests and all actual output Evidence facts."""

    artifact_ref: ArtifactRef
    items: tuple[OutputEvidence, ...]
    digest_version: Literal["frame-output-v3"] = "frame-output-v3"

    def __post_init__(self) -> None:
        for value in (
            self.quality_summary_digest,
            self.typed_issue_digest,
            self.evidence_digest,
            self.finding_set_digest,
        ):
            _text(value)
        _count(self.finding_count)
        if (
            type(self.artifact_ref) is not ArtifactRef
            or type(self.items) is not tuple
            or any(type(item) is not OutputEvidence for item in self.items)
            or self.digest_version != "frame-output-v3"
        ):
            raise invalid("invalid complete output Evidence digest")

    @property
    def evidence_count(self) -> int:
        return len(self.items)

    def _card(self) -> Card:
        card = (
            Card(identity=self._repr_identity(), available=(".items", ".show()"))
            .field("evidence_count", str(self.evidence_count))
            .field("finding_count", str(self.finding_count))
            .field("evidence_digest", self.evidence_digest)
        )
        for index, item in enumerate(self.items[:20]):
            card.field(f"fact.{index}", f"{item.kind}: {item.summary}"[:512])
            card.field(f"values.{index}", repr(item.values)[:1024])
            if item.coordinates:
                card.field(
                    f"coordinates.{index}",
                    repr(
                        tuple(
                            (coordinate.slot_id, coordinate.value)
                            for coordinate in item.coordinates
                        )
                    )[:1024],
                )
        if len(self.items) > 20:
            card.field(
                "remaining_facts", f"{len(self.items) - 20}; read .items for all saved facts"
            )
        return card
