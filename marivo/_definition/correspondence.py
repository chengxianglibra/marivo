"""Axis-free correspondence rules and immutable endpoint coordinate projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate, invalid


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


ComparisonAxis: TypeAlias = AxisBinding | CalendarCoordinate | IntervalCoordinate


def coordinate_matches(left: ComparisonAxis, right: ComparisonAxis) -> bool:
    """Match business coordinates without treating definition versions as identity."""
    if isinstance(left, AxisBinding) and isinstance(right, AxisBinding):
        return (
            left.role == right.role
            and left.entity.ref == right.entity.ref
            and (left.field.ref if left.field is not None else None)
            == (right.field.ref if right.field is not None else None)
            and tuple(step.ref for step in left.path) == tuple(step.ref for step in right.path)
            and left.at == right.at
        )
    return left == right


def coordinate_position(axes: tuple[ComparisonAxis, ...], axis: ComparisonAxis) -> int:
    """Locate one already validated business coordinate in its captured order."""
    return next(
        index for index, candidate in enumerate(axes) if coordinate_matches(candidate, axis)
    )
