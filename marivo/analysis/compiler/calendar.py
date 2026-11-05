"""Calendar membership through native civil keys and captured source ranges."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.expressions import BuiltinSampleCoordinate
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import Grain
from marivo._timezones import same_timezone
from marivo.datasource._temporal_expressions import render, timestamp

KEY_TYPE = dt.Timestamp(timezone="UTC", scale=6)


def reusable_date_day(calendar: CalendarCoordinate, date_timezone: str, engine: str) -> bool:
    """Qualify a one-to-one DATE/day mapping across every selected day."""
    from marivo.analysis.compiler.source_time import endpoint_rewrite_safe

    grain = calendar.grain_spec
    return (
        grain.kind == "builtin"
        and grain.unit == "day"
        and same_timezone(date_timezone, calendar.timezone)
        and bool(calendar.cells)
        and len(calendar.civil_bounds) == len(calendar.cells)
        and all(cell[1] - cell[0] == timedelta(days=1) for cell in calendar.cells)
        # Captured native labels also qualify the executor's timezone rules;
        # Python and an older server need not ship the same timezone database.
        and all(
            (start := datetime.fromisoformat(bounds[0])).time() == time.min
            and (end := datetime.fromisoformat(bounds[1])).time() == time.min
            and end.date() - start.date() == timedelta(days=1)
            for bounds in calendar.civil_bounds
        )
        and all(
            endpoint_rewrite_safe(point, date_timezone, engine)
            for cell in calendar.cells
            for point in cell[:2]
        )
    )


def builtin_key(
    value: ir.Value,
    calendar: CalendarCoordinate,
    *,
    date_timezone: str | None = None,
    engine: str = "duckdb",
) -> ir.Value:
    """Use the executing backend's civil truncation and native interpretation."""
    from marivo.datasource._temporal_expressions import localize

    grain = calendar.grain_spec
    assert grain.kind == "builtin" and grain.unit is not None
    if date_timezone is not None and reusable_date_day(calendar, date_timezone, engine):
        return value.cast(KEY_TYPE)
    wall = (
        timestamp(value) if value.type().is_date() else render(calendar.timezone, timestamp(value))
    )
    return localize(calendar.timezone, timestamp(builtin_wall_key(wall, grain))).cast(KEY_TYPE)


def builtin_wall_key(wall: ir.Value, grain: Grain) -> ir.Value:
    """Truncate a civil clock without choosing a timezone interpretation."""
    assert grain.kind == "builtin" and grain.unit is not None
    if grain.unit in ("second", "minute", "hour"):
        units = {"second": 1, "minute": 60, "hour": 3600}
        assert grain.count is not None
        width = units[grain.unit] * grain.count
        midnight = wall.truncate("D")
        seconds = ((wall.hour() * 3600 + wall.minute() * 60 + wall.second()) // width) * width
        bucket = midnight + seconds.as_interval("s")
    else:
        truncations = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}
        bucket = wall.truncate(truncations[grain.unit])
    return bucket


def builtin_sample_key(value: ir.Value, calendar: BuiltinSampleCoordinate) -> ir.Value:
    """Build an actual sample key and distinguish the two instants in a civil fold."""
    from marivo.datasource._temporal_expressions import localize

    zone = calendar.timezone
    instant = timestamp(value)
    wall = render(zone, instant)
    grain = calendar.grain_spec
    bucket = timestamp(builtin_wall_key(wall, grain))
    key = localize(zone, bucket)
    if grain.unit in ("second", "minute", "hour") and not same_timezone(zone, "UTC"):
        utc = render("UTC", instant)
        offset = wall.delta(utc, unit="second")
        candidate = localize("UTC", timestamp(bucket - offset.as_interval("s")))
        # The row's offset can differ from the bucket's when a transition lies
        # inside a wider bucket. Use it only when it names the exact boundary.
        key = (render(zone, candidate) == bucket).ifelse(candidate, key)
    return key.cast(KEY_TYPE)


def bind_calendar(
    table: ir.Table,
    time: ir.Value,
    calendar: CalendarCoordinate | IntervalCoordinate | BuiltinSampleCoordinate,
    slot: str,
    *,
    date_timezone: str | None = None,
    engine: str = "duckdb",
) -> ir.Table:
    """Attach a native bucket identity without a calendar directory or JOIN."""
    from marivo.analysis.compiler.observation import temporal_bound

    if isinstance(calendar, BuiltinSampleCoordinate):
        return table.mutate(**{slot: builtin_sample_key(time, calendar)})
    if isinstance(calendar, IntervalCoordinate):
        condition = (time >= temporal_bound(time, calendar.start, calendar.timezone)) & (
            time < temporal_bound(time, calendar.end, calendar.timezone)
        )
        return table.mutate(**{slot: condition.ifelse(ibis.literal(0).cast("!int64"), ibis.null())})
    selected: ir.BooleanValue | None = None
    for start, end in calendar.ranges:
        condition = (time >= temporal_bound(time, start, calendar.timezone)) & (
            time < temporal_bound(time, end, calendar.timezone)
        )
        selected = condition if selected is None else selected | condition
    if selected is None:
        return table.mutate(**{slot: ibis.null().cast(KEY_TYPE)})
    if calendar.grain_spec.kind == "builtin":
        key = builtin_key(time, calendar, date_timezone=date_timezone, engine=engine)
        return table.mutate(**{slot: selected.ifelse(key, ibis.null())})
    if len(calendar.cells) != 1 or calendar.cells[0][:2] != calendar.cells[0][2:]:
        from marivo.analysis.core.model import reject

        reject(
            "one complete custom calendar period",
            calendar.identity,
            "Select exactly one complete period from the certified calendar.",
            "calendar.observe",
        )
    from datetime import datetime

    from marivo.datasource._temporal_expressions import localize

    wall = datetime.fromisoformat(calendar.civil_bounds[0][0]).replace(tzinfo=None)
    return table.mutate(
        **{
            slot: selected.ifelse(
                localize(calendar.timezone, timestamp(ibis.literal(wall))), ibis.null()
            )
        }
    )
