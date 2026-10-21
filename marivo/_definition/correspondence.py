"""Axis-free correspondence rules and immutable endpoint coordinate projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate, invalid


@dataclass(frozen=True, slots=True, repr=False)
class CalendarCorrespondence:
    """One closed calendar mapping rule, without endpoint axes or source state.

    Acquire with mv.ordinal() or mv.offset(years=...)/mv.offset(periods=...).
    Direct kind/by construction and subclass extension are unsupported.
    """

    kind: Literal["ordinal", "years", "periods"]
    by: int = 0

    def __post_init__(self) -> None:
        if self.kind not in ("ordinal", "years", "periods"):
            invalid("calendar correspondence requires ordinal, years or periods")
        if type(self.by) is not int or (self.kind == "ordinal" and self.by != 0):
            invalid("offset requires a non-boolean integer; ordinal has no offset")

    def __repr__(self) -> str:
        suffix = f" by={self.by}" if self.kind != "ordinal" else ""
        return f"<CalendarCorrespondence {self.kind}{suffix}; use .show()>"

    def show(self) -> str:
        """Return the finite rule; endpoint calendars bind at shift/align/compare."""
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
