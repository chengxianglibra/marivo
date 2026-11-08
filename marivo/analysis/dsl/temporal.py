"""Exact observation boundaries and finite calendars, without source access."""

from __future__ import annotations

from datetime import date, datetime
from typing import TypeAlias

from marivo._temporal import (
    CalendarAxis as CalendarAxis,
)
from marivo._temporal import (
    InstantAnchor,
    TimeScope,
    anchor_from_datetime,
    session_time_scope,
)
from marivo._timezones import normalize_timezone
from marivo.analysis.core.model import reject
from marivo.analysis.core.time_grid import instant

TimeBoundary: TypeAlias = str | date | datetime


def exact_instant(
    value: TimeBoundary, timezone: str, *, location: str = "session.instant"
) -> InstantAnchor:
    """Resolve a boundary once, rejecting ambiguous clocks and naive datetimes."""
    original = value
    if not isinstance(timezone, str) or not timezone:
        reject(
            "a nonempty timezone name",
            repr(timezone),
            "Use None for the Session default or an explicit timezone string.",
            location,
        )
    try:
        timezone = normalize_timezone(timezone).name
    except (ValueError, KeyError, TypeError):
        reject(
            "an IANA or fixed-offset timezone",
            repr(timezone),
            "Choose an explicit valid boundary timezone.",
            location,
        )
    if isinstance(value, datetime) and value.utcoffset() is None:
        reject(
            "an aware datetime",
            repr(value),
            "Add an explicit timezone or pass a civil ISO string.",
            location,
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
                location,
            )
    if not isinstance(value, date):
        reject(
            "a date, aware datetime or ISO string",
            type(value).__name__,
            "Pass an explicit time boundary.",
            location,
        )
    from marivo.analysis.datasets.errors import DatasetConstructionError

    try:
        return anchor_from_datetime(instant(value, timezone))
    except DatasetConstructionError as error:
        if location == "session.instant":
            raise
        reject(
            "an unambiguous existing civil boundary or an aware instant",
            f"{original!r}: {error.received}",
            "Pass an aware datetime or an ISO timestamp with an explicit UTC offset.",
            location,
            canonical_id="session.instant",
        )


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
