"""Shared isolated Frame exports and bounded source-free result formatting."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.artifact_data import DataSchema, OutputAttachment
from marivo.analysis.core.data_types import DataColumn, DurationDataType, TimestampDataType
from marivo.analysis.core.frame_structure import axis_role
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


def export_frame(
    schema: DataSchema, rows: pd.DataFrame, attachments: tuple[OutputAttachment, ...] = ()
) -> pd.DataFrame:
    """Copy exact physical arrays and inert frozen interpretation, without readers."""
    series: list[pd.Series] = []
    names: list[tuple[str, str]] = []
    for axis in schema.axes:
        if len(axis.columns) == 1:
            series.append(rows[axis.columns[0].slot_id].reset_index(drop=True).copy(deep=True))
        else:
            arrays = tuple(
                pa.array(rows[column.slot_id], from_pandas=True) for column in axis.columns
            )
            series.append(
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
            )
        names.append(("coordinate", axis.token))
    for role, columns in (("value", schema.values), ("presence", schema.presence)):
        for column in columns:
            series.append(rows[column.slot_id].reset_index(drop=True).copy(deep=True))
            names.append((role, column.name))
    result = pd.concat(series, axis=1) if series else pd.DataFrame(index=pd.RangeIndex(len(rows)))
    result.columns = (
        pd.MultiIndex.from_tuples(names, names=("kind", "name"))
        if names
        else pd.MultiIndex.from_arrays([[], []], names=("kind", "name"))
    )
    result.attrs = {
        "axes": tuple(
            {
                "token": axis.token,
                "name": axis.name,
                "role": axis_role(axis),
                "key_names": tuple(column.name for column in axis.columns),
                "at": axis.at,
                "descriptor": asdict(axis),
            }
            for axis in schema.axes
        ),
        "units": tuple((column.name, column.unit) for column in schema.values),
        "comparisons": tuple(asdict(fact) for fact in schema.comparisons),
        "frozen_coordinates": tuple(
            {"name": part.name, "schema": asdict(part.schema), "rows": part.rows.copy(deep=True)}
            for part in attachments
        ),
    }
    return result


def _cell(array: pa.Array, i: int, column: DataColumn) -> str:
    if not array[i].is_valid:
        return "NA"
    if isinstance(column.dtype, (TimestampDataType, DurationDataType)):
        # pandas scalar extraction maps a valid int64 minimum to NaT. Validity
        # and exact physical ticks remain separate through presentation too.
        return f"{array.cast(pa.int64())[i].as_py()} {array.type} ticks"
    value = _coordinate_scalar(array, i, column)
    if isinstance(value, pa.Scalar) and pa.types.is_date32(array.type):
        return f"{array.cast(pa.int32())[i].as_py()} date32 day ticks"
    return str(value)[:256]


def preview_text(
    schema: DataSchema, rows: pd.DataFrame, attachments: tuple[OutputAttachment, ...]
) -> str:
    """Format only the supplied prefix and its frozen coordinate interpretation."""
    if not len(rows):
        return "empty result"
    if not schema.columns:
        return "one row: ()"
    columns = schema.columns[:8]
    arrays = tuple(pa.array(rows[column.slot_id], from_pandas=True) for column in columns)
    display = pd.DataFrame(
        {
            column.name: [_cell(array, i, column) for i in range(len(rows))]
            for column, array in zip(columns, arrays, strict=True)
        }
    )
    text = display.to_string(index=False, max_colwidth=60)
    # Only selected attachment records are supplied by the stored preview reader.
    # Memory uses the same key projection, without decoding files.
    from marivo.analysis.core.data_schema import CalendarDataAxis, IntervalDataAxis, PairedDataAxis

    parts = {part.name: part for part in attachments}

    def labels(axis: CalendarDataAxis | IntervalDataAxis, keys: set[FrozenKey]) -> None:
        nonlocal text
        assert axis.frozen is not None
        part = parts[axis.frozen.directory_name]
        selected = [
            i for i, key in enumerate(frozen_key_rows(part.rows, axis.columns)) if key in keys
        ]
        values = part.rows.iloc[selected]
        text += f"\n{axis.name}: " + values.to_string(index=False, max_cols=7, max_colwidth=60)

    for axis in schema.axes:
        keys = set(frozen_key_rows(rows, axis.columns))
        if isinstance(axis, PairedDataAxis):
            part = parts[axis.mapping_name]
            selected = [
                i for i, key in enumerate(frozen_key_rows(part.rows, axis.columns)) if key in keys
            ]
            mapped = part.rows.iloc[selected]
            width = len(axis.left.columns)
            for endpoint, columns in (
                (axis.left, part.schema.values[:width]),
                (axis.right, part.schema.values[width:]),
            ):
                endpoint_keys = frozen_key_rows(mapped, columns)
                # Nullable map columns carry the same dtype tags as endpoint keys.
                labels(endpoint, set(endpoint_keys))
        elif isinstance(axis, (CalendarDataAxis, IntervalDataAxis)):
            labels(axis, keys)
    return text
