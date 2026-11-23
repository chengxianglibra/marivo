"""Closed public grouping variants wrapping existing canonical definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, overload

from typing_extensions import override

from marivo._definition.correspondence import ComparisonAxis, coordinate_matches
from marivo._definition.population_types import MembershipBinding
from marivo._definition.relations import validate_memberships
from marivo._definition.temporal_bridge import calendar_coordinate
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    IntervalCoordinate,
    field_axis,
)
from marivo._kernel.errors import invalid
from marivo._kernel.grouping import Grouping
from marivo._kernel.input_context import InputContext
from marivo._kernel.temporal import CalendarAxis


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
                axis = field_axis(read)
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
        invalid("consuming coordinates allow at most one direct observation time axis")
    validate_memberships(tuple(axes), bindings)
    return tuple(axes), bindings


class _DefinitionGrouping(Grouping):
    """A grouping whose definition coordinate is already captured."""

    __slots__ = ()

    @property
    def _binding(self) -> ComparisonAxis | MembershipBinding:
        raise NotImplementedError

    @override
    def __repr__(self) -> str:
        binding = self._binding
        if isinstance(binding, (CalendarCoordinate, IntervalCoordinate)):
            identity = binding.identity[:48]
        elif isinstance(binding, MembershipBinding):
            identity = f"fields={len(binding.fields)} paths={len(binding.paths)}"
        else:
            identity = (binding.field or binding.entity).ref.key[:96]
        return f"<Grouping kind={self.kind} id={identity}; use .show()>"

    @override
    def show(self) -> None:
        """Print bounded captured grouping facts.

        Args: None.
        Returns: None.
        Example: ``population.axes[0].show()``.
        Constraints: Reads frozen metadata only; never enumerates domain keys.
        """
        binding = self._binding
        print(repr(self))
        if isinstance(binding, CalendarCoordinate):
            print(f"grain={binding.grain} timezone={binding.timezone} cells={len(binding.cells)}")
        elif isinstance(binding, IntervalCoordinate):
            print(f"start={binding.start} end={binding.end} timezone={binding.timezone}")
        elif isinstance(binding, MembershipBinding):
            print(f"unmatched={binding.unmatched}")
            for read in binding.fields[:8]:
                print(read.field.ref.key[:200])
            for path in binding.paths[:8]:
                print("via=" + " -> ".join(step.ref.ref.key for step in path.relationships)[:400])
        else:
            print(f"path={tuple(step.ref.key for step in binding.path)!r}"[:600])
            print(f"at={binding.at}")


@dataclass(frozen=True, slots=True, repr=False)
class _CoordinateGrouping(_DefinitionGrouping):
    _value: AxisBinding | CalendarCoordinate | IntervalCoordinate
    _context: InputContext = field(default_factory=InputContext, compare=False)
    _identity_keys: tuple[tuple[str, tuple[str, ...]], ...] = field(default=(), compare=False)

    @property
    @override
    def kind(self) -> Literal["field", "identity", "calendar", "interval"]:
        return (
            "calendar"
            if isinstance(self._value, CalendarCoordinate)
            else "interval"
            if isinstance(self._value, IntervalCoordinate)
            else self._value.role
        )

    @property
    @override
    def _binding(self) -> AxisBinding | CalendarCoordinate | IntervalCoordinate:
        return self._value


@dataclass(frozen=True, slots=True, repr=False)
class _MembershipGrouping(_DefinitionGrouping):
    _value: MembershipBinding
    _context: InputContext = field(default_factory=InputContext, compare=False)
    _identity_keys: tuple[tuple[str, tuple[str, ...]], ...] = field(default=(), compare=False)

    @property
    @override
    def kind(self) -> Literal["membership"]:
        return "membership"

    @property
    @override
    def _binding(self) -> MembershipBinding:
        return self._value


@overload
def grouping_binding(value: CalendarAxis) -> CalendarCoordinate: ...


@overload
def grouping_binding(value: _MembershipGrouping) -> MembershipBinding: ...


@overload
def grouping_binding(value: Grouping) -> ComparisonAxis | MembershipBinding: ...


def grouping_binding(value: Grouping) -> ComparisonAxis | MembershipBinding:
    """Read the exact definition binding of a supported grouping."""
    if isinstance(value, CalendarAxis):
        return calendar_coordinate(value.grid)
    if isinstance(value, _DefinitionGrouping):
        return value._binding  # noqa: SLF001 - Closed definition binding of the grouping.
    invalid("Expected a captured Grouping from Marivo.")
