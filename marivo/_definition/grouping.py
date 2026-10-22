"""Closed public grouping variants wrapping existing canonical definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._definition.correspondence import ComparisonAxis, coordinate_matches
from marivo._definition.relations import MembershipBinding, validate_memberships
from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate, invalid
from marivo._grouping import Grouping


def normalize_groupings(
    items: tuple[ComparisonAxis | MembershipBinding, ...],
    *,
    population_axes: tuple[ComparisonAxis, ...] | None = None,
) -> tuple[tuple[ComparisonAxis, ...], tuple[MembershipBinding, ...]]:
    """Expand rules after collecting every explicit field position in this layer."""
    bindings = tuple(item for item in items if isinstance(item, MembershipBinding))
    explicit = tuple(item for item in items if not isinstance(item, MembershipBinding))
    if population_axes is not None:
        if explicit or not bindings:
            invalid("a Population tuple requires only membership rules after its first item")
        validate_memberships(population_axes, bindings)
        return population_axes, bindings
    axes: list[ComparisonAxis] = []
    for item in items:
        if isinstance(item, MembershipBinding):
            for index, read in enumerate(item.fields):
                axis = AxisBinding(read.entity, read.field, read.path, "field", read.at)
                positioned = any(coordinate_matches(axis, candidate) for candidate in explicit)
                if positioned and index == 0:
                    invalid("a membership first field cannot also have an explicit position")
                if not positioned:
                    axes.append(axis)
        else:
            axes.append(item)
    if any(
        coordinate_matches(left, right)
        for index, left in enumerate(axes)
        for right in axes[index + 1 :]
    ):
        invalid("consuming coordinates must be unique; remove repeated fields or rules")
    if sum(isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) for axis in axes) > 1:
        invalid("consuming coordinates allow at most one output time axis")
    validate_memberships(tuple(axes), bindings)
    return tuple(axes), bindings


@dataclass(frozen=True, slots=True, repr=False)
class _CoordinateGrouping(Grouping):
    _value: AxisBinding | CalendarCoordinate | IntervalCoordinate

    @property
    def kind(self) -> Literal["field", "identity", "calendar", "interval"]:
        return (
            "calendar"
            if isinstance(self._value, CalendarCoordinate)
            else "interval"
            if isinstance(self._value, IntervalCoordinate)
            else self._value.role
        )

    @property
    def _binding(self) -> AxisBinding | CalendarCoordinate | IntervalCoordinate:
        return self._value


@dataclass(frozen=True, slots=True, repr=False)
class _MembershipGrouping(Grouping):
    _value: MembershipBinding

    @property
    def kind(self) -> Literal["membership"]:
        return "membership"

    @property
    def _binding(self) -> MembershipBinding:
        return self._value
