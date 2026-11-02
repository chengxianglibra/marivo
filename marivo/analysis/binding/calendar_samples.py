"""Validate static sample hierarchies and retain the contribution selection."""

from __future__ import annotations

from typing import NoReturn

from marivo._definition.graph import canonical_value, fingerprint
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import (
    Grain,
    PeriodCalendarSnapshotV2,
)
from marivo._timezones import normalize_timezone
from marivo.analysis.binding.windows import resolve_calendar_grain
from marivo.analysis.core.model import reject


def _builtin_nests(inner: Grain, outer: Grain) -> bool:
    widths = {"second": 1, "minute": 60, "hour": 3600}
    if inner.unit in widths:
        if outer.unit not in widths:
            return True
        assert inner.count is not None and outer.count is not None
        return (widths[outer.unit] * outer.count) % (widths[inner.unit] * inner.count) == 0
    return inner == outer or (inner.unit, outer.unit) in {
        ("day", "week"),
        ("day", "month"),
        ("day", "quarter"),
        ("day", "year"),
        ("month", "quarter"),
        ("month", "year"),
        ("quarter", "year"),
    }


def bind_calendar_samples(
    grain: Grain,
    parent: CalendarCoordinate | IntervalCoordinate | None,
    calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
) -> CalendarCoordinate:
    """Validate every layer without generating or completing internal samples."""
    if parent is None:
        reject(
            "a finite observation time coordinate for calendar samples",
            "None",
            'Pass by=(session.time_scope(start=..., end=...).by(mv.grain("week")), ...).',
            "Metric.per",
            canonical_id="session.observe",
        )
    grain = resolve_calendar_grain(grain, calendar_snapshots, location="Metric.per")
    received = grain._to_token()
    expected = "a supported static child-to-parent calendar grain combination"

    def invalid(reason: str) -> NoReturn:
        reject(
            expected,
            f"{received}: {reason}",
            'Choose m.per(mv.grain("day")) or a finer grain nested in the enclosing calendar; use grains from the same certified snapshot.',
            "Metric.per",
            canonical_id="session.observe",
        )

    if grain.kind != "builtin":
        if not isinstance(parent, IntervalCoordinate):
            invalid("custom calendar sample hierarchies have no static nesting rule")
        from marivo._temporal import calendar_axis, session_time_scope

        try:
            return calendar_axis(
                session_time_scope(parent.start, parent.end, parent.timezone), grain
            )._binding
        except ValueError as error:
            invalid(str(error))
    if isinstance(parent, CalendarCoordinate) and (
        parent.grain_spec.kind != "builtin" or not _builtin_nests(grain, parent.grain_spec)
    ):
        invalid(f"outer grain is {parent.grain}")
    # This is a contribution selection, not a completed inner sample grid.
    # Each source computes both parent and child keys from the same fact time.
    if isinstance(parent, CalendarCoordinate):
        cells, civil, ranges = parent.cells, parent.civil_bounds, parent.ranges
    else:
        cells = ((parent.start, parent.end, parent.start, parent.end),)
        ranges = ((parent.start, parent.end),)
        zone = normalize_timezone(parent.timezone).tz
        start = parent.start.astimezone(zone).isoformat(timespec="microseconds")
        end = parent.end.astimezone(zone).isoformat(timespec="microseconds")
        civil = ((start, end, start, end),)
    return CalendarCoordinate(
        fingerprint((parent.identity, canonical_value(grain), "calendar-samples/v2")),
        grain,
        parent.timezone,
        None,
        cells,
        civil_bounds=civil,
        ranges=ranges,
    )
