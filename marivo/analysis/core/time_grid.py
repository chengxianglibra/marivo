"""Frozen finite time coordinates, independent of sources and host timezone.

This module resolves explicit temporal authority into durable UTC boundaries.
It does not grant source execution or original-state reduction qualification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from marivo import _temporal_grid as _grid
from marivo._temporal import Grain, PeriodCalendarSnapshotV1, TimeScope
from marivo._temporal_grid import BoundTimeGrid as BoundTimeGrid
from marivo._temporal_grid import TimeCell as TimeCell
from marivo._temporal_grid import _floor as _floor
from marivo._temporal_grid import _identity as _identity
from marivo._temporal_grid import _next as _next
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.datasource.timezone import parse_timezone
from marivo.semantic.ir import TargetSnapshotSelection, TargetValiditySelection

if TYPE_CHECKING:
    from marivo.semantic.metric_graph import CumulativeAnchorV1


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="finite half-open time cells with exact timezone and calendar authority",
        received=received,
        repair="Bind the Session report timezone and the exact certified calendar; choose wholly contained cells.",
        location="analysis.time_grid",
    )


@dataclass(frozen=True, slots=True)
class GridPoint:
    """An exact endpoint interpretation on an already bound grid."""

    grid: BoundTimeGrid
    side: Literal["start", "end", "before_end"]


@dataclass(frozen=True, slots=True)
class GridVersionSelection:
    """Ordered independent attribute-version selections for one exact product."""

    grid_identity: str
    selections: tuple[tuple[str, TargetSnapshotSelection | TargetValiditySelection], ...]

    def __post_init__(self) -> None:
        if (
            not self.grid_identity
            or not self.selections
            or len({k for k, _ in self.selections}) != len(self.selections)
        ):
            raise _invalid("missing or duplicate grid version selections")


def instant(value: date | datetime, zone: str) -> datetime:
    """Resolve a boundary through the shared pure kernel with Analysis repair."""
    try:
        return _grid.instant(value, zone)
    except ValueError as error:
        raise _invalid(str(error)) from error


def bind_grid(
    during: TimeScope,
    grain: Grain,
    *,
    report_timezone: str,
    explicit_timezone: str | None = None,
    snapshot: PeriodCalendarSnapshotV1 | None = None,
) -> BoundTimeGrid:
    """Bind the shared finite grid while preserving the existing Analysis errors."""
    try:
        return _grid.bind_grid(
            during,
            grain,
            report_timezone=report_timezone,
            explicit_timezone=explicit_timezone,
            snapshot=snapshot,
        )
    except ValueError as error:
        raise _invalid(str(error)) from error


def continuation(source: BoundTimeGrid, count: int) -> BoundTimeGrid:
    """Capture approved future periods from frozen civil or certified authority."""
    import re

    from marivo._temporal import builtin_grain, semantic_grain, time_scope
    from marivo.refs import ref

    start = source.cells[-1].end
    if source.snapshot_digest is not None:
        snapshot = source.calendar_snapshot
        if snapshot is None or snapshot.snapshot_digest != source.snapshot_digest:
            raise _invalid("forecast requires the captured certified calendar snapshot")
        path, level = source.grain_token.split("::", 1)
        grain = semantic_grain(calendar=ref.period_calendar(path), level=level)
        if level == "day":
            cursor = start.astimezone(parse_timezone(source.boundary_timezone)[1]).replace(
                tzinfo=None
            )
            end = instant(cursor + timedelta(days=count), source.boundary_timezone)
        else:
            periods = tuple(
                p
                for p in snapshot.periods
                if p.level_name == level
                and instant(p.start_date, source.boundary_timezone) >= start
            )
            if (
                len(periods) < count
                or instant(periods[0].start_date, source.boundary_timezone) != start
            ):
                raise _invalid("forecast future periods exceed captured certified coverage")
            end = instant(periods[count - 1].end_date, source.boundary_timezone)
        result = bind_grid(
            time_scope(start=start, end=end),
            grain,
            report_timezone=source.report_timezone,
            explicit_timezone=source.boundary_timezone,
            snapshot=snapshot,
        )
    else:
        match = re.fullmatch(
            r"(\d*)(second|minute|hour|day|week|month|quarter|year)", source.grain_token
        )
        if match is None:
            raise _invalid("forecast builtin grain token is unavailable")
        grain = builtin_grain(match[2], count=int(match[1] or "1"))
        authority = parse_timezone(source.boundary_timezone)[1]
        cursor = start.astimezone(authority).replace(tzinfo=None)
        needed = count
        while True:
            # This endpoint only bounds enumeration; it is not a user civil boundary.
            # Round-trip through UTC so gaps/folds cannot reject an unused lookahead.
            for _ in range(needed + 2):
                cursor = _next(cursor, grain)
            end = max(
                cursor.replace(tzinfo=authority, fold=fold).astimezone(timezone.utc)
                for fold in (0, 1)
            )
            extended = bind_grid(
                time_scope(start=start, end=end),
                grain,
                report_timezone=source.report_timezone,
                explicit_timezone=source.boundary_timezone,
            )
            cells = extended.cells[:count]
            needed = count - len(cells)
            if not needed:
                break
        result = replace_grid_cells(extended, cells)
    if (
        len(result.cells) != count
        or any(c.partial for c in result.cells)
        or result.cells[0].start != start
    ):
        raise _invalid("forecast future periods are incomplete or nonadjacent")
    return result


def replace_grid_cells(grid: BoundTimeGrid, cells: tuple[TimeCell, ...]) -> BoundTimeGrid:
    from dataclasses import replace

    return replace(
        grid,
        cells=cells,
        identity=_identity(
            grid.grain_token,
            grid.report_timezone,
            grid.boundary_timezone,
            grid.snapshot_digest,
            grid.scope_digest,
            cells,
            grid.scope_identity,
        ),
    )


def coarsening(source: BoundTimeGrid, target: BoundTimeGrid) -> tuple[tuple[str, str], ...]:
    """Map whole original cells only; clipping cannot make a crossing week a month."""
    if (source.report_timezone, source.boundary_timezone, source.snapshot_digest) != (
        target.report_timezone,
        target.boundary_timezone,
        target.snapshot_digest,
    ):
        raise _invalid("coarsening changes timezone or certification authority")
    if target.scope_identity is not None and source.scope_identity != target.scope_identity:
        raise _invalid("distinct certified occurrences cannot form one temporal partition")
    pairs: list[tuple[str, str]] = []
    for cell in source.cells:
        matches = [
            parent
            for parent in target.cells
            if parent.original_start <= cell.original_start
            and cell.original_end <= parent.original_end
            and parent.start <= cell.start
            and cell.end <= parent.end
        ]
        if len(matches) != 1:
            raise _invalid("source cell crosses a target boundary or lacks exact coverage")
        pairs.append((cell.identity, matches[0].identity))
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class EndpointWindow:
    """One retained half-open cumulative input interval, independent of display bounds."""

    key: str
    start: str | None
    end: str

    def __post_init__(self) -> None:
        end = datetime.fromisoformat(self.end)
        start = None if self.start is None else datetime.fromisoformat(self.start)
        if end.utcoffset() != timedelta(0) or (
            start is not None and (start.utcoffset() != timedelta(0) or start >= end)
        ):
            raise _invalid("invalid cumulative input interval")


@dataclass(frozen=True, slots=True)
class CumulativeBinding:
    """Frozen endpoint intervals and reset authority for one canonical occurrence."""

    anchor_token: str
    report_timezone: str
    boundary_timezone: str
    snapshot_digest: str | None
    grid_identity: str | None
    windows: tuple[EndpointWindow, ...]

    def __post_init__(self) -> None:
        if (
            not self.anchor_token
            or not self.windows
            or len({w.key for w in self.windows}) != len(self.windows)
        ):
            raise _invalid("missing or duplicate cumulative endpoints")
        if any(a.end >= b.end for a, b in pairwise(self.windows)):
            raise _invalid("cumulative endpoints must be strictly ordered")
        for zone in (self.report_timezone, self.boundary_timezone):
            parse_timezone(zone)

    @property
    def overlapping(self) -> bool:
        return any(b.start is None or a.end > b.start for a, b in pairwise(self.windows))


def bind_cumulative(
    anchor: CumulativeAnchorV1,
    at: datetime | GridPoint,
    report_timezone: str,
    snapshot: PeriodCalendarSnapshotV1 | None = None,
) -> CumulativeBinding:
    """Resolve declared reset intervals without using the display start as history."""
    from marivo._temporal import builtin_grain

    points = (
        tuple(
            (cell.identity, cell.start if at.side == "start" else cell.end)
            for cell in at.grid.cells
        )
        if isinstance(at, GridPoint)
        else (("endpoint", at),)
    )
    boundary = snapshot.boundary_timezone if snapshot else report_timezone
    zone = parse_timezone(boundary)[1]
    windows: list[EndpointWindow] = []
    for key, point in points:
        if point.tzinfo is None:
            raise _invalid("cumulative endpoint must be an aware instant")
        end = point.astimezone(timezone.utc)
        start: datetime | None = None
        if anchor != "all_history":
            if anchor[0] == "trailing":
                start = end - timedelta(
                    seconds=anchor[1]
                    * {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}[
                        anchor[2]
                    ]
                )
            else:
                grain = builtin_grain(anchor[1]) if isinstance(anchor[1], str) else anchor[1]
                if grain.kind == "semantic":
                    if snapshot is None or snapshot.calendar_ref != grain.calendar:
                        raise _invalid("cumulative reset requires its exact certified calendar")
                    candidates = [
                        (instant(p.start_date, boundary), instant(p.end_date, boundary))
                        for p in snapshot.periods
                        if p.level_name == grain.level
                    ]
                    if grain.level == "day":
                        candidates = [
                            (
                                instant(snapshot.coverage[0] + timedelta(days=i), boundary),
                                instant(snapshot.coverage[0] + timedelta(days=i + 1), boundary),
                            )
                            for i in range((snapshot.coverage[1] - snapshot.coverage[0]).days)
                        ]
                    matches = [left for left, right in candidates if left < end <= right]
                    if len(matches) != 1:
                        raise _invalid("cumulative endpoint is outside certified reset coverage")
                    start = matches[0]
                else:
                    local = end.astimezone(zone)
                    floor = _floor(local, grain)
                    if floor.astimezone(timezone.utc) == end:
                        unit, count = grain.unit, grain.count
                        assert unit is not None and count is not None
                        widths = {
                            "second": 1,
                            "minute": 60,
                            "hour": 3600,
                            "day": 86400,
                            "week": 604800,
                        }
                        if unit in widths:
                            floor -= timedelta(seconds=widths[unit] * count)
                        else:
                            ordinal = (
                                floor.year * 12
                                + floor.month
                                - 1
                                - {"month": 1, "quarter": 3, "year": 12}[unit] * count
                            )
                            floor = floor.replace(year=ordinal // 12, month=ordinal % 12 + 1)
                    start = instant(floor, boundary)
        windows.append(
            EndpointWindow(key, None if start is None else start.isoformat(), end.isoformat())
        )
    return CumulativeBinding(
        repr(anchor),
        report_timezone,
        boundary,
        None if snapshot is None else snapshot.snapshot_digest,
        at.grid.identity if isinstance(at, GridPoint) else None,
        tuple(windows),
    )
