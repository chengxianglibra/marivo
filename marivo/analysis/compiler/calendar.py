"""Calendar membership through native civil keys and captured source ranges."""

from __future__ import annotations

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import Grain
from marivo.datasource._temporal_expressions import render, timestamp

KEY_TYPE = dt.Timestamp(timezone="UTC", scale=6)


def builtin_key(value: ir.Value, calendar: CalendarCoordinate) -> ir.Value:
    """Use the executing backend's civil truncation and native interpretation."""
    from marivo.datasource._temporal_expressions import localize

    grain = calendar.grain_spec
    assert grain.kind == "builtin" and grain.unit is not None
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


def bind_calendar(
    table: ir.Table,
    time: ir.Value,
    calendar: CalendarCoordinate | IntervalCoordinate,
    slot: str,
) -> ir.Table:
    """Attach a native bucket identity without a calendar directory or JOIN."""
    from marivo.analysis.compiler.observation import temporal_bound

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
        return table.mutate(**{slot: selected.ifelse(builtin_key(time, calendar), ibis.null())})
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
