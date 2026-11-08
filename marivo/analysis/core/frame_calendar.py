"""Freeze source calendar facts once into inert typed finite data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal_grid import TimeShiftAbsent, TimeShiftMatch, builtin_nominal_ordinal
from marivo.analysis.core.artifact_data import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataColumn,
    FrozenCalendarData,
    FrozenIntervalData,
    IntervalDataAxis,
    OutputAttachment,
)
from marivo.analysis.core.calendar_keys import time_key_series, time_keys
from marivo.analysis.core.data_schema import FrozenCalendarOrigin

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
    additional: tuple[OutputAttachment, ...] = ()

    @property
    def attachments(self) -> tuple[OutputAttachment, ...]:
        return (self.directory, *self.additional)


def calendar_descriptor(
    coordinate: CalendarCoordinate,
    *,
    token: str,
    name: str,
    key: DataColumn,
) -> CalendarDataAxis:
    """Capture a pending axis descriptor without freezing result positions."""
    grain = coordinate.grain_spec
    definition: BuiltinCalendarData
    definition = BuiltinCalendarData(_UNITS[grain.unit], grain.count)
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


def freeze_calendar(
    coordinate: CalendarCoordinate,
    *,
    token: str,
    name: str,
    key: DataColumn,
) -> FrozenSourceCalendar:
    grain = coordinate.grain_spec
    labels: list[str] = []
    nominal_ordinals: list[int] = []
    definition: BuiltinCalendarData = BuiltinCalendarData(_UNITS[grain.unit], grain.count)
    for civil in coordinate.civil_bounds:
        local = datetime.fromisoformat(civil[0])
        labels.append(
            local.isoformat(timespec="microseconds")
            if grain.unit in ("second", "minute", "hour")
            else local.date().isoformat()
        )
        nominal_ordinals.append(builtin_nominal_ordinal(grain.unit, grain.count, local))
    directory_name = f"calendar.{token}"
    axis = CalendarDataAxis(
        token,
        (key,),
        coordinate.timezone,
        name=name,
        frozen=FrozenCalendarData(definition, directory_name),
    )
    from marivo.analysis.core.frozen_calendar import (
        calendar_directory_schema,
        direct_mapping_schema,
    )

    additional: tuple[OutputAttachment, ...] = ()
    if coordinate.origin is not None:
        origin = coordinate.origin
        origin_coordinate = origin.coordinate
        frozen_origin = freeze_calendar(
            origin_coordinate,
            token=f"{token}.origin",
            name=name,
            key=replace(key, slot_id=f"{key.slot_id}.origin"),
        )
        assert axis.frozen is not None
        mapping_name = f"shift.{token}"
        axis = replace(
            axis,
            frozen=replace(
                axis.frozen,
                direct=FrozenCalendarOrigin(
                    frozen_origin.axis, mapping_name, origin.kind, origin.by
                ),
            ),
        )
        mapping_schema = direct_mapping_schema(axis)
        targets = {cell[0].isoformat(): cell[0] for cell in coordinate.cells}
        mapping_rows = pd.DataFrame(
            {
                frozen_origin.axis.columns[0].slot_id: pd.Series(
                    time_keys(origin_coordinate), dtype="timestamp[us, tz=UTC][pyarrow]"
                ),
                "target.0": pd.Series(
                    [
                        targets[entry.target_identity]
                        if isinstance(entry, TimeShiftMatch)
                        else None
                        for entry in origin.entries
                    ],
                    dtype="timestamp[us, tz=UTC][pyarrow]",
                ),
                "reason": pd.Series(
                    [
                        entry.reason if isinstance(entry, TimeShiftAbsent) else None
                        for entry in origin.entries
                    ],
                    dtype="string[pyarrow]",
                ),
            }
        )
        additional = (
            frozen_origin.directory,
            OutputAttachment(mapping_name, mapping_schema, mapping_rows),
        )
    schema = calendar_directory_schema(axis)
    selections: list[str] = []
    cutoffs: list[str | None] = []
    for cell, civil in zip(coordinate.cells, coordinate.civil_bounds, strict=True):
        full = cell[:2] == cell[2:]
        prefix = cell[0] == cell[2] and not full
        selections.append(
            "unavailable"
            if cell[0] == cell[1]
            else "full"
            if full
            else "prefix"
            if prefix
            else "partial"
        )
        cutoffs.append(
            datetime.fromisoformat(civil[3]).replace(tzinfo=None).isoformat(timespec="microseconds")
            if prefix
            else None
        )
    rows = pd.DataFrame(
        {
            key.slot_id: time_key_series(coordinate),
            "nominal_label": pd.Series(labels, dtype="string[pyarrow]"),
            "ordinal": pd.Series(range(len(coordinate.cells)), dtype="int64[pyarrow]"),
            "nominal_ordinal": pd.Series(nominal_ordinals, dtype="int64[pyarrow]"),
            "selection": pd.Series(selections, dtype="string[pyarrow]"),
            "cutoff": pd.Series(cutoffs, dtype="string[pyarrow]"),
        }
    )
    return FrozenSourceCalendar(axis, OutputAttachment(directory_name, schema, rows), additional)


@dataclass(frozen=True, slots=True)
class FrozenSourceInterval:
    axis: IntervalDataAxis
    directory: OutputAttachment


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

    axis = IntervalDataAxis(
        token,
        (key,),
        FrozenIntervalData(f"interval.{token}"),
        timezone=coordinate.timezone,
        name=name,
    )
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
