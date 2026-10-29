"""Freeze source calendar facts once into inert typed finite data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal, TypeAlias

import pandas as pd

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import _BuiltinGrain, _key_token, _SemanticGrain, anchor_from_datetime
from marivo._temporal_grid import TimeShiftAbsent, TimeShiftMatch, builtin_nominal_ordinal
from marivo.analysis.core.artifact_data import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataColumn,
    FrozenCalendarData,
    FrozenIntervalData,
    IntervalDataAxis,
    NamedCalendarData,
    OutputAttachment,
)
from marivo.analysis.core.data_schema import FrozenCalendarOrigin
from marivo.analysis.core.lineage import SemanticObjectIdentity
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
    additional: tuple[OutputAttachment, ...] = ()

    @property
    def attachments(self) -> tuple[OutputAttachment, ...]:
        return (self.directory, *self.additional)


def freeze_calendar(
    coordinate: CalendarCoordinate,
    *,
    token: str,
    name: str,
    key: DataColumn,
    calendar_ref: SemanticObjectIdentity | None = None,
) -> FrozenSourceCalendar:
    grain = coordinate.grain_spec
    labels: list[str] = []
    nominal_ordinals: list[int] = []
    if isinstance(grain, _BuiltinGrain):
        definition: BuiltinCalendarData | NamedCalendarData = BuiltinCalendarData(
            _UNITS[grain.unit], grain.count
        )
        for civil in coordinate.civil_bounds:
            local = datetime.fromisoformat(civil[0])
            labels.append(
                local.isoformat(timespec="microseconds")
                if grain.unit in ("second", "minute", "hour")
                else local.date().isoformat()
            )
            nominal_ordinals.append(builtin_nominal_ordinal(grain.unit, grain.count, local))
    elif isinstance(grain, _SemanticGrain):
        snapshot = coordinate.snapshot
        if (
            snapshot is None
            or snapshot.snapshot_digest != coordinate.snapshot_digest
            or snapshot.calendar_ref != grain._calendar
        ):
            reject(
                "the exact captured named calendar snapshot",
                coordinate.identity,
                "Capture the complete certified calendar before source evaluation.",
                "frame.calendar",
            )
        definition = NamedCalendarData(
            grain._calendar.path, snapshot.snapshot_digest, grain.level, calendar_ref
        )
        for civil in coordinate.civil_bounds:
            start, end = (
                datetime.fromisoformat(civil[0]).date(),
                datetime.fromisoformat(civil[1]).date(),
            )
            if grain.level == "day":
                labels.append(_key_token(start.isoformat()))
                nominal_ordinals.append(start.toordinal() - 1)
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
                nominal_ordinals.append(periods[0].global_ordinal)
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
    from marivo.analysis.core.frozen_calendar import (
        calendar_directory_schema,
        direct_mapping_schema,
    )

    additional: tuple[OutputAttachment, ...] = ()
    if coordinate.origin is not None:
        origin = coordinate.origin
        grid = origin.grid
        origin_coordinate = CalendarCoordinate(
            grid.identity,
            grid.grain_spec,
            grid.boundary_timezone,
            grid.snapshot_digest,
            tuple(
                (
                    anchor_from_datetime(cell.original_start),
                    anchor_from_datetime(cell.original_end),
                    anchor_from_datetime(cell.start),
                    anchor_from_datetime(cell.end),
                )
                for cell in grid.cells
            ),
            grid.calendar_snapshot,
            civil_bounds=grid.civil_bounds,
        )
        frozen_origin = freeze_calendar(
            origin_coordinate,
            token=f"{token}.origin",
            name=name,
            key=replace(key, slot_id=f"{key.slot_id}.origin"),
            calendar_ref=calendar_ref,
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
        targets = {cell[0].isoformat(): i for i, cell in enumerate(coordinate.cells)}
        mapping_rows = pd.DataFrame(
            {
                frozen_origin.axis.columns[0].slot_id: pd.Series(
                    range(len(origin.entries)), dtype="int64[pyarrow]"
                ),
                "target.0": pd.Series(
                    [
                        targets[entry.target_identity]
                        if isinstance(entry, TimeShiftMatch)
                        else None
                        for entry in origin.entries
                    ],
                    dtype="int64[pyarrow]",
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
    bounds = ("nominal_start", "nominal_end", "actual_start", "actual_end")
    rows = pd.DataFrame(
        {
            key.slot_id: pd.Series(range(len(coordinate.cells)), dtype="int64[pyarrow]"),
            "nominal_label": pd.Series(labels, dtype="string[pyarrow]"),
            "ordinal": pd.Series(range(len(coordinate.cells)), dtype="int64[pyarrow]"),
            "nominal_ordinal": pd.Series(nominal_ordinals, dtype="int64[pyarrow]"),
            **{
                bound: pd.Series(
                    [
                        datetime.fromisoformat(cell[index].astimezone(timezone.utc).isoformat())
                        for cell in coordinate.cells
                    ],
                    dtype="timestamp[us, tz=UTC][pyarrow]",
                )
                for index, bound in enumerate(bounds)
            },
            **{
                f"{bound}_civil": pd.Series(
                    [civil[index] for civil in coordinate.civil_bounds],
                    dtype="string[pyarrow]",
                )
                for index, bound in enumerate(bounds)
            },
        }
    )
    return FrozenSourceCalendar(axis, OutputAttachment(directory_name, schema, rows), additional)


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
