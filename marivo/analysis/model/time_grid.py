"""Analysis error adaptation for the shared temporal instant kernel."""

from __future__ import annotations

from datetime import date, datetime
from typing import TypeAlias

from marivo._kernel.temporal_grid import instant as _instant
from marivo._kernel.time_types import InstantAnchor, anchor_from_datetime
from marivo._kernel.timezones import normalize_timezone
from marivo.analysis.model.errors import DatasetConstructionError, reject


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="finite half-open time cells with an exact report timezone",
        received=received,
        repair="Use explicit non-empty bounds in the Session report timezone.",
        location="analysis.time_grid",
    )


def instant(value: date | datetime, zone: str) -> datetime:
    """Resolve a boundary through the shared pure kernel with Analysis repair."""
    try:
        return _instant(value, zone)
    except ValueError as error:
        raise _invalid(str(error)) from error


TimeBoundary: TypeAlias = str | date | datetime


def exact_instant(
    value: TimeBoundary, timezone: str, *, location: str = "session.instant"
) -> InstantAnchor:
    """Resolve a boundary once, rejecting ambiguous clocks and naive datetimes."""
    original = value
    if not isinstance(timezone, str) or not timezone:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
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
