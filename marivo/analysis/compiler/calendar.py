"""Calendar membership through native lattice keys and unique equality maps."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import reduce
from math import gcd

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo.analysis.compiler.source_time import render, timestamp
from marivo.analysis.core.time_authority import time_zone
from marivo.datasource._calendar_relations import CalendarFloor, calendar_data

KEY_TYPE = dt.Timestamp(timezone="UTC", scale=6)


def _floor(value: ir.Value, origin: datetime, width: int) -> ir.Value:
    return CalendarFloor(
        value.op(),
        origin.astimezone(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="microseconds"),
        width,
    ).to_expr()


def builtin_key(value: ir.Value, calendar: CalendarCoordinate) -> ir.Value:
    """Calculate a native key for the frozen builtin lattice or civil period."""
    grain = calendar.grain_spec
    assert grain.kind == "builtin"
    if not calendar.cells:
        return ibis.null().cast(KEY_TYPE)
    if grain.unit in ("second", "minute", "hour"):
        widths = tuple(int((cell[1] - cell[0]).total_seconds()) for cell in calendar.cells)
        width = reduce(
            gcd,
            (
                *widths,
                *(int((cell[0] - calendar.cells[0][0]).total_seconds()) for cell in calendar.cells),
            ),
        )
        return _floor(value, calendar.cells[0][0], width)
    wall = value if value.type().is_date() else render(calendar.timezone, timestamp(value))
    units = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}
    assert isinstance(wall, (ir.DateValue, ir.TimestampValue)) and grain.unit is not None
    return wall.truncate(units[grain.unit]).cast("date")


def _civil_key(day: date, unit: str | None) -> date:
    """Mirror native civil truncation without interpreting a local midnight."""
    if unit == "week":
        return day - timedelta(days=day.weekday())
    if unit == "month":
        return day.replace(day=1)
    if unit == "quarter":
        return day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)
    if unit == "year":
        return day.replace(month=1, day=1)
    return day


def bind_calendar(
    table: ir.Table,
    time: ir.Value,
    calendar: CalendarCoordinate | IntervalCoordinate,
    slot: str,
) -> ir.Table:
    """Attach at most one selected bucket, retaining NULL for unmatched facts.

    Builtin subday clocks use native arithmetic. A DST transition can split a
    nominal width; its finer elapsed lattice has a unique equality mapping to
    the already frozen starts. No fact scans an array or a range directory.
    Civil builtin periods use native truncation and unique date-key rows.
    Certified calendars use their unique civil-date to period mapping.
    """
    from marivo.analysis.compiler.observation import temporal_bound

    if isinstance(calendar, IntervalCoordinate):
        condition = (time >= temporal_bound(time, calendar.start, calendar.timezone)) & (
            time < temporal_bound(time, calendar.end, calendar.timezone)
        )
        return table.mutate(**{slot: condition.ifelse(ibis.literal(0).cast("!int64"), ibis.null())})
    if not calendar.cells:
        return table.mutate(**{slot: ibis.null().cast(KEY_TYPE)})
    grain = calendar.grain_spec
    subday = grain.kind == "builtin" and grain.unit in ("second", "minute", "hour")
    key = (
        builtin_key(time, calendar)
        if grain.kind == "builtin"
        else (time if time.type().is_date() else render(calendar.timezone, timestamp(time))).cast(
            "date"
        )
    )
    rows: list[tuple[object, ...]] = []
    zone = time_zone(calendar.timezone)
    width = key.op().width if subday else 0
    for nominal_start, nominal_end, start, end in calendar.cells:
        if subday:
            cursor = nominal_start
            while cursor < nominal_end:
                rows.append((cursor, nominal_start, start, end))
                cursor += timedelta(seconds=width)
        elif grain.kind == "builtin":
            first = nominal_start.astimezone(zone).date()
            last = (nominal_end - timedelta(microseconds=1)).astimezone(zone).date()
            # A repeated midnight's short bucket can return to the previous
            # date. Order the half-open interval's included endpoint dates.
            # Missing civil midnights still belong to the retained period.
            day, last = min(first, last), max(first, last)
            prior = None
            while day <= last:
                match = _civil_key(day, grain.unit)
                if match != prior:
                    rows.append((match, nominal_start, start, end))
                    prior = match
                day += timedelta(days=1)
        else:
            day = nominal_start.astimezone(zone).date()
            last = nominal_end.astimezone(zone).date()
            while day < last:
                # Clip the materialized date map to this selected occurrence.
                if day >= start.astimezone(zone).date() and day <= end.astimezone(zone).date():
                    rows.append((day, nominal_start, start, end))
                day += timedelta(days=1)
    # The common complete regular axis needs no source-side directory at all.
    if (
        subday
        and len(rows) == len(calendar.cells)
        and all(cell[0] == cell[2] and cell[1] == cell[3] for cell in calendar.cells)
        and all(a[1] == b[0] for a, b in zip(calendar.cells, calendar.cells[1:], strict=False))
    ):
        start, end = calendar.cells[0][2], calendar.cells[-1][3]
        selected = (time >= temporal_bound(time, start, calendar.timezone)) & (
            time < temporal_bound(time, end, calendar.timezone)
        )
        return table.mutate(**{slot: selected.ifelse(key, ibis.null())})
    prefix = "__calendar_map_"
    while any(name.startswith(prefix) for name in table.columns):
        prefix += "_"
    names = tuple(prefix + name for name in ("match", "key", "start", "end"))
    schema = ibis.schema(
        {names[0]: KEY_TYPE if subday else dt.date, **dict.fromkeys(names[1:], KEY_TYPE)}
    )
    repeated: tuple[str, ...] = ()
    if grain.kind == "builtin" and not subday:
        grouped: dict[object, list[tuple[object, ...]]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(row)
        if any(len(group) > 1 for group in grouped.values()):
            # A repeated civil boundary has exactly two frozen instants. One
            # equality row chooses between them; it never duplicates a fact.
            assert all(len(group) <= 2 for group in grouped.values())
            repeated = tuple(prefix + field for field in ("later_key", "later_start", "later_end"))
            schema = ibis.schema({**dict(schema.items()), **dict.fromkeys(repeated, KEY_TYPE)})
            rows = [
                (*group[0], *(group[1][1:] if len(group) == 2 else (None, None, None)))
                for group in grouped.values()
            ]
    directory = calendar_data(schema, tuple(rows))
    source_key = prefix + "source"
    original = tuple(name for name in table.columns if name != slot)
    left = table.mutate(**{source_key: key})
    joined = left.left_join(directory, left[source_key] == directory[names[0]])
    # Native DATE is compared to civil boundaries, as in temporal_bound.
    bucket = directory[names[1]]
    lower, upper = directory[names[2]], directory[names[3]]
    if repeated:
        later = directory[repeated[0]]
        boundary = render(calendar.timezone, timestamp(later)) if time.type().is_date() else later
        use_later = (time >= boundary).fill_null(False)
        bucket = use_later.ifelse(later, bucket)
        lower = use_later.ifelse(directory[repeated[1]], lower)
        upper = use_later.ifelse(directory[repeated[2]], upper)
    if time.type().is_date():
        lower = render(calendar.timezone, timestamp(lower))
        upper = render(calendar.timezone, timestamp(upper))
    valid = (time >= lower) & (time < upper)
    return joined.select(
        *[left[name] for name in original], **{slot: valid.ifelse(bucket, ibis.null())}
    )


def bind_wall_clock(
    table: ir.Table,
    wall: ir.Value,
    zone_name: str,
    calendar: CalendarCoordinate,
    name: str,
) -> ir.Table:
    """Interpret raw local clocks by governed wall order, using one row per date.

    The neutral UTC carrier preserves the authored civil fields. Each date has
    one offset, or one offset transition: folds use the later offset and gaps
    use the transition instant. This does not change ordinary value projection.
    """
    from datetime import time as civil_time

    if not calendar.cells:
        return table.mutate(**{name: ibis.null().cast(KEY_TYPE)})
    zone = time_zone(zone_name)
    first = min(cell[2] for cell in calendar.cells).astimezone(zone).date() - timedelta(days=1)
    last = max(cell[3] for cell in calendar.cells).astimezone(zone).date() + timedelta(days=1)
    rows: list[tuple[object, ...]] = []
    day = first
    while day <= last:
        midnight = datetime.combine(day, civil_time())
        tomorrow = midnight + timedelta(days=1)
        before = midnight.replace(tzinfo=zone, fold=0).utcoffset()
        after = (tomorrow - timedelta(microseconds=1)).replace(tzinfo=zone, fold=1).utcoffset()
        assert before is not None and after is not None
        lower = upper = transition = midnight.replace(tzinfo=timezone.utc)
        if before != after:
            left = (midnight - before).replace(tzinfo=timezone.utc) - timedelta(seconds=1)
            right = (tomorrow - after).replace(tzinfo=timezone.utc)
            while right - left > timedelta(seconds=1):
                middle = left + timedelta(seconds=int((right - left).total_seconds()) // 2)
                if middle.astimezone(zone).utcoffset() == before:
                    left = middle
                else:
                    right = middle
            transition = right
            lower, upper = sorted((transition + before, transition + after))
        rows.append(
            (day, int(before.total_seconds()), int(after.total_seconds()), lower, upper, transition)
        )
        day += timedelta(days=1)
    prefix = "__wall_calendar_"
    while any(column.startswith(prefix) for column in table.columns):
        prefix += "_"
    day_key, old, new, lower_key, upper_key, instant = (
        prefix + field for field in ("day", "old", "new", "lower", "upper", "instant")
    )
    directory = calendar_data(
        ibis.schema(
            {
                day_key: dt.date,
                old: dt.int64,
                new: dt.int64,
                lower_key: KEY_TYPE,
                upper_key: KEY_TYPE,
                instant: KEY_TYPE,
            }
        ),
        tuple(rows),
    )
    source_key = prefix + "source"
    left = table.mutate(**{source_key: wall.cast("date")})
    joined = left.left_join(directory, left[source_key] == directory[day_key])

    def shifted(offset: ir.IntegerValue) -> ir.Value:
        # Trino's dynamic interval parser accepts a nonnegative magnitude;
        # timestamp addition/subtraction retains exact signed offset semantics.
        magnitude = offset.abs().as_interval("s")
        return (offset >= 0).ifelse(wall - magnitude, wall + magnitude)

    early = shifted(directory[old])
    late = shifted(directory[new])
    corrected = (wall < directory[lower_key]).ifelse(
        early,
        ((wall < directory[upper_key]) & (directory[new] > directory[old])).ifelse(
            directory[instant], late
        ),
    )
    return joined.select(
        *[left[column] for column in table.columns if column != name], **{name: corrected}
    )
