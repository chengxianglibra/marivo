"""Freeze source calendar facts once into inert typed finite data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

import pandas as pd

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import _BuiltinGrain, _key_token, _SemanticGrain
from marivo._timezones import parse_timezone
from marivo.analysis.core.artifact_data import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    FrozenCalendarData,
    FrozenIntervalData,
    IntervalDataAxis,
    NamedCalendarData,
    OutputAttachment,
)
from marivo.analysis.core.data_types import ScalarDataType, TimestampDataType
from marivo.analysis.core.model import reject

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


@dataclass(frozen=True, slots=True)
class FrozenSourceCalendar:
    axis: CalendarDataAxis
    directory: OutputAttachment


def freeze_calendar(
    coordinate: CalendarCoordinate, *, token: str, name: str, key: DataColumn
) -> FrozenSourceCalendar:
    grain = coordinate.grain_spec
    zone = parse_timezone(coordinate.timezone)[1]
    labels: list[str] = []
    if isinstance(grain, _BuiltinGrain):
        definition: BuiltinCalendarData | NamedCalendarData = BuiltinCalendarData(
            _UNITS[grain.unit], grain.count
        )
        for cell in coordinate.cells:
            local = cell[0].astimezone(zone)
            labels.append(
                local.isoformat(timespec="microseconds")
                if grain.unit in ("second", "minute", "hour")
                else local.date().isoformat()
            )
    elif isinstance(grain, _SemanticGrain):
        snapshot = coordinate.snapshot
        if (
            snapshot is None
            or snapshot.snapshot_digest != coordinate.snapshot_digest
            or snapshot.calendar_ref != grain.calendar
        ):
            reject(
                "the exact captured named calendar snapshot",
                coordinate.identity,
                "Capture the complete certified calendar before source evaluation.",
                "frame.calendar",
            )
        definition = NamedCalendarData(grain.calendar.path, snapshot.snapshot_digest, grain.level)
        for cell in coordinate.cells:
            start, end = cell[0].astimezone(zone).date(), cell[1].astimezone(zone).date()
            if grain.level == "day":
                labels.append(_key_token(start.isoformat()))
            else:
                periods = tuple(
                    period
                    for period in snapshot.periods
                    if period.level_name == grain.level
                    and (period.start_date, period.end_date) == (start, end)
                )
                if len(periods) != 1:
                    reject(
                        "one exact captured named period",
                        repr((grain.level, start, end)),
                        "Use a complete certified period matching the nominal bounds.",
                        "frame.calendar",
                    )
                labels.append(_key_token(periods[0].key))
    else:
        reject(
            "a structured builtin or named grain",
            type(grain).__name__,
            "Capture the owning Grain before evaluation.",
            "frame.calendar",
        )
    directory_name = f"calendar.{token}"
    axis = CalendarDataAxis(
        token,
        (key,),
        coordinate.timezone,
        name=name,
        frozen=FrozenCalendarData(definition, directory_name),
    )
    values = (
        DataColumn("nominal_label", "nominal_label", ScalarDataType("string"), False),
        DataColumn("ordinal", "ordinal", ScalarDataType("int64"), False),
        *(
            DataColumn(name, name, TimestampDataType("us", "UTC"), False)
            for name in ("nominal_start", "nominal_end", "actual_start", "actual_end")
        ),
    )
    schema = DataSchema(
        (DataAxis(token, (key,), name=name),),
        values,
        value_paths=tuple((column.name,) for column in values),
    )
    rows = pd.DataFrame(
        {
            key.slot_id: pd.Series(range(len(coordinate.cells)), dtype="int64[pyarrow]"),
            "nominal_label": pd.Series(labels, dtype="string[pyarrow]"),
            "ordinal": pd.Series(range(len(coordinate.cells)), dtype="int64[pyarrow]"),
            **{
                column.slot_id: pd.Series(
                    [
                        datetime.fromisoformat(cell[index].astimezone(timezone.utc).isoformat())
                        for cell in coordinate.cells
                    ],
                    dtype="timestamp[us, tz=UTC][pyarrow]",
                )
                for index, column in enumerate(values[2:])
            },
        }
    )
    return FrozenSourceCalendar(axis, OutputAttachment(directory_name, schema, rows))


@dataclass(frozen=True, slots=True)
class FrozenSourceInterval:
    axis: IntervalDataAxis
    directory: OutputAttachment


FrozenSourceTime: TypeAlias = FrozenSourceCalendar | FrozenSourceInterval


def freeze_interval(
    coordinate: IntervalCoordinate, *, token: str, name: str, key: DataColumn
) -> FrozenSourceInterval:
    """Freeze the sole declared scope using an inert key and exact UTC bounds."""
    from marivo.analysis.core.frozen_calendar import (
        FrozenIntervalBucket,
        FrozenIntervalDirectory,
        interval_directory_rows,
        interval_directory_schema,
    )

    axis = IntervalDataAxis(token, (key,), FrozenIntervalData(f"interval.{token}"), name=name)
    start, end = (
        datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())
        for value in (coordinate.start, coordinate.end)
    )
    directory = FrozenIntervalDirectory(
        axis, (FrozenIntervalBucket(((key.dtype, 0),), start, end, coordinate.timezone),)
    )
    return FrozenSourceInterval(
        axis,
        OutputAttachment(
            axis.frozen.directory_name,
            interval_directory_schema(axis),
            interval_directory_rows(directory),
        ),
    )
