"""Exact observation boundaries and finite calendars, without source access."""

from __future__ import annotations

from datetime import date, datetime
from typing import TypeAlias

from marivo._temporal import (
    CalendarAxis as CalendarAxis,
)
from marivo._temporal import (
    InstantAnchor,
    _SessionTimeScope,
    anchor_from_datetime,
    session_time_scope,
)
from marivo._timezones import parse_timezone
from marivo.analysis.core.model import reject
from marivo.analysis.core.time_grid import instant

TimeBoundary: TypeAlias = str | date | datetime


def exact_instant(value: TimeBoundary, timezone: str) -> InstantAnchor:
    """Resolve a boundary once, rejecting ambiguous clocks and naive datetimes."""
    original = value
    if not isinstance(timezone, str) or not timezone:
        reject(
            "a nonempty timezone name",
            repr(timezone),
            "Use None for the Session default or an explicit timezone string.",
            "session.instant",
        )
    try:
        timezone = parse_timezone(timezone)[0]
    except (ValueError, KeyError, TypeError):
        reject(
            "an IANA or fixed-offset timezone",
            repr(timezone),
            "Choose an explicit valid boundary timezone.",
            "session.instant",
        )
    if isinstance(value, datetime) and value.utcoffset() is None:
        reject(
            "an aware datetime",
            repr(value),
            "Add an explicit timezone or pass a civil ISO string.",
            "session.instant",
        )
    if isinstance(value, str):
        try:
            value = (
                date.fromisoformat(value)
                if len(value) == 10
                else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
        except ValueError:
            reject(
                "an ISO date or timestamp",
                repr(original),
                "Pass an ISO boundary, e.g. '2026-01-01'.",
                "session.instant",
            )
    if not isinstance(value, date):
        reject(
            "a date, aware datetime or ISO string",
            type(value).__name__,
            "Pass an explicit time boundary.",
            "session.instant",
        )
    return anchor_from_datetime(instant(value, timezone))


def exact_scope(start: TimeBoundary, end: TimeBoundary, timezone: str) -> _SessionTimeScope:
    """Freeze both endpoints and the timezone used for calendar partitioning."""
    left, right = exact_instant(start, timezone), exact_instant(end, timezone)
    if left >= right:
        reject(
            "start < end",
            f"{left.isoformat()} >= {right.isoformat()}",
            "Choose a nonempty half-open scope.",
            "session.time_scope",
        )
    return session_time_scope(left, right, parse_timezone(timezone)[0])
