"""Validate the shared typed values of captured Frame method facts."""

from __future__ import annotations

from decimal import Decimal
from math import isfinite

from marivo.analysis.model.data_types import (
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.method_values import (
    FrameMethodValue,
    _fail,
)
from marivo.semantic.spi import ratio_unit


def _quotient_unit(numerator: str | None, denominator: str | None) -> str | None:
    try:
        return ratio_unit(numerator, denominator)
    except ValueError:
        _fail("governed ratio component units", "invalid numerator or denominator unit")


def _total(value: FrameMethodValue) -> None:
    if (
        type(value) is not FrameMethodValue
        or value.value is None
        or not (
            isinstance(value.column.dtype, (DecimalDataType, DurationDataType))
            or value.column.dtype in (ScalarDataType("int64"), ScalarDataType("float64"))
        )
    ):
        _fail("a nonnull typed numeric or Duration parent total", type(value).__name__)


def _totals(left: FrameMethodValue, right: FrameMethodValue, change: FrameMethodValue) -> None:
    for value in (left, right, change):
        _total(value)
    if not left.column.unit == right.column.unit == change.column.unit:
        _fail("matching total and change units", "inconsistent parent total units")
    durations = tuple(isinstance(v.column.dtype, DurationDataType) for v in (left, right, change))
    if any(durations) and not (
        all(durations) and left.column.dtype == right.column.dtype == change.column.dtype
    ):
        _fail("matching exact Duration units or numeric totals", "inconsistent total types")


def _divisor(value: int | float | Decimal, operation: str) -> None:
    if (
        type(value) not in (int, float, Decimal)
        or (isinstance(value, float) and not isfinite(value))
        or (isinstance(value, Decimal) and not value.is_finite())
        or value == 0
        or (operation == "rescale" and value < 0)
    ):
        _fail("a finite nonzero typed divisor, positive for rescale", repr(value))
    if operation not in ("divide", "rescale"):
        _fail("divide or rescale", operation)
