"""Dependency-neutral finite calendar kernel shared by semantic and analysis values."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from itertools import pairwise
from typing import Literal, TypeAlias

from marivo._temporal import Grain, PeriodCalendarSnapshotV2, TimeScope
from marivo._timezones import parse_timezone

CivilBounds: TypeAlias = tuple[str, str, str, str]
InstantBounds: TypeAlias = tuple[datetime, datetime, datetime, datetime]


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
    grain_spec: Grain
    report_timezone: str
    boundary_timezone: str
    snapshot_digest: str | None
    scope_digest: str | None
    cells: tuple[TimeCell, ...]
    civil_bounds: tuple[CivilBounds, ...] = field(kw_only=True)
    precision: Literal["us"] = "us"
    scope_identity: str | None = None
    calendar_snapshot: PeriodCalendarSnapshotV2 | None = None
    origin: TimeGridOrigin | None = None

    @property
    def grain_token(self) -> str:
        return self.grain_spec._to_token()

    def __post_init__(self) -> None:
        if not self.identity or not self.grain_token or self.precision != "us":
            raise _invalid("missing grid identity or grain")
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
            left.original_end > right.original_start for left, right in pairwise(self.cells)
        ):
            raise _invalid("grid cells are duplicated, unordered or overlapping")
        if self.identity != _identity(
            self.grain_token,
            self.report_timezone,
            self.boundary_timezone,
            self.snapshot_digest,
            self.scope_digest,
            self.cells,
            self.scope_identity,
            self.origin,
            civil_bounds=self.civil_bounds,
        ):
            raise _invalid("grid identity differs from its frozen boundaries")
        if self.origin is not None:
            if type(self.origin) is not TimeGridOrigin:
                raise _invalid("unknown direct origin descriptor")
            self.origin.validate(self)
        if len(self.civil_bounds) != len(self.cells):
            raise _invalid("civil boundary count differs from the exact selected domain")
        for cell, civil in zip(self.cells, self.civil_bounds, strict=True):
            validate_civil_bounds(
                civil, (cell.original_start, cell.original_end, cell.start, cell.end)
            )


@dataclass(frozen=True, slots=True)
class TimeShiftMatch:
    """One origin bucket and its unique selected target bucket."""

    origin_identity: str
    target_identity: str


@dataclass(frozen=True, slots=True)
class TimeShiftAbsent:
    """A legal missing civil period or cutoff, with no fabricated target."""

    origin_identity: str
    reason: Literal["period_absent", "cutoff_absent"]


TimeShiftEntry: TypeAlias = TimeShiftMatch | TimeShiftAbsent


@dataclass(frozen=True, slots=True)
class TimeGridOrigin:
    """Finite direct shift facts; the origin never retains an ancestor relation."""

    grid: BoundTimeGrid
    kind: Literal["years", "periods"]
    by: int
    entries: tuple[TimeShiftEntry, ...]

    def validate(self, target: BoundTimeGrid) -> None:
        if (
            self.kind not in ("years", "periods")
            or type(self.by) is not int
            or self.grid.origin is not None
            or (self.grid.grain_spec, self.grid.boundary_timezone)
            != (target.grain_spec, target.boundary_timezone)
            or type(self.entries) is not tuple
            or any(type(entry) not in (TimeShiftMatch, TimeShiftAbsent) for entry in self.entries)
            or tuple(entry.origin_identity for entry in self.entries)
            != tuple(cell.identity for cell in self.grid.cells)
            or any(
                isinstance(entry, TimeShiftAbsent)
                and entry.reason not in ("period_absent", "cutoff_absent")
                for entry in self.entries
            )
        ):
            raise _invalid("invalid direct origin domain, rule or no-match records")
        targets = tuple(
            entry.target_identity for entry in self.entries if isinstance(entry, TimeShiftMatch)
        )
        if len(set(targets)) != len(targets) or set(targets) != {
            cell.identity for cell in target.cells
        }:
            raise _invalid("direct targets must cover the exact unique selected domain")


def _identity(
    grain_token: str,
    report: str,
    boundary: str,
    snapshot_digest: str | None,
    scope_digest: str | None,
    cells: tuple[TimeCell, ...],
    scope_identity: str | None = None,
    origin: TimeGridOrigin | None = None,
    *,
    civil_bounds: tuple[CivilBounds, ...],
) -> str:
    domain = (
        grain_token,
        report,
        boundary,
        snapshot_digest,
        scope_digest,
        cells,
        scope_identity,
        civil_bounds,
    )
    return sha256(repr(domain if origin is None else (*domain, origin)).encode()).hexdigest()


def validate_civil_bounds(civil: CivilBounds, bounds: InstantBounds) -> None:
    """Validate captured civil text against exact UTC bounds without resolving a zone."""
    if type(civil) is not tuple or len(civil) != 4:
        raise _invalid("four captured civil boundary strings are required")
    for text, bound in zip(civil, bounds, strict=True):
        try:
            stamp = datetime.fromisoformat(text)
            if (
                stamp.utcoffset() is None
                or stamp.isoformat(timespec="microseconds") != text
                or stamp.astimezone(timezone.utc) != bound
            ):
                raise ValueError("inconsistent civil boundary")
        except (TypeError, ValueError, OverflowError) as error:
            raise _invalid(
                "canonical offset-aware civil boundaries matching UTC instants"
            ) from error


def capture_civil_bounds(bounds: InstantBounds, zone: str) -> CivilBounds:
    """Capture civil text once when constructing a temporal domain."""
    authority = parse_timezone(zone)[1]

    def one(value: datetime) -> str:
        return value.astimezone(authority).isoformat(timespec="microseconds")

    return (one(bounds[0]), one(bounds[1]), one(bounds[2]), one(bounds[3]))


@dataclass(frozen=True, slots=True)
class CivilShiftTarget:
    """Intended local boundary roles; zero offsets additionally fix the exact instants."""

    bounds: InstantBounds
    exact_instants: InstantBounds | None = None


@dataclass(frozen=True, slots=True)
class CivilShiftAbsent:
    reason: Literal["period_absent", "cutoff_absent"]


CivilShiftResult: TypeAlias = CivilShiftTarget | CivilShiftAbsent


def _year(value: int) -> int:
    if not 1 <= value <= 9999:
        raise _invalid("target civil year is outside the representable range 1..9999")
    return value


def _civil_period_shift(
    value: datetime, unit: str, step: int, kind: Literal["years", "periods"], by: int
) -> datetime | None:
    if kind == "years":
        target_year = _year(value.isocalendar().year + by if unit == "week" else value.year + by)
        try:
            if unit == "week":
                target = date.fromisocalendar(
                    target_year, value.isocalendar().week, value.isoweekday()
                )
                return datetime.combine(target, value.time())
            return value.replace(year=target_year)
        except ValueError:
            return None  # A validated target year lacks this ISO week or civil day.
    count = step * by
    if unit in ("day", "week"):
        try:
            return value + timedelta(days=count * (7 if unit == "week" else 1))
        except (ValueError, OverflowError) as error:
            raise _invalid("target civil period is outside the representable range") from error
    months = count * {"month": 1, "quarter": 3, "year": 12}[unit]
    year, month = divmod(value.year * 12 + value.month - 1 + months, 12)
    _year(year)
    try:
        return value.replace(year=year, month=month + 1)
    except ValueError:
        return None


def civil_shift(
    unit: str, step: int, kind: Literal["years", "periods"], by: int, civil: CivilBounds
) -> CivilShiftResult:
    """Map finite full/prefix civil positions; no timezone or selected target lookup."""
    if (
        unit not in ("day", "week", "month", "quarter", "year")
        or type(step) is not int
        or step < 1
        or type(by) is not int
    ):
        raise _invalid("unsupported civil offset grain, width or integer")
    parsed = (
        datetime.fromisoformat(civil[0]),
        datetime.fromisoformat(civil[1]),
        datetime.fromisoformat(civil[2]),
        datetime.fromisoformat(civil[3]),
    )
    original_start, original_end, start, end = tuple(value.replace(tzinfo=None) for value in parsed)
    full = (start, end) == (original_start, original_end)
    if start != original_start or (
        not full and (unit not in ("week", "month", "year") or step != 1)
    ):
        raise _invalid(
            "offset requires complete buckets or unit week/month/year bucket-start prefixes"
        )
    if by == 0:
        return CivilShiftTarget(
            (original_start, original_end, start, end),
            (
                parsed[0].astimezone(timezone.utc),
                parsed[1].astimezone(timezone.utc),
                parsed[2].astimezone(timezone.utc),
                parsed[3].astimezone(timezone.utc),
            ),
        )
    target_start = _civil_period_shift(original_start, unit, step, kind, by)
    if target_start is None:
        return CivilShiftAbsent("period_absent")
    target_end = _civil_period_shift(target_start, unit, step, "periods", 1)
    if target_end is None:
        raise _invalid("target nominal endpoint is not representable")
    if full:
        return CivilShiftTarget((target_start, target_end, target_start, target_end))
    try:
        if unit == "week":
            cutoff = target_start + timedelta(days=(end.date() - original_start.date()).days)
            cutoff = cutoff.replace(
                hour=end.hour, minute=end.minute, second=end.second, microsecond=end.microsecond
            )
        elif unit == "month":
            cutoff = end.replace(year=target_start.year, month=target_start.month)
        else:
            cutoff = end.replace(year=target_start.year)
    except ValueError:
        return CivilShiftAbsent("cutoff_absent")
    if not target_start < cutoff < target_end:
        raise _invalid("mapped prefix cutoff is outside its target nominal period")
    return CivilShiftTarget((target_start, target_end, target_start, cutoff))


def shift_grid(grid: BoundTimeGrid, kind: Literal["years", "periods"], by: int) -> BoundTimeGrid:
    """Construct one exact selected domain with only its stripped immediate origin."""
    grain = grid.grain_spec
    if grain.kind != "builtin" or grain.unit not in ("week", "month", "year") or grain.count != 1:
        raise _invalid("shift requires builtin unit-step week, month or year")
    cells: list[TimeCell] = []
    civil_bounds: list[CivilBounds] = []
    entries: list[TimeShiftEntry] = []
    for cell, civil in zip(grid.cells, grid.civil_bounds, strict=True):
        result = civil_shift(grain.unit, 1, kind, by, civil)
        if isinstance(result, CivilShiftAbsent):
            entries.append(TimeShiftAbsent(cell.identity, result.reason))
            continue
        if by == 0:
            target, target_civil = cell, civil
        else:
            bounds = (
                instant(result.bounds[0], grid.boundary_timezone),
                instant(result.bounds[1], grid.boundary_timezone),
                instant(result.bounds[2], grid.boundary_timezone),
                instant(result.bounds[3], grid.boundary_timezone),
            )
            target = TimeCell(
                bounds[0].isoformat(), *bounds, bounds[0] != bounds[2] or bounds[1] != bounds[3]
            )
            target_civil = capture_civil_bounds(bounds, grid.boundary_timezone)
        cells.append(target)
        civil_bounds.append(target_civil)
        entries.append(TimeShiftMatch(cell.identity, target.identity))
    stripped = replace(
        grid,
        origin=None,
        identity=_identity(
            grid.grain_token,
            grid.report_timezone,
            grid.boundary_timezone,
            grid.snapshot_digest,
            grid.scope_digest,
            grid.cells,
            grid.scope_identity,
            civil_bounds=grid.civil_bounds,
        ),
    )
    origin = TimeGridOrigin(stripped, kind, by, tuple(entries))
    selected, frozen_civil = tuple(cells), tuple(civil_bounds)
    return BoundTimeGrid(
        _identity(
            grid.grain_token,
            grid.report_timezone,
            grid.boundary_timezone,
            grid.snapshot_digest,
            None,
            selected,
            origin=origin,
            civil_bounds=frozen_civil,
        ),
        grain,
        grid.report_timezone,
        grid.boundary_timezone,
        grid.snapshot_digest,
        None,
        selected,
        calendar_snapshot=grid.calendar_snapshot,
        origin=origin,
        civil_bounds=frozen_civil,
    )


def builtin_nominal_ordinal(unit: str, step: int, start: datetime) -> int:
    """Return a stable nominal-start rank, independent of a finite selection."""
    if unit in ("second", "minute", "hour"):
        utc = start.astimezone(timezone.utc)
        seconds = (
            (utc.date().toordinal() - 1) * 86400 + utc.hour * 3600 + utc.minute * 60 + utc.second
        )
        # Local subday buckets can start less than one configured grain width
        # apart during a fold. Preserve their exact instant ordering rather than
        # dividing both starts into the same elapsed-time interval.
        return seconds * 1_000_000 + utc.microsecond
    if unit == "day":
        return (start.date().toordinal() - 1) // step
    if unit == "week":
        return (start.date().toordinal() - 1) // (7 * step)
    months = (start.year - 1) * 12 + start.month - 1
    return months // ({"month": 1, "quarter": 3, "year": 12}[unit] * step)


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
    snapshot: PeriodCalendarSnapshotV2 | None = None,
) -> BoundTimeGrid:
    """Freeze a finite grid; no source, host clock or calendar store is consulted."""
    if not isinstance(during, TimeScope) or not isinstance(grain, Grain):
        raise _invalid("grid requires a TimeScope and Grain")
    scope_digest = None if during.kind == "absolute" else during.snapshot_digest
    if grain.kind == "semantic":
        if snapshot is None or snapshot.calendar_ref != grain.calendar:
            raise _invalid("missing exact certified calendar snapshot")
        if (
            grain._snapshot is not None
            and grain._snapshot.snapshot_digest != snapshot.snapshot_digest
        ):
            raise _invalid("grain and supplied calendar have different snapshot identities")
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
        boundary = explicit_timezone or during.boundary_timezone
    scope_zone = during.boundary_timezone
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
    civil_bounds = tuple(
        capture_civil_bounds(
            (cell.original_start, cell.original_end, cell.start, cell.end), boundary
        )
        for cell in cells
    )
    snapshot_digest = snapshot.snapshot_digest if snapshot is not None else None
    token = grain._to_token()
    scope_identity = (
        None if during.kind == "absolute" else sha256(repr(during._identity()).encode()).hexdigest()
    )
    return BoundTimeGrid(
        _identity(
            token,
            report_timezone,
            boundary,
            snapshot_digest,
            scope_digest,
            cells,
            scope_identity,
            civil_bounds=civil_bounds,
        ),
        grain,
        report_timezone,
        boundary,
        snapshot_digest,
        scope_digest,
        cells,
        scope_identity=scope_identity,
        calendar_snapshot=snapshot,
        civil_bounds=civil_bounds,
    )
