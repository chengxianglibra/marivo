"""Frozen finite time coordinates, independent of sources and host timezone.

This module resolves explicit temporal authority into durable UTC boundaries.
It does not grant source execution or original-state reduction qualification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, TimeScope
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


def instant(value: date | datetime, zone: str) -> datetime:
    """Resolve civil boundaries exactly; ambiguous or nonexistent wall clocks reject."""
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value.astimezone(timezone.utc)
    wall = value if isinstance(value, datetime) else datetime.combine(value, time())
    try:
        authority = parse_timezone(zone)[1]
    except (ValueError, KeyError) as error:
        raise _invalid(f"invalid boundary timezone {zone!r}") from error
    candidates = {
        local.astimezone(timezone.utc)
        for fold in (0, 1)
        if (local := wall.replace(tzinfo=authority, fold=fold))
        .astimezone(timezone.utc)
        .astimezone(authority)
        .replace(tzinfo=None)
        == wall.replace(tzinfo=None)
    }
    if len(candidates) != 1:
        raise _invalid(f"boundary {wall.isoformat()} has {len(candidates)} instants in {zone}")
    return next(iter(candidates))


@dataclass(frozen=True, slots=True)
class TimeCell:
    """One stable original period and its consumed half-open intersection."""

    identity: str
    original_start: datetime
    original_end: datetime
    start: datetime
    end: datetime
    partial: bool

    def __repr__(self) -> str:
        return repr(
            (
                "TimeCell",
                self.identity,
                self.original_start.isoformat(),
                self.original_end.isoformat(),
                self.start.isoformat(),
                self.end.isoformat(),
                self.partial,
            )
        )

    def __post_init__(self) -> None:
        bounds = (self.original_start, self.original_end, self.start, self.end)
        if (
            not self.identity
            or any(v.tzinfo is None or v.utcoffset() != timedelta(0) for v in bounds)
            or not self.original_start <= self.start < self.end <= self.original_end
            or self.partial != (self.start != self.original_start or self.end != self.original_end)
        ):
            raise _invalid("inconsistent or non-UTC frozen cell boundaries")


@dataclass(frozen=True, slots=True)
class BoundTimeGrid:
    """Complete, serializable temporal authority adopted by a consuming graph."""

    identity: str
    grain_token: str
    report_timezone: str
    boundary_timezone: str
    snapshot_digest: str | None
    scope_digest: str | None
    cells: tuple[TimeCell, ...]
    precision: Literal["us"] = "us"
    scope_identity: str | None = None
    calendar_snapshot: PeriodCalendarSnapshotV1 | None = None

    def __post_init__(self) -> None:
        if not self.identity or not self.grain_token or not self.cells or self.precision != "us":
            raise _invalid("missing grid identity, grain or cells")
        if (
            self.calendar_snapshot is not None
            and self.calendar_snapshot.snapshot_digest != self.snapshot_digest
        ):
            raise _invalid("captured calendar differs from grid snapshot digest")
        for zone in (self.report_timezone, self.boundary_timezone):
            try:
                parse_timezone(zone)
            except (ValueError, KeyError) as error:
                raise _invalid(f"invalid frozen timezone {zone!r}") from error
        if len({cell.identity for cell in self.cells}) != len(self.cells) or any(
            left.end != right.start for left, right in pairwise(self.cells)
        ):
            raise _invalid("grid cells are duplicated, unordered or not a partition")
        if self.identity != _identity(
            self.grain_token,
            self.report_timezone,
            self.boundary_timezone,
            self.snapshot_digest,
            self.scope_digest,
            self.cells,
            self.scope_identity,
        ):
            raise _invalid("grid identity differs from its frozen boundaries")


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


def _identity(
    grain_token: str,
    report: str,
    boundary: str,
    snapshot_digest: str | None,
    scope_digest: str | None,
    cells: tuple[TimeCell, ...],
    scope_identity: str | None = None,
) -> str:
    return sha256(
        repr(
            (grain_token, report, boundary, snapshot_digest, scope_digest, cells, scope_identity)
        ).encode()
    ).hexdigest()


def _floor(value: datetime, grain: Grain) -> datetime:
    unit, count = grain.unit, grain.count
    if unit is None or count is None:
        raise _invalid("builtin grain has no unit or count")
    if unit in ("second", "minute", "hour"):
        width = {"second": 1, "minute": 60, "hour": 3600}[unit] * count
        if 86400 % width:
            raise _invalid("subday grain width must divide the civil day")
        seconds = value.hour * 3600 + value.minute * 60 + value.second
        return value.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            seconds=seconds // width * width
        )
    if count != 1:
        raise _invalid("multi-unit civil calendar grains are not qualified")
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "day":
        return midnight
    if unit == "week":
        return midnight - timedelta(days=value.weekday())
    if unit == "month":
        return midnight.replace(day=1)
    if unit == "quarter":
        return midnight.replace(month=(value.month - 1) // 3 * 3 + 1, day=1)
    if unit == "year":
        return midnight.replace(month=1, day=1)
    raise _invalid(f"unsupported builtin grain {unit!r}")


def _next(value: datetime, grain: Grain) -> datetime:
    unit, count = grain.unit, grain.count
    if unit is None or count is None:
        raise _invalid("builtin grain has no unit or count")
    seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
    if unit in seconds:
        return value + timedelta(seconds=seconds[unit] * count)
    months = {"month": 1, "quarter": 3, "year": 12}[unit] * count
    ordinal = value.year * 12 + value.month - 1 + months
    return value.replace(year=ordinal // 12, month=ordinal % 12 + 1)


def bind_grid(
    during: TimeScope,
    grain: Grain,
    *,
    report_timezone: str,
    explicit_timezone: str | None = None,
    snapshot: PeriodCalendarSnapshotV1 | None = None,
) -> BoundTimeGrid:
    """Freeze a finite grid; no source, host clock or calendar store is consulted."""
    if not isinstance(during, TimeScope) or not isinstance(grain, Grain):
        raise _invalid("grid requires a TimeScope and Grain")
    scope_digest = None if during.kind == "absolute" else during.snapshot_digest
    if grain.kind == "semantic":
        if snapshot is None or snapshot.calendar_ref != grain.calendar:
            raise _invalid("missing exact certified calendar snapshot")
        boundary = snapshot.boundary_timezone
        if explicit_timezone is not None and explicit_timezone != boundary:
            raise _invalid("explicit calendar timezone conflicts with certification")
        if during.kind == "calendar_period" and (
            during.calendar != snapshot.calendar_ref or scope_digest != snapshot.snapshot_digest
        ):
            raise _invalid("scope and grain have different calendar snapshot identities")
    else:
        if snapshot is not None:
            raise _invalid("builtin grain cannot adopt a calendar snapshot")
        boundary = explicit_timezone or report_timezone
    scope_zone = report_timezone if during.kind == "absolute" else during.boundary_timezone
    start, end = instant(during.start, scope_zone), instant(during.end, scope_zone)
    if start >= end:
        raise _invalid("grid scope is empty or reversed")
    periods: list[tuple[datetime, datetime]] = []
    if snapshot is not None:
        if start < instant(snapshot.coverage[0], boundary) or end > instant(
            snapshot.coverage[1], boundary
        ):
            raise _invalid("grid extends beyond certified calendar coverage")
        if grain.level == "day":
            cursor_date = snapshot.coverage[0]
            while cursor_date < snapshot.coverage[1]:
                next_date = cursor_date + timedelta(days=1)
                periods.append((instant(cursor_date, boundary), instant(next_date, boundary)))
                cursor_date = next_date
        else:
            periods.extend(
                (instant(p.start_date, boundary), instant(p.end_date, boundary))
                for p in snapshot.periods
                if p.level_name == grain.level
            )
    else:
        authority = parse_timezone(boundary)[1]
        lookback = timedelta(0) if authority.utcoffset(None) is not None else timedelta(days=2)
        cursor = _floor((start - lookback).astimezone(authority).replace(tzinfo=None), grain)
        # Enumerate civil boundaries, retaining both instants of repeated subday
        # boundaries and skipping nonexistent ones. Source naive folds still reject.
        boundaries: set[datetime] = set()
        earliest: datetime | None = None
        while True:
            current: list[datetime] = []
            for fold in (0, 1):
                local = cursor.replace(tzinfo=authority, fold=fold)
                candidate = local.astimezone(timezone.utc)
                if candidate.astimezone(authority).replace(tzinfo=None) == cursor:
                    boundaries.add(candidate)
                    current.append(candidate)
                    earliest = candidate if earliest is None else min(earliest, candidate)
            if earliest is not None and earliest <= start and current and min(current) >= end:
                break
            cursor = _next(cursor, grain)
        ordered = sorted(boundaries)
        periods.extend(pairwise(ordered))
    cells = tuple(
        TimeCell(a.isoformat(), a, b, max(a, start), min(b, end), a < start or b > end)
        for a, b in sorted(periods)
        if a < end and b > start
    )
    if not cells or cells[0].start != start or cells[-1].end != end:
        raise _invalid("grid periods do not cover the requested scope")
    snapshot_digest = snapshot.snapshot_digest if snapshot is not None else None
    token = grain.to_token()
    scope_identity = (
        None if during.kind == "absolute" else sha256(repr(during._identity()).encode()).hexdigest()
    )
    return BoundTimeGrid(
        _identity(
            token, report_timezone, boundary, snapshot_digest, scope_digest, cells, scope_identity
        ),
        token,
        report_timezone,
        boundary,
        snapshot_digest,
        scope_digest,
        cells,
        scope_identity=scope_identity,
        calendar_snapshot=snapshot,
    )


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
