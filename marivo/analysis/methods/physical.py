"""Physical numeric carriers used by the current Query kernels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, get_args

import pyarrow as pa

from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.methods.errors import reject

QUERY_COMBINE_KERNEL = "query.combine@v1"
ScalarName: TypeAlias = Literal["boolean", "string", "int64", "float64", "date", "timestamp"]


@dataclass(frozen=True, slots=True)
class ScalarType:
    name: ScalarName

    def __post_init__(self) -> None:
        if self.name not in get_args(ScalarName):
            reject(
                "an exact scalar type", str(self.name), "Declare a supported scalar or DecimalType."
            )


@dataclass(frozen=True, slots=True)
class DecimalType:
    precision: int
    scale: int

    def __post_init__(self) -> None:
        if (
            type(self.precision) is not int
            or type(self.scale) is not int
            or not 0 <= self.scale <= self.precision
            or not 1 <= self.precision <= 38
        ):
            reject(
                "positive Decimal precision and scale within precision",
                repr(self),
                "Bind exact decimal metadata.",
            )

    @property
    def name(self) -> str:
        return f"decimal({self.precision},{self.scale})"


@dataclass(frozen=True, slots=True)
class DurationType:
    """Fixed elapsed ticks, never a calendar interval or a floating value."""

    unit: Literal["s", "ms", "us", "ns"]

    def __post_init__(self) -> None:
        if self.unit not in ("s", "ms", "us", "ns"):
            reject(
                "fixed duration unit s/ms/us/ns",
                repr(self.unit),
                "Preserve the exact elapsed tick unit.",
            )

    @property
    def name(self) -> str:
        return f"interval('{self.unit}')"


ValueType: TypeAlias = ScalarType | DecimalType | DurationType


def stage_arrow_type(dtype: DataType) -> pa.DataType:
    """Return the exact Arrow carrier for one current Query stage slot."""
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
