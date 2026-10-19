"""Axis-free correspondence rules and immutable endpoint coordinate projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

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


ComparisonAxis: TypeAlias = AxisBinding | CalendarCoordinate
