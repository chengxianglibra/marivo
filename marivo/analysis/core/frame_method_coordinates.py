"""Retained coordinate attachments for typed Attribute facts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo.analysis.model.data_schema import (
    DataSchema,
)
from marivo.analysis.model.data_types import (
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.method_values import (
    _cell,
    _FactRecord,
    _fail,
    _MethodScalar,
    _scalar,
    _short,
    _text,
)


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodCoordinate(_FactRecord):
    """Own a named finite typed snapshot. Args: name, schema, rows; e.g. ``FrameMethodCoordinate('c', s, ())``.

    Returns a frozen record; rows must be immutable exact-width scalar tuples.
    """

    name: str
    schema: DataSchema = field(
        metadata={
            "meaning": "Read coordinate.schema for the complete saved coordinate layout. Read-only schema metadata; nested axes and bindings do not acquire public construction authority."
        }
    )
    rows: tuple[tuple[bool | int | float | str | date | Decimal | None, ...], ...]

    def __post_init__(self) -> None:
        _text(self.name)
        if type(self.schema) is not DataSchema or type(self.rows) is not tuple:
            _fail("a pure schema and immutable coordinate rows", type(self).__name__)
        for row in self.rows:
            if type(row) is not tuple or len(row) != len(self.schema.columns):
                _fail("complete immutable rows in schema column order", self.name)
            for column, value in zip(self.schema.columns, row, strict=True):
                _scalar(column, value)

    def _summary(self) -> str:
        return f"kind=coordinate, name={_short(self.name)!r}, rows={len(self.rows)}, columns={len(self.schema.columns)}"

    def _lines(self) -> Iterator[str]:
        for row in self.rows:
            yield " | ".join(_cell(value) for value in row)


def _arrow_table(schema: DataSchema, rows: tuple[tuple[_MethodScalar, ...], ...]) -> pd.DataFrame:
    result: dict[str, pd.Series] = {}
    for i, column in enumerate(schema.columns):
        dtype = column.dtype
        if isinstance(dtype, ScalarDataType):
            physical = {
                "boolean": pa.bool_(),
                "string": pa.string(),
                "int64": pa.int64(),
                "float64": pa.float64(),
                "date": pa.date32(),
            }[dtype.name]
        elif isinstance(dtype, DecimalDataType):
            physical = pa.decimal128(dtype.precision, dtype.scale)
        elif isinstance(dtype, TimestampDataType):
            physical = pa.timestamp(dtype.unit, dtype.timezone)
        else:
            physical = pa.duration(dtype.unit)
        values = tuple(row[i] for row in rows)
        array = pa.array(
            values,
            type=pa.int64()
            if isinstance(dtype, (TimestampDataType, DurationDataType))
            else physical,
        )
        result[column.slot_id] = pd.Series(pd.arrays.ArrowExtensionArray(array.cast(physical)))
    return pd.DataFrame(result, index=pd.RangeIndex(len(rows)))
