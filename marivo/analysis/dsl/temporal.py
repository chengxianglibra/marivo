"""Exact observation boundaries and finite calendars, without source access."""

from __future__ import annotations

from marivo._definition.types import IntervalCoordinate
from marivo._kernel.temporal import (
    CalendarAxis as CalendarAxis,
)
from marivo._kernel.temporal import (
    TimeScope,
    anchor_from_datetime,
    session_time_scope,
)
from marivo._kernel.timezones import normalize_timezone
from marivo.analysis.model.errors import reject
from marivo.analysis.model.time_grid import TimeBoundary, exact_instant, instant


def exact_scope(start: TimeBoundary, end: TimeBoundary, timezone: str) -> TimeScope:
    """Freeze both endpoints and the timezone used for calendar partitioning."""
    left, right = exact_instant(start, timezone), exact_instant(end, timezone)
    if left >= right:
        reject(
            "start < end",
            f"{left.isoformat()} >= {right.isoformat()}",
            "Choose a nonempty half-open scope.",
            "session.time_scope",
        )
    return session_time_scope(left, right, normalize_timezone(timezone).name)


def interval_coordinate(scope: TimeScope) -> IntervalCoordinate:
    if not isinstance(scope, TimeScope):
        reject(
            "a TimeScope with a frozen timezone",
            repr(scope),
            "Construct the scope with session.time_scope(...).",
            "population.interval",
        )
    zone = scope.boundary_timezone
    start = anchor_from_datetime(instant(scope.start, zone))
    end = anchor_from_datetime(instant(scope.end, zone))
    return IntervalCoordinate(
        f"interval:{start.isoformat()}:{end.isoformat()}:{zone}",
        start,
        end,
        zone,
    )
