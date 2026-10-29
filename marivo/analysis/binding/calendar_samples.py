"""Freeze calendar sample cells from their nearest enclosing time coordinate."""

from __future__ import annotations

from typing import NoReturn

from marivo._definition.graph import canonical_value, fingerprint
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import (
    Grain,
    InstantAnchor,
    PeriodCalendarSnapshotV2,
    calendar_axis,
    session_time_scope,
)
from marivo._temporal_grid import CivilBounds
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
    """Retain every inner cell, clipping only actual bounds to its parent's cells."""
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
    expected = "an inner calendar grain that nests and tiles each enclosing cell"

    def invalid(reason: str) -> NoReturn:
        reject(
            expected,
            f"{received}: {reason}",
            'Choose m.per(mv.grain("day")) or a finer grain nested in the enclosing calendar; use grains from the same certified snapshot.',
            "Metric.per",
            canonical_id="session.observe",
        )

    if grain.kind == "semantic" and (
        grain._snapshot is None or grain._snapshot.boundary_timezone != parent.timezone
    ):
        invalid("missing certified snapshot or incompatible boundary timezone")
    if isinstance(parent, CalendarCoordinate):
        outer = parent.grain_spec
        if grain.kind == outer.kind == "builtin" and not _builtin_nests(grain, outer):
            invalid(f"outer grain is {parent.grain}")
        if grain.kind == outer.kind == "semantic":
            snapshot = grain._snapshot
            if snapshot is None or snapshot.snapshot_digest != parent.snapshot_digest:
                invalid("inner and outer calendar snapshots differ")
            assert snapshot is not None
            if grain.level != outer.level and not any(
                item.source_level == grain.level and item.target_level == outer.level
                for item in snapshot.containments
            ):
                invalid(f"snapshot has no {grain.level} to {outer.level} containment")
        parent_cells = parent.cells
    else:
        parent_cells = ((parent.start, parent.end, parent.start, parent.end),)
    cells: list[tuple[InstantAnchor, InstantAnchor, InstantAnchor, InstantAnchor]] = []
    civil: list[CivilBounds] = []
    for nominal_start, nominal_end, start, end in parent_cells:
        try:
            grid = calendar_axis(session_time_scope(start, end, parent.timezone), grain)._binding
            # Unlike actual edge clipping, nominal crossings are not nesting.
            if isinstance(parent, CalendarCoordinate) and (
                grid.cells[0][0] < nominal_start or grid.cells[-1][1] > nominal_end
            ):
                invalid(f"inner nominal bounds cross outer {parent.grain} bounds")
            if isinstance(parent, CalendarCoordinate) and grain.kind != parent.grain_spec.kind:
                nominal = calendar_axis(
                    session_time_scope(nominal_start, nominal_end, parent.timezone), grain
                )._binding
                if nominal.cells[0][0] != nominal_start or nominal.cells[-1][1] != nominal_end:
                    invalid("mixed calendar nominal boundaries do not tile")
        except ValueError as error:
            invalid(str(error))
        cells.extend(grid.cells)
        civil.extend(grid.civil_bounds)
    return CalendarCoordinate(
        fingerprint((parent.identity, canonical_value(grain), "calendar-samples/v1")),
        grain,
        parent.timezone,
        grain._snapshot.snapshot_digest if grain._snapshot is not None else None,
        tuple(cells),
        grain._snapshot,
        civil_bounds=tuple(civil),
    )
