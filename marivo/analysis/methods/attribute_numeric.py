"""Typed arithmetic and roundoff for Attribute component and report values."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from math import fsum, isfinite, ulp
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.attribute import _type
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.methods.numeric_state import (
    duration_arithmetic,
    finish_decimal,
    finish_division,
    finish_float64,
    nullable_arithmetic,
)
from marivo.analysis.methods.physical import DecimalType, DurationType
from marivo.analysis.model.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.model.method_values import FrameMethodValue
from marivo.semantic.spi import numeric_failure, ratio_unit


@dataclass(frozen=True, slots=True)
class _Value:
    values: pd.Series
    column: DataColumn
    roundoff: pd.Series | None = None


def _with_roundoff(value: _Value) -> _Value:
    """Seed effect-only error propagation from the actual Float64 operands."""
    if value.column.dtype != ScalarDataType("float64") or value.roundoff is not None:
        return value
    return replace(
        value,
        roundoff=pd.Series(
            [0.0 if pd.isna(cell) else ulp(float(cell)) for cell in value.values], dtype=float
        ),
    )


def _operation_roundoff(
    operation: Literal["add", "sub", "mul", "div"],
    a: _Value,
    b: _Value,
    values: pd.Series,
    dtype: DataType,
) -> pd.Series | None:
    """Propagate invocation-only effect bounds through the existing ordered kernel."""
    if dtype != ScalarDataType("float64") or (a.roundoff is None and b.roundoff is None):
        return None
    errors: list[float] = []
    for index, (left, right, result) in enumerate(zip(a.values, b.values, values, strict=True)):
        if pd.isna(left) or pd.isna(right) or pd.isna(result):
            errors.append(0.0)
            continue
        x, y, z = float(left), float(right), float(result)
        dx = 0.0 if a.roundoff is None else float(a.roundoff.iloc[index])
        dy = 0.0 if b.roundoff is None else float(b.roundoff.iloc[index])
        try:
            if operation in ("add", "sub"):
                error = fsum((dx, dy, ulp(z)))
            elif operation == "mul":
                error = fsum((abs(y) * dx, abs(x) * dy, dx * dy, ulp(z)))
            else:
                # This order avoids overflowing y*y or an unneeded x/y factor.
                numerator_error = dx / abs(y)
                denominator_error = abs(z) * (dy / abs(y))
                error = fsum((numerator_error, denominator_error, ulp(z)))
        except ArithmeticError as failure:
            numeric_failure("a finite Attribute effect rounding budget", str(failure))
        if not isfinite(error):
            numeric_failure("a finite Attribute effect rounding budget", repr(error))
        errors.append(error)
    return pd.Series(errors, dtype=float)


def _consistent(
    cells: list[int | float | Decimal],
    expected: int | float | Decimal,
    column: DataColumn,
    *,
    rounded: bool = False,
    roundoff: float = 0.0,
) -> bool:
    """Compare exact masses, ULP-bounded floats, or explicitly rounded typed effects."""
    if isinstance(expected, float) or any(isinstance(cell, float) for cell in cells):
        try:
            actual = fsum(float(cell) for cell in cells)
            magnitude = max(abs(float(expected)), fsum(abs(float(cell)) for cell in cells))
            tolerance = 16 * (len(cells) + 1) * fsum((ulp(magnitude), roundoff))
        except ArithmeticError as failure:
            numeric_failure("a finite Attribute component/effect numeric budget", str(failure))
        if not isfinite(tolerance):
            numeric_failure("a finite Attribute component/effect numeric budget", repr(tolerance))
        return isfinite(actual) and abs(actual - float(expected)) <= tolerance
    with localcontext() as decimal_context:
        decimal_context.prec = 120
        actual_exact = sum((Decimal(cell) for cell in cells), Decimal(0))
        tolerance_exact = (
            Decimal(len(cells) + 1) / 2
            if rounded and isinstance(column.dtype, DurationDataType)
            else Decimal(len(cells) + 1) * Decimal(1).scaleb(-column.dtype.scale) / 2
            if rounded and isinstance(column.dtype, DecimalDataType)
            else Decimal(0)
        )
        return abs(actual_exact - Decimal(expected)) <= tolerance_exact


def _numeric_binary(
    operation: Literal["add", "sub", "mul", "div"],
    a: _Value,
    b: _Value,
    *,
    output: DataType | None = None,
) -> _Value:
    dtype = output or _type(operation, a.column, b.column)
    names = {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}
    if isinstance(a.column.dtype, DurationDataType) or isinstance(b.column.dtype, DurationDataType):
        assert isinstance(dtype, (DurationDataType, ScalarDataType))
        values = duration_arithmetic(
            names[operation],
            a.values,
            b.values,
            left_type=a.column.dtype,
            right_type=b.column.dtype,
            output=dtype,
        )
    else:
        values = nullable_arithmetic(names[operation], a.values, b.values)
        if operation == "div" and isinstance(dtype, DurationDataType):
            try:
                values = pd.Series(
                    [
                        None
                        if pd.isna(x) or pd.isna(y) or y == 0
                        else finish_division(x, y, DurationType(dtype.unit))
                        for x, y in zip(a.values, b.values, strict=True)
                    ],
                    dtype=object,
                )
            except (ArithmeticError, TypeError, ValueError) as error:
                numeric_failure("exact signed int64 Duration arithmetic", str(error))
        if operation == "div" and isinstance(dtype, DecimalDataType):
            finished: list[int | float | Decimal | None] = []
            try:
                for x, y in zip(a.values, b.values, strict=True):
                    finished.append(
                        None
                        if pd.isna(x) or pd.isna(y) or y == 0
                        else finish_division(x, y, DecimalType(dtype.precision, dtype.scale))
                    )
            except (ArithmeticError, TypeError, ValueError) as error:
                numeric_failure("valid typed Decimal division", str(error))
            values = pd.Series(finished, dtype=object)
        if isinstance(dtype, DecimalDataType) and operation != "div":
            values = finish_decimal(values, dtype.precision, dtype.scale)
        if dtype == ScalarDataType("float64"):
            values = finish_float64(values, "Attribute contribution")
    column = replace(
        a.column,
        dtype=dtype,
        nullable=True,
        unit=ratio_unit(a.column.unit, b.column.unit) if operation == "div" else a.column.unit,
    )
    try:
        exact = pa.array(values, type=stage_arrow_type(dtype), from_pandas=True)
    except (pa.ArrowException, ArithmeticError, TypeError, ValueError) as error:
        numeric_failure("admitted Attribute numeric output", str(error))
    published = pd.Series(pd.arrays.ArrowExtensionArray(exact))
    return _Value(published, column, _operation_roundoff(operation, a, b, published, dtype))


def capture_method_value(value: _Value, index: int) -> FrameMethodValue:
    if isinstance(value.column.dtype, DurationDataType):
        scalar = (
            pa.array(value.values, type=stage_arrow_type(value.column.dtype), from_pandas=True)
            .cast(pa.int64())[index]
            .as_py()
        )
    else:
        scalar = pa.array(
            value.values, type=stage_arrow_type(value.column.dtype), from_pandas=True
        )[index].as_py()
    return FrameMethodValue(value.column, scalar)
