"""Exact Arrow carriers for frozen coordinate cells."""

import pandas as pd
import pyarrow as pa

from marivo.analysis.model.data_schema import calendar_failure as _fail
from marivo.analysis.model.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)


def coordinate_arrow_type(dtype: DataType) -> pa.DataType:
    if isinstance(dtype, ScalarDataType):
        return {
            "boolean": pa.bool_(),
            "string": pa.string(),
            "int64": pa.int64(),
            "float64": pa.float64(),
            "date": pa.date32(),
        }[dtype.name]
    if isinstance(dtype, DecimalDataType):
        return pa.decimal128(dtype.precision, dtype.scale)
    if isinstance(dtype, TimestampDataType):
        return pa.timestamp(dtype.unit, dtype.timezone)
    return pa.duration(dtype.unit)


def coordinate_array(rows: pd.DataFrame, column: DataColumn) -> pa.Array:
    try:
        series = rows[column.slot_id]
        expected = coordinate_arrow_type(column.dtype)
        arrow_values = getattr(series, "array", None)
        values: pa.Array
        if isinstance(arrow_values, pd.arrays.ArrowExtensionArray):
            arrow = arrow_values.__arrow_array__()
            candidate = arrow.combine_chunks() if isinstance(arrow, pa.ChunkedArray) else arrow
            if candidate.type == pa.large_string() and expected == pa.string():
                candidate = candidate.cast(expected)
            values = candidate if candidate.type == expected else pa.array(series, from_pandas=True)
        else:
            values = pa.array(series, from_pandas=True)
        # pandas' Arrow string extension uses large_string for the same string
        # type. No other physical conversion may erase exact units or precision.
        if values.type == pa.large_string() and expected == pa.string():
            values = values.cast(expected)
        if values.type != expected:
            _fail(
                "exact declared physical column type",
                f"{column.slot_id}: {values.type} != {expected}",
            )
        if values.null_count and not column.nullable:
            _fail("nonnull declared coordinate cells", column.slot_id)
        return values
    except (pa.ArrowException, TypeError, ValueError, OverflowError) as error:
        _fail("exact typed coordinate cells", f"{column.slot_id}: {error}")
