"""Immutable explicit endpoint inputs and Session-owned investigation capture."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ibis.common.deferred import Deferred

from marivo._grouping import Grouping
from marivo._temporal import TimeScope
from marivo.analysis.dsl.temporal import CalendarAxis


@dataclass(frozen=True, slots=True, repr=False)
class AttributeEndpoint:
    """Explicit observation inputs; constructing this record performs no reads.

    Args:
        metrics: Ordered named resolved Metrics, copied on construction.
        during: The explicit endpoint TimeScope or CalendarAxis.
        where: The endpoint field or Population condition.
        memberships: Explicit allocation rules for existing endpoint coordinates.

    Example: ``AttributeEndpoint({'revenue': revenue}, during=period)``.
    Constraints: Session.attribute validates ownership and binding; the record
    does not retain an executable Session or a historical Frame producer.
    """

    metrics: Mapping[str, Deferred]
    during: TimeScope | CalendarAxis | None = None
    where: Deferred | None = None
    memberships: tuple[Grouping, ...] = ()

    def __post_init__(self) -> None:
        from marivo.analysis.core.model import reject

        if not isinstance(self.metrics, Mapping) or not self.metrics:
            reject(
                "a nonempty named Metric mapping",
                type(self.metrics).__name__,
                "Provide resolved named Metrics for the endpoint.",
                "session.attribute",
            )
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def __repr__(self) -> str:
        return f"<AttributeEndpoint metrics={len(self.metrics)}; explicit observation inputs>"
