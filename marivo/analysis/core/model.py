"""Bound Query output signatures and their owning validation errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import MetricNode
from marivo._definition.types import ExactRef, Owner
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ObservationBinding


class CoreRuleError(AnalysisError):
    """A private analysis structure has invalid input or lacks a premise."""

    def __init__(self, *, expected: str, received: str, repair: str, location: str) -> None:
        super().__init__(
            message="Analysis core rule validation failed.",
            expected=expected,
            received=received,
            location=location,
            repair=AnalysisRepair(
                kind="retry",
                action=repair,
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id={
                        "frame.attribute": "analysis_frame.attribute",
                        "frame.method_facts": "analysis_frame.method_facts",
                        "frame.method_facts.show": "frame_method_facts.show",
                    }.get(location),
                ),
            ),
        )


def reject(expected: str, received: str, repair: str, location: str) -> NoReturn:
    raise CoreRuleError(expected=expected, received=received, repair=repair, location=location)


def _nonempty(value: str, location: str) -> None:
    if type(value) is not str or not value:
        reject(
            "a non-empty bound identifier",
            type(value).__name__,
            "Supply the exact owner value.",
            location,
        )


def _unique(values: tuple[str, ...], location: str) -> None:
    if type(values) is not tuple or any(type(value) is not str or not value for value in values):
        reject(
            "an immutable tuple of identifiers",
            type(values).__name__,
            "Use exact identifiers.",
            location,
        )
    if len(set(values)) != len(values):
        reject("distinct identifiers", repr(values), "Remove the repeated binding.", location)


@dataclass(frozen=True, slots=True)
class BoundQuantity:
    """One named Metric output; physical dtype resolves when its source binds."""

    name: str
    definition: MetricNode
    unit: str | None

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name or any(c in self.name for c in "\n\r\t"):
            reject(
                "a nonempty single-line output name",
                repr(self.name),
                "Give each Metric a distinct output name.",
                "query.metrics",
            )


@dataclass(frozen=True, slots=True)
class ResultQuantity:
    """A computed Query value without a source Metric recipe."""

    name: str
    unit: str | None
    kind: Literal["numeric", "boolean", "string", "date", "timestamp", "duration", "unresolved"] = (
        "numeric"
    )

    def __post_init__(self) -> None:
        _nonempty(self.name, "query.values")


@dataclass(frozen=True, slots=True)
class BoundSignature:
    """Multi-output contract for one bound Query invocation."""

    owner: Owner
    quantities: tuple[BoundQuantity | ResultQuantity, ...]
    axes: tuple[ComparisonAxis, ...] = ()
    declared_metrics: tuple[tuple[ExactRef, MetricNode], ...] = ()
    observation: ObservationBinding | None = None

    def __post_init__(self) -> None:
        if type(self.quantities) is not tuple or any(
            type(item) not in (BoundQuantity, ResultQuantity) for item in self.quantities
        ):
            reject(
                "an immutable tuple of named Metric outputs",
                type(self.quantities).__name__,
                "Construct an AnalysisFrame through session.observe(metrics).",
                "query.signature",
            )
        names = tuple(item.name for item in self.quantities)
        _unique(names, "query.metrics")
        _nonempty(self.owner.session_id, "query.session_id")
        _nonempty(self.owner.owner_id, "query.owner_id")
