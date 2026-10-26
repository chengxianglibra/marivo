"""Typed numeric finishing and nullable arithmetic for fixed relations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta
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
    """Own local arithmetic policy without inheriting caller traps or bounds."""
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


def checked_sum(values: Sequence[object], physical: pa.DataType) -> Number:
    """Accumulate exactly (or compensated float64), then check the stored type."""
    if pa.types.is_decimal(physical):
        if any(type(value) is not Decimal or not value.is_finite() for value in values):
            raise ValueError("Decimal state has a non-Decimal or nonfinite component")
        with localcontext(_decimal_context()):
            result = sum((value for value in values if isinstance(value, Decimal)), Decimal(0))
        pa.scalar(result, type=physical)
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
    pa.scalar(integer, type=pa.int64())
    return integer


def sum_values(values: Sequence[object], physical: pa.DataType) -> Number:
    """Run one primary sum into the declared widened numeric output."""
    if physical == pa.int64():
        if any(type(value) is not int for value in values):
            raise ValueError("integer sum requires int64 components")
        result = Decimal(sum(value for value in values if type(value) is int))
        pa.scalar(result, type=pa.decimal128(38, 0))
        return result
    if pa.types.is_decimal(physical):
        physical = pa.decimal128(38, physical.scale)
    return checked_sum(values, physical)


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
    if not math.isfinite(value_float):
        raise OverflowError("float64 finish overflow")
    return value_float


def _require_finite_values(values: pd.Series, missing: pd.Series, operation: str) -> None:
    """Validate demanded numeric values before combining nullable input domains."""
    from marivo.semantic._numeric_lowering import numeric_failure

    if any(
        not absent
        and (
            (isinstance(value, float) and not math.isfinite(value))
            or (isinstance(value, Decimal) and not value.is_finite())
        )
        for value, absent in zip(values, missing, strict=True)
    ):
        numeric_failure("finite numeric inputs", operation)


def nullable_arithmetic(
    operation: str,
    left: pd.Series,
    right: pd.Series,
    *,
    exact_duration: bool = False,
) -> pd.Series:
    """Compute over nullable pandas values, masking zeros before division.

    Integer add/subtract/multiply use Arrow checked kernels. Decimal work uses
    the existing wide context; the bound consumer owns result convergence.
    """
    from marivo.semantic._numeric_lowering import numeric_failure

    left_missing, right_missing = typed_null_mask(left), typed_null_mask(right)
    a, b = left.astype(object), right.astype(object)
    for values, missing in ((a, left_missing), (b, right_missing)):
        _require_finite_values(values, missing, operation)
    if exact_duration:
        a = pd.Series(
            [
                Fraction(v) if not absent else pd.NA
                for v, absent in zip(a, left_missing, strict=True)
            ],
            dtype=object,
            index=a.index,
        )
        b = pd.Series(
            [
                Fraction(v) if not absent else pd.NA
                for v, absent in zip(b, right_missing, strict=True)
            ],
            dtype=object,
            index=b.index,
        )
    if any(isinstance(value, float) for value in a) or any(isinstance(value, float) for value in b):
        a = a.map(lambda value: float(value) if isinstance(value, Decimal) else value).astype(
            object
        )
        b = b.map(lambda value: float(value) if isinstance(value, Decimal) else value).astype(
            object
        )
    if operation == "divide":
        zero = ~right_missing & ((b == 0) | (b == timedelta(0))).fillna(False)
        b = b.mask(zero, pd.NA)
        right_missing = right_missing | zero
    try:
        with localcontext(_decimal_context()):
            if (
                operation in ("add", "subtract", "multiply")
                and all(
                    absent or type(value) is int
                    for values, missing in ((a, left_missing), (b, right_missing))
                    for value, absent in zip(values, missing, strict=True)
                )
                and not exact_duration
            ):
                kernel = {
                    "add": pc.add_checked,
                    "subtract": pc.subtract_checked,
                    "multiply": pc.multiply_checked,
                }[operation]
                result = pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        kernel(
                            pa.array(a, type=pa.int64(), from_pandas=True),
                            pa.array(b, type=pa.int64(), from_pandas=True),
                        )
                    ),
                    index=a.index,
                )
            elif operation == "add":
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
    result = result.astype(object).mask(left_missing | right_missing, pd.NA)
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

    finished: list[float | None] = []
    for value, absent in zip(values, typed_null_mask(values), strict=True):
        if absent:
            finished.append(None)
            continue
        try:
            converted = float(value)
        except (ValueError, TypeError, OverflowError):
            numeric_failure("a finite float64 output", f"{operation}: {value!r}")
        if not math.isfinite(converted):
            numeric_failure("a finite float64 output", f"{operation}: output overflow")
        finished.append(converted)
    return pd.Series(
        pd.arrays.ArrowExtensionArray(pa.array(finished, type=pa.float64())), index=values.index
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
