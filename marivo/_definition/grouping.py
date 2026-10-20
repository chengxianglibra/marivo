"""Closed public grouping variants wrapping existing canonical definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._definition.relations import MembershipBinding
from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate
from marivo._grouping import Grouping


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
