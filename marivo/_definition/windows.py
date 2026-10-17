"""Source-free window rules shared by declaration and observation consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeAlias

from marivo._definition.types import invalid
from marivo._temporal import Grain


class _Window:
    def __repr__(self) -> str:
        return f"<ObservationWindow kind={type(self).__name__}; use .show()>"

    def show(self) -> str:
        """Return this rule's source-free parameters; binding belongs to observe."""
        if isinstance(self, AllHistory):
            return "AllHistory: contributions before each endpoint, with no lower bound"
        if isinstance(self, TrailingDuration):
            return f"TrailingDuration: {self.duration}"
        if isinstance(self, TrailingPeriods):
            return f"TrailingPeriods: {self.count} periods"
        if isinstance(self, Since):
            return f"Since: {self.start.isoformat()}"
        assert isinstance(self, ToDate)
        return f"ToDate: {self.grain.to_token()}"


@dataclass(frozen=True, slots=True, repr=False)
class AllHistory(_Window):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class TrailingDuration(_Window):
    duration: timedelta


@dataclass(frozen=True, slots=True, repr=False)
class TrailingPeriods(_Window):
    count: int


@dataclass(frozen=True, slots=True, repr=False)
class Since(_Window):
    start: datetime


@dataclass(frozen=True, slots=True, repr=False)
class ToDate(_Window):
    grain: Grain


ObservationWindow: TypeAlias = AllHistory | TrailingDuration | TrailingPeriods | Since | ToDate


def all_history() -> ObservationWindow:
    """Use all available history before each observation endpoint.

    Returns: A source-free rule with no lower bound.
    Example: ``revenue.window(mv.all_history())``.
    Constraints: Source retention still limits available history.
    """
    return AllHistory()


def trailing_duration(*, duration: timedelta) -> ObservationWindow:
    """Use a fixed elapsed duration before each observation endpoint.

    Args: duration: A positive timedelta, measured in elapsed time.
    Returns: A source-free window rule.
    Example: ``revenue.window(mv.trailing_duration(duration=timedelta(hours=24)))``.
    Constraints: Civil day lengths may differ across timezone transitions.
    """
    if not isinstance(duration, timedelta) or duration <= timedelta(0):
        invalid(f"Expected a positive timedelta; received {duration!r}. Pass timedelta(hours=24).")
    return TrailingDuration(duration)


def trailing_periods(*, count: int) -> ObservationWindow:
    """Use the current calendar period and its count minus one predecessors.

    Args: count: A positive, non-boolean integer.
    Returns: A source-free calendar window rule.
    Example: ``revenue.window(mv.trailing_periods(count=7))``.
    Constraints: Observation requires a CalendarAxis.
    """
    if type(count) is not int or count <= 0:
        invalid(
            f"Expected a positive integer; received {count!r}. Pass count=7 with a CalendarAxis."
        )
    return TrailingPeriods(count)


def since(*, start: datetime) -> ObservationWindow:
    """Use contributions from an exact start through each observation endpoint.

    Args: start: An aware datetime or Session InstantAnchor.
    Returns: A source-free window rule retaining the exact UTC instant.
    Example: ``revenue.window(mv.since(start=session.instant('2026-01-01')))``.
    Constraints: Start must precede every consumed endpoint.
    """
    if not isinstance(start, datetime) or start.utcoffset() is None:
        invalid(
            f"Expected an aware datetime or InstantAnchor; received {start!r}. Pass session.instant(...)."
        )
    return Since(start.astimezone(timezone.utc))


def to_date(*, grain: Grain) -> ObservationWindow:
    """Reset at the start of the period ending at or containing each endpoint.

    Args: grain: A civil or certified semantic Grain.
    Returns: A source-free reset window rule.
    Example: ``revenue.window(mv.to_date(grain=mv.grain('month')))``.
    Constraints: Each output calendar bucket must lie in one reset period.
    """
    if not isinstance(grain, Grain):
        invalid(f"Expected a Grain; received {grain!r}. Pass grain=mv.grain('month').")
    return ToDate(grain)
