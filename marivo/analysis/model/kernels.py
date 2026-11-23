"""Invocation-independent identifiers and exact frequency records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias

from marivo.analysis.model.data_types import DataType, DecimalDataType, ScalarDataType

QUERY_COMBINE_KERNEL = "query.combine@v1"


FrequencyValue: TypeAlias = int | float | Decimal


def frequency_value_key(value: FrequencyValue) -> tuple[int, FrequencyValue]:
    # Native exact numeric ordering places valid NaN after every finite value
    # and infinity. A single token also preserves native GROUP BY NaN equality.
    return (1, 0) if isinstance(value, float) and math.isnan(value) else (0, value)


@dataclass(frozen=True, slots=True)
class FrequencyState:
    """One group's exact non-NULL values and its original input support."""

    dtype: DataType
    values: tuple[FrequencyValue, ...]
    frequencies: tuple[int, ...]
    support_count: int
    null_count: int

    def __post_init__(self) -> None:
        if not (
            isinstance(self.dtype, DecimalDataType)
            or self.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
        ):
            raise ValueError("frequency inputs require a numeric DataType")
        if len(self.values) != len(self.frequencies):
            raise ValueError("frequency values and counts must have the same length")
        if any(type(count) is not int or count <= 0 for count in self.frequencies):
            raise ValueError("frequency counts must be positive integers")
        if (
            type(self.support_count) is not int
            or type(self.null_count) is not int
            or self.null_count < 0
            or self.support_count != sum(self.frequencies) + self.null_count
        ):
            raise ValueError("frequency support must equal valid and NULL counts")
        if any(
            type(value) not in (int, float, Decimal)
            or (isinstance(value, Decimal) and not value.is_finite())
            for value in self.values
        ):
            raise ValueError("frequency states require native numeric values")
        if any(
            frequency_value_key(left) >= frequency_value_key(right)
            for left, right in zip(self.values, self.values[1:], strict=False)
        ):
            raise ValueError("frequency values must be distinct and ordered in their native type")

    @property
    def valid_count(self) -> int:
        return self.support_count - self.null_count
