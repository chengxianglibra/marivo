"""Dependency-neutral finite calendar kernel shared by semantic and analysis values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from itertools import pairwise
from typing import Literal

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, TimeScope
from marivo._timezones import parse_timezone


def _invalid(received: str) -> ValueError:
    return ValueError(
        "Expected finite half-open time cells with exact timezone and calendar authority; "
        f"received {received}. Bind the frozen timezone and the exact certified calendar "
        "and choose boundaries inside its coverage."
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


@lru_cache(maxsize=65536, typed=True)
def _cell_repr(
    identity: str,
    original_start: datetime,
    original_end: datetime,
    start: datetime,
    end: datetime,
    partial: bool,
) -> str:
    # Cells are immutable and UTC-only, so equal bounds always render the same
    # ISO text; grid identities and definition digests repr the same cells often.
    return repr(
        (
            "TimeCell",
            identity,
            original_start.isoformat(),
            original_end.isoformat(),
            start.isoformat(),
            end.isoformat(),
            partial,
        )
    )


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
        return _cell_repr(
            self.identity,
            self.original_start,
            self.original_end,
            self.start,
            self.end,
            self.partial,
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
