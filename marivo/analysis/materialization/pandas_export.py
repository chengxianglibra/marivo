"""Shared isolated Frame exports and source-free result formatting."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.artifact_data import DataSchema, OutputAttachment
from marivo.analysis.core.data_schema import (
    CalendarDataAxis,
    DataAxis,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import DataColumn, DurationDataType, TimestampDataType
from marivo.analysis.core.frame_schema import (
    LogicalSchema,
    export_layout,
    selected_columns,
)
from marivo.analysis.core.frozen_calendar import FrozenKey, frozen_key_rows


def _coordinate_scalar(array: pa.Array, index: int, column: DataColumn) -> object:
    scalar = array[index]
    if not scalar.is_valid:
        return None
    if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
        return scalar
    try:
        value: object = scalar.as_py()
    except OverflowError:
        if pa.types.is_date32(array.type):
            return scalar
        raise
    return value


def export_names(
    schema: LogicalSchema, axis_names: tuple[str, ...], *, time_details: bool = False
) -> tuple[str, ...]:
    """Validate the captured flat namespace before evaluation or payload reads."""
    return tuple(
        column.name
        for column in export_layout(
            schema, axis_names, validate_names=True, time_details=time_details
        )
    )


def _coordinate_rows(
    part: OutputAttachment, columns: tuple[DataColumn, ...], keys: tuple[FrozenKey | None, ...]
) -> pd.DataFrame:
    positions = {key: i for i, key in enumerate(frozen_key_rows(part.rows, columns))}
    return (
        part.rows.reset_index(drop=True)
        .reindex([None if key is None else positions[key] for key in keys])
        .reset_index(drop=True)
    )


def _time_columns(
    axis: CalendarDataAxis | IntervalDataAxis,
    keys: tuple[FrozenKey | None, ...],
    parts: dict[str, OutputAttachment],
    time_details: bool,
) -> list[pd.Series]:
    assert axis.frozen is not None
    selected = _coordinate_rows(parts[axis.frozen.directory_name], axis.columns, keys)

    zone = axis.timezone

    if zone.startswith(("UTC+", "UTC-")):
        zone = zone[3:]
        if len(zone) == 9:
            # Arrow/pandas timezone decoding supports minute offsets only.
            # Keep exact instants in UTC for sub-minute offsets, never round them.
            zone = zone[:6] if zone[-2:] == "00" else "UTC"

    def stamp(name: str) -> pd.Series:
        # Preserve Arrow precision and validity while displaying the captured axis timezone.
        values = pa.array(selected[name])
        assert pa.types.is_timestamp(values.type)
        return pd.Series(
            values.cast(pa.timestamp(values.type.unit, tz=zone)),
            dtype=pd.ArrowDtype(pa.timestamp(values.type.unit, tz=zone)),
        )

    bounds = (
        [
            stamp(axis.columns[0].slot_id)
            if pa.types.is_timestamp(pa.array(selected[axis.columns[0].slot_id]).type)
            else selected[axis.columns[0].slot_id]
        ]
        if isinstance(axis, CalendarDataAxis)
        else [stamp("start"), stamp("end")]
    )
    if isinstance(axis, CalendarDataAxis) and time_details:
        labels = selected["nominal_label"]
        bounds.append(labels)
    return bounds


def _coordinate_columns(
    axis: DataAxis,
    rows: pd.DataFrame,
    parts: dict[str, OutputAttachment],
    time_details: bool = False,
) -> list[pd.Series]:
    if isinstance(axis, PairedDataAxis):
        part = parts[axis.mapping_name]
        mapped = _coordinate_rows(part, axis.columns, frozen_key_rows(rows, axis.columns))
        width = len(axis.left.columns)
        result: list[pd.Series] = []
        for endpoint, columns in (
            (axis.left, part.schema.values[:width]),
            (axis.right, part.schema.values[width:]),
        ):
            keys = tuple(
                None if all(value is None for _, value in key) else key
                for key in frozen_key_rows(mapped, columns)
            )
            result.extend(_time_columns(endpoint, keys, parts, time_details))
        return result
    if isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
        return _time_columns(axis, frozen_key_rows(rows, axis.columns), parts, time_details)
    if len(axis.columns) == 1:
        return [rows[axis.columns[0].slot_id].reset_index(drop=True)]
    arrays = tuple(pa.array(rows[column.slot_id], from_pandas=True) for column in axis.columns)
    return [
        pd.Series(
            [
                tuple(
                    _coordinate_scalar(array, i, column)
                    for column, array in zip(axis.columns, arrays, strict=True)
                )
                for i in range(len(rows))
            ],
            dtype=object,
        )
    ]


def export_frame(
    schema: DataSchema,
    rows: pd.DataFrame,
    attachments: tuple[OutputAttachment, ...] = (),
    *,
    axis_names: tuple[str, ...] | None = None,
    columns: tuple[str, ...] | None = None,
    time_details: bool = False,
    validate_names: bool = True,
) -> pd.DataFrame:
    """Copy readable coordinates and exact value arrays into a table without attrs."""
    names = tuple(
        column.name
        for column in export_layout(
            schema, axis_names, columns, validate_names=validate_names, time_details=time_details
        )
    )
    parts = {part.name: part for part in attachments}
    series = [
        column.copy(deep=True)
        for axis in schema.axes
        for column in _coordinate_columns(axis, rows, parts, time_details)
    ]
    series.extend(
        rows[column.slot_id].reset_index(drop=True).copy(deep=True)
        for column in selected_columns(schema, columns)
    )
    result = pd.concat(series, axis=1) if series else pd.DataFrame(index=pd.RangeIndex(len(rows)))
    result.columns = pd.Index(names, dtype=object)
    return result
