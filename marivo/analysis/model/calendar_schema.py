"""Frozen axis descriptors shared by source binding and retained calendars."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo._definition.types import CalendarCoordinate
from marivo._kernel.time_types import Grain
from marivo.analysis.model.data_schema import (
    BuiltinCalendarData,
    CalendarDataAxis,
    FrozenCalendarData,
    FrozenCalendarOrigin,
)
from marivo.analysis.model.data_types import DataColumn

BuiltinUnit = Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]
_UNITS: dict[str, BuiltinUnit] = {
    "second": "second",
    "minute": "minute",
    "hour": "hour",
    "day": "day",
    "week": "week",
    "month": "month",
    "quarter": "quarter",
    "year": "year",
}


def calendar_grain(grain: Grain) -> BuiltinCalendarData:
    """Capture the builtin unit and width of a governed calendar."""
    return BuiltinCalendarData(_UNITS[grain.unit], grain.count)


def calendar_descriptor(
    coordinate: CalendarCoordinate,
    *,
    token: str,
    name: str,
    key: DataColumn,
) -> CalendarDataAxis:
    """Capture a pending axis descriptor without freezing result positions."""
    definition = calendar_grain(coordinate.grain_spec)
    origin = coordinate.origin
    direct = (
        FrozenCalendarOrigin(
            calendar_descriptor(
                origin.coordinate,
                token=f"{token}.origin",
                name=name,
                key=replace(key, slot_id=f"{key.slot_id}.origin"),
            ),
            f"shift.{token}",
            origin.kind,
            origin.by,
        )
        if origin is not None
        else None
    )
    return CalendarDataAxis(
        token,
        (key,),
        coordinate.timezone,
        name=name,
        frozen=FrozenCalendarData(definition, f"calendar.{token}", direct=direct),
    )
