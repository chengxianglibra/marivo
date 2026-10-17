"""Axis-free correspondence rules and immutable endpoint coordinate projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

from marivo._definition.deferred import RelationshipParameter
from marivo._definition.types import AxisBinding, CalendarCoordinate, invalid


@dataclass(frozen=True, slots=True, repr=False)
class CalendarCorrespondence:
    """One closed calendar mapping rule, without endpoint axes or source state."""

    kind: Literal["ordinal", "period_offset", "previous_year"]
    by: int = 0

    def __post_init__(self) -> None:
        if self.kind not in ("ordinal", "period_offset", "previous_year"):
            invalid("calendar correspondence requires ordinal, period_offset or previous_year")
        if type(self.by) is not int or (self.kind != "period_offset" and self.by != 0):
            invalid("only period_offset accepts a non-boolean integer offset")

    def __repr__(self) -> str:
        suffix = f" by={self.by}" if self.kind == "period_offset" else ""
        return f"<CalendarCorrespondence {self.kind}{suffix}; use .show()>"

    def show(self) -> str:
        """Return the finite rule; endpoint calendars bind at align/compare."""
        return repr(self)


@dataclass(frozen=True, slots=True, repr=False)
class DeclaredCorrespondence:
    """A directed declaration path between identity axes, without source reads."""

    via: tuple[RelationshipParameter, ...]

    def __post_init__(self) -> None:
        if (
            type(self.via) is not tuple
            or not self.via
            or any(not isinstance(step, RelationshipParameter) for step in self.via)
        ):
            invalid("declared correspondence requires a nonempty tuple of Relationships")
        object.__setattr__(
            self,
            "via",
            tuple(
                RelationshipParameter(step.ref, step.from_entity, step.to_entity, step.cardinality)
                for step in self.via
            ),
        )
        for left, right in zip(self.via, self.via[1:], strict=False):
            if left.to_entity.ref != right.from_entity.ref:
                invalid("declared correspondence requires a contiguous directed path")

    def __repr__(self) -> str:
        return f"<DeclaredCorrespondence steps={len(self.via)}; use .show()>"

    def show(self) -> str:
        """Return bounded declared path identities, without resolving sources."""
        return "\n".join((repr(self), *(step.ref.ref.key for step in self.via[:8])))


Correspondence: TypeAlias = CalendarCorrespondence | DeclaredCorrespondence


@dataclass(frozen=True, slots=True)
class AxisProjection:
    path: tuple[str, ...]
    axis: AxisBinding | CalendarCoordinate | CorrespondenceAxis


@dataclass(frozen=True, slots=True, repr=False)
class CorrespondenceAxis:
    """An actual paired coordinate with paths to its original endpoint axes."""

    identity: str
    endpoints: tuple[AxisProjection, ...]

    @property
    def projections(
        self,
    ) -> Mapping[tuple[str, ...], AxisBinding | CalendarCoordinate | CorrespondenceAxis]:
        values: dict[tuple[str, ...], ComparisonAxis] = {}
        for item in self.endpoints:
            values[item.path] = item.axis
            if isinstance(item.axis, CorrespondenceAxis):
                values.update(
                    ((*item.path, *path), axis) for path, axis in item.axis.projections.items()
                )
        return MappingProxyType(values)

    def __repr__(self) -> str:
        return f"<CorrespondenceAxis id={self.identity[:16]} projections={len(self.endpoints)}; use .show()>"

    def show(self) -> str:
        """Return bounded projection paths over the retained endpoint bindings."""
        return "\n".join((repr(self), *(".".join(item.path) for item in self.endpoints[:8])))


ComparisonAxis: TypeAlias = AxisBinding | CalendarCoordinate | CorrespondenceAxis
