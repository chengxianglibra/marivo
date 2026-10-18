"""Typed numeric finishing and nullable arithmetic for fixed relations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from typing import TypeAlias

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.data_types import DataType, DurationDataType, ScalarDataType
from marivo.analysis.methods.physical import DecimalType, DurationType, ValueType

Number: TypeAlias = int | float | Decimal


def checked_sum(values: Sequence[object], physical: pa.DataType) -> Number:
    """Accumulate exactly (or compensated float64), then check the stored type."""
    if pa.types.is_decimal(physical):
        if any(type(value) is not Decimal or not value.is_finite() for value in values):
            raise ValueError("Decimal state has a non-Decimal or nonfinite component")
        with localcontext() as context:
            context.prec = 120
            result = sum((value for value in values if isinstance(value, Decimal)), Decimal(0))
            if abs(result) >= Decimal(10) ** (physical.precision - physical.scale):
                raise OverflowError("Decimal state precision overflow")
        return result
    if pa.types.is_floating(physical):
        if any(type(value) is not float or not math.isfinite(value) for value in values):
            raise ValueError("float64 state has a non-float or nonfinite component")
        floating = math.fsum(value for value in values if isinstance(value, float))
        if not math.isfinite(floating):
            raise OverflowError("float64 state overflow")
        return floating
    if physical != pa.int64() or any(type(value) is not int for value in values):
        raise ValueError("state requires its declared int64/float64/Decimal components")
    integer = sum(value for value in values if type(value) is int)
    if not -(2**63) <= integer < 2**63:
        raise OverflowError("int64 state overflow")
    return integer


def finish_division(numerator: Number, denominator: Number, output: ValueType) -> Number:
    """Finish retained components in their captured output carrier."""
    if isinstance(output, (DurationType, DecimalType)):
        exact = Fraction(numerator) / Fraction(denominator)
    if isinstance(output, DurationType):
        ticks = round(exact)
        if not -(2**63) <= ticks < 2**63:
            raise OverflowError("Duration finish tick overflow")
        return ticks
    if isinstance(output, DecimalType):
        with localcontext() as context:
            context.prec = 120
            value = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
                Decimal(1).scaleb(-output.scale), rounding=ROUND_HALF_EVEN
            )
            if abs(value) >= Decimal(10) ** (output.precision - output.scale):
                raise OverflowError("Decimal finish precision overflow")
            return value
    value_float = float(numerator) / float(denominator)
    if not math.isfinite(value_float):
        raise OverflowError("float64 finish overflow")
    return value_float


def nullable_arithmetic(
    operation: str,
    left: pd.Series,
    right: pd.Series,
    *,
    zero_denominator: str = "null",
    exact_duration: bool = False,
) -> pd.Series:
    """Compute over nullable pandas values, masking zeros before division.

    Object arithmetic preserves int64 precision until the shared output
    normalizer checks overflow. Decimal work uses the existing wide context.
    """
    from marivo.semantic._numeric_lowering import numeric_failure

    a, b = left.astype(object), right.astype(object)
    if exact_duration:
        a = pd.Series(
            [Fraction(v) if pd.notna(v) else pd.NA for v in a], dtype=object, index=a.index
        )
        b = pd.Series(
            [Fraction(v) if pd.notna(v) else pd.NA for v in b], dtype=object, index=b.index
        )
    if any(isinstance(value, float) for value in a) or any(isinstance(value, float) for value in b):
        a = a.map(lambda value: float(value) if isinstance(value, Decimal) else value).astype(
            object
        )
        b = b.map(lambda value: float(value) if isinstance(value, Decimal) else value).astype(
            object
        )
    if operation == "divide":
        zero = b.notna() & ((b == 0) | (b == timedelta(0))).fillna(False)
        if zero_denominator == "error" and (a.notna() & zero).any():
            numeric_failure("a nonzero denominator", "metric.divide(zero_denominator='error')")
        b = b.mask(zero, pd.NA)
    try:
        with localcontext() as context:
            context.prec = 120
            if operation == "add":
                result = a + b
            elif operation == "subtract":
                result = a - b
            elif operation == "multiply":
                result = a * b
            elif operation == "divide":
                result = a / b
            else:
                numeric_failure("an admitted nullable arithmetic operator", operation)
    except (ArithmeticError, TypeError, ValueError) as error:
        numeric_failure("valid numeric arithmetic", f"{operation}: {error}")
    result = result.astype(object).mask(a.isna() | b.isna(), pd.NA)
    if any(isinstance(value, int) and not -(2**63) <= value < 2**63 for value in result):
        numeric_failure("signed int64 arithmetic", f"{operation}: integer overflow")
    if any(
        (isinstance(value, float) and not math.isfinite(value))
        or (isinstance(value, Decimal) and not value.is_finite())
        for value in result
    ):
        numeric_failure("finite numeric output", operation)
    return result


def finish_float64(values: pd.Series, operation: str) -> pd.Series:
    """Convert an exact completed expression once to its declared float carrier."""
    from marivo.semantic._numeric_lowering import numeric_failure

    finished: list[float] = []
    for value in values:
        if pd.isna(value):
            finished.append(math.nan)
            continue
        try:
            converted = float(value)
        except (ValueError, TypeError, OverflowError):
            numeric_failure("a finite float64 output", f"{operation}: {value!r}")
        if not math.isfinite(converted):
            numeric_failure("a finite float64 output", f"{operation}: output overflow")
        finished.append(converted)
    return pd.Series(finished, dtype="float64", index=values.index)


def duration_arithmetic(
    operation: str,
    left: pd.Series,
    right: pd.Series,
    *,
    left_type: DataType,
    right_type: DataType,
    output: DurationDataType | ScalarDataType,
    zero_denominator: str,
) -> pd.Series:
    """Execute already typed Duration arithmetic in its exact original ticks."""
    from marivo.semantic._numeric_lowering import numeric_failure

    def operand(series: pd.Series, dtype: DataType) -> pd.Series:
        if not isinstance(dtype, DurationDataType):
            return series
        ticks = pa.array(series, type=pa.duration(dtype.unit), from_pandas=True).cast(pa.int64())
        return pd.Series(
            [int(value) if value is not None else pd.NA for value in ticks.to_pylist()],
            dtype=object,
            index=series.index,
        )

    result = nullable_arithmetic(
        operation,
        operand(left, left_type),
        operand(right, right_type),
        zero_denominator=zero_denominator,
        exact_duration=True,
    )
    if not isinstance(output, DurationDataType):
        return finish_float64(result, "Duration ratio")
    finished: list[int | None] = []
    try:
        for value in result:
            if pd.isna(value):
                finished.append(None)
            else:
                exact = Fraction(value)
                tick = finish_division(
                    exact.numerator, exact.denominator, DurationType(output.unit)
                )
                assert isinstance(tick, int)
                finished.append(tick)
    except (ArithmeticError, ValueError, TypeError) as error:
        numeric_failure("exact signed int64 Duration arithmetic", str(error))
    return pd.Series(
        pd.arrays.ArrowExtensionArray(
            pa.array(finished, type=pa.int64()).cast(pa.duration(output.unit))
        ),
        index=result.index,
    )
