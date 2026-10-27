"""Typed numeric finishing and nullable arithmetic for fixed relations."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from fractions import Fraction
from typing import TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.data_types import DataType, DurationDataType, ScalarDataType
from marivo.analysis.core.data_validation import typed_null_mask
from marivo.analysis.methods.physical import DecimalType, DurationType, ValueType

Number: TypeAlias = int | float | Decimal


def _decimal_context() -> Context:
    """Finish method-declared Decimal outputs; ordinary arithmetic uses its kernel."""
    return Context(
        prec=120,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def sum_values(values: Sequence[object], physical: pa.DataType) -> Number:
    """Use Arrow's native accumulator without widening or overflow checks."""
    result: object = pc.sum(pa.array(values, type=physical), min_count=0).as_py()
    if isinstance(result, (int, float, Decimal)):
        return result
    raise TypeError("native sum did not return a numeric scalar")


def finish_decimal(values: pd.Series, precision: int, scale: int) -> pd.Series:
    """Round once with ties away from zero, then safely admit the target carrier.

    Decimal quantize avoids Arrow's input-precision carry limit, including p=38.
    """
    from marivo.semantic._numeric_lowering import numeric_failure

    missing = typed_null_mask(values)
    try:
        with localcontext(_decimal_context()):
            rounded = [
                None
                if absent
                else Decimal(str(value)).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
                for value, absent in zip(values, missing, strict=True)
            ]
        array = pa.array(rounded).cast(pa.decimal128(precision, scale), safe=True)
    except (pa.ArrowException, ArithmeticError, TypeError, ValueError) as error:
        numeric_failure(f"a representable Decimal({precision},{scale}) result", str(error))
    return pd.Series(pd.arrays.ArrowExtensionArray(array), index=values.index)


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
        with localcontext(_decimal_context()):
            value = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
                Decimal(1).scaleb(-output.scale), rounding=ROUND_HALF_UP
            )
            pa.scalar(value, type=pa.decimal128(output.precision, output.scale))
            return value
    value_float = float(numerator) / float(denominator)
    return value_float


def nullable_arithmetic(
    operation: str,
    left: pd.Series,
    right: pd.Series,
    *,
    exact_duration: bool = False,
) -> pd.Series:
    """Delegate arithmetic to the actual carrier, retaining NULL and zero division."""
    import operator

    from marivo.semantic._numeric_lowering import numeric_failure

    left_missing, right_missing = typed_null_mask(left), typed_null_mask(right)
    a, b = left, right
    if exact_duration:
        a = left.map(lambda value: pd.NA if value is pd.NA or value is None else Fraction(value))
        b = right.map(lambda value: pd.NA if value is pd.NA or value is None else Fraction(value))
    if operation == "divide":
        zero = (b == 0).fillna(False)
        b = b.mask(zero, pd.NA)
        right_missing = right_missing | zero
    try:
        function = {
            "add": operator.add,
            "subtract": operator.sub,
            "multiply": operator.mul,
            "divide": operator.truediv,
        }[operation]
        decimal_operands = any(
            isinstance(series.dtype, pd.ArrowDtype)
            and pa.types.is_decimal(series.dtype.pyarrow_dtype)
            for series in (a, b)
        ) and not any(
            isinstance(series.dtype, pd.ArrowDtype)
            and pa.types.is_floating(series.dtype.pyarrow_dtype)
            for series in (a, b)
        )
        result = (
            pd.Series(
                [
                    pd.NA if missing else function(x, y)
                    for x, y, missing in zip(a, b, left_missing | right_missing, strict=True)
                ],
                index=a.index,
                dtype=object,
            )
            if decimal_operands
            else function(a, b)
        )
    except (ArithmeticError, TypeError, ValueError) as error:
        numeric_failure("native numeric arithmetic", f"{operation}: {error}")
    return result.mask(left_missing | right_missing, pd.NA)


def finish_float64(values: pd.Series, operation: str) -> pd.Series:
    """Convert an exact completed expression once to its declared float carrier."""
    if isinstance(values.dtype, pd.ArrowDtype) and values.dtype.pyarrow_dtype == pa.float64():
        return values

    return pd.Series(
        pd.arrays.ArrowExtensionArray(
            pa.array(
                [
                    None if absent else float(value)
                    for value, absent in zip(values, typed_null_mask(values), strict=True)
                ],
                type=pa.float64(),
            )
        ),
        index=values.index,
    )


def duration_arithmetic(
    operation: str,
    left: pd.Series,
    right: pd.Series,
    *,
    left_type: DataType,
    right_type: DataType,
    output: DurationDataType | ScalarDataType,
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
        exact_duration=True,
    )
    if not isinstance(output, DurationDataType):
        return finish_float64(result, "Duration ratio")
    finished: list[int | None] = []
    try:
        for value, absent in zip(result, typed_null_mask(result), strict=True):
            if absent:
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
