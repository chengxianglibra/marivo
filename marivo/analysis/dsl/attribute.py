"""Immutable explicit endpoint inputs and Session-owned investigation capture."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ibis.common.deferred import Deferred

from marivo._grouping import Grouping
from marivo._temporal import TimeScope
from marivo.analysis.dsl.temporal import CalendarAxis


@dataclass(frozen=True, slots=True, repr=False, init=False)
class AttributeEndpoint:
    """Explicit observation inputs returned by ``mv.attribute_endpoint(...)``.

    Example: ``mv.attribute_endpoint({'revenue': revenue}, during=period)``.
    Constraints: Session.attribute validates ownership and binding; the record
    does not retain an executable Session or a historical Frame producer.
    """

    metrics: Mapping[str, Deferred]
    during: TimeScope | CalendarAxis | None = None
    where: Deferred | None = None
    memberships: tuple[Grouping, ...] = ()

    def __init__(self, *args: object, **kwargs: object) -> None:
        from marivo.analysis.core.model import reject

        reject(
            "an endpoint returned by mv.attribute_endpoint(...)",
            "direct AttributeEndpoint construction",
            "Use mv.attribute_endpoint(metrics, during, where, memberships).",
            "attribute_endpoint.define",
        )

    def __post_init__(self) -> None:
        from marivo.analysis.core.model import reject

        if not isinstance(self.metrics, Mapping) or not self.metrics:
            reject(
                "a nonempty named Metric mapping",
                type(self.metrics).__name__,
                "Use mv.attribute_endpoint with resolved named Metrics for the endpoint.",
                "attribute_endpoint.define",
            )
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def __repr__(self) -> str:
        return f"<AttributeEndpoint metrics={len(self.metrics)}; explicit observation inputs>"


def attribute_endpoint(
    metrics: Mapping[str, Deferred],
    during: TimeScope | CalendarAxis | None = None,
    where: Deferred | None = None,
    memberships: tuple[Grouping, ...] = (),
) -> AttributeEndpoint:
    """Capture explicit observation inputs without reading sources or creating a Run.

    Args:
        metrics: Nonempty ordered mapping of named resolved Metrics, copied here.
        during: Explicit endpoint TimeScope or CalendarAxis, or None.
        where: Native Deferred endpoint predicate, or None.
        memberships: Explicit Grouping allocation rules for existing coordinates.

    Returns:
        An immutable AttributeEndpoint consumed by ``session.attribute``.

    Example:
        ``mv.attribute_endpoint({'revenue': revenue}, during=period)``.

    Constraints:
        Session.attribute validates Metric eligibility, ownership and binding.
        Capture does not open a Session, load a Catalog or evaluate business rows.
    """
    endpoint = object.__new__(AttributeEndpoint)
    object.__setattr__(endpoint, "metrics", metrics)
    object.__setattr__(endpoint, "during", during)
    object.__setattr__(endpoint, "where", where)
    object.__setattr__(endpoint, "memberships", memberships)
    endpoint.__post_init__()
    return endpoint
