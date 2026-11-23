"""Primitive checked numeric operations shared by recipe lowering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.exceptions import IbisTypeError

from marivo._kernel.numeric_types import bind_numeric_literal, decimal_result_type
from marivo.semantic.errors import ErrorKind, SemanticExpressionError


@dataclass(frozen=True, slots=True, eq=False)
class NumericCheck:
    """A temporary boolean violation expression, consumed before publication."""

    violation: ir.BooleanValue
    expected: str
    received: str


@dataclass(frozen=True, slots=True, eq=False)
class NumericValue:
    value: ir.Value
    checks: tuple[NumericCheck, ...] = ()


def numeric_failure(expected: str, received: str) -> NoReturn:

    action = "Repair the input values to satisfy the expected numeric/type constraints, then retry."
    if expected == "non-NULL internal calendar sample time":
        action = "Filter the named TimeField with where=sample_field.notnull(), or correct its source values."
    elif "non-NA" in expected:
        action = "Supply non-NA inputs, or explicitly select nulls='ignore' on this reducer."
    elif "native" in expected or "demanded numeric" in expected:
        action = (
            "Inspect the native numeric error and operand dtypes. If a wider carrier is intended, "
            "author .cast(ibis.dtype('decimal(38,0)')) in the Entity expression before reduction, "
            "or rescale the inputs to the intended unit. Rebuild the expression and retry; "
            "Marivo does not widen or switch kernels automatically."
        )
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        message="The numeric operation cannot produce an admitted nullable value.",
        expected=expected,
        received=received,
        action=action,
    )


def numeric_binary(operation: str, left: NumericValue, right: NumericValue) -> NumericValue:
    """Emit arithmetic with the denominator masked before division."""
    a, b = bind_numeric_literal(left.value), bind_numeric_literal(right.value)
    if a.type().is_floating() and b.type().is_decimal():
        b = b.cast("float64")
    elif b.type().is_floating() and a.type().is_decimal():
        a = a.cast("float64")
    checks = (*left.checks, *right.checks)
    if operation == "divide":
        zero = b == 0
        safe = zero.ifelse(ibis.null().cast(b.type().copy(nullable=True)), b)
        # Cast operands before division: Trino otherwise truncates an integer
        # quotient before the explicit float64 result cast can preserve it.
        a, safe = a.cast("float64"), safe.cast("float64")
        # Preserve the float64 result boundary in SQL. In particular, ClickHouse
        # can rewrite AVG(x / NULL) and fail while extracting the NULL constant;
        # AVG(CAST(x / NULL AS Float64)) keeps the nullable division intact.
        value = force_cast(a / safe, dt.float64)
    elif operation == "add":
        value = add_numeric(a, b)
    elif operation == "subtract":
        value = _binary_numeric("subtract", a, b)
    elif operation == "multiply":
        value = multiply_numeric(a, b)
    else:
        numeric_failure("add/subtract/multiply/divide", operation)
    return NumericValue(value, checks)


def force_cast(value: ir.Value, dtype: dt.DataType) -> ir.Value:
    """Retain a result CAST in SQL even when Ibis infers the same type."""
    return ops.Cast(value, to=dtype).to_expr()


def _binary_numeric(operation: str, left: ir.Value, right: ir.Value) -> ir.Value:
    a, b = left.type(), right.type()
    if a.is_floating() and b.is_decimal():
        right = right.cast(a)
    elif b.is_floating() and a.is_decimal():
        left = left.cast(b)
    target = decimal_result_type(operation, a, b)
    if target is not None:
        if a.is_decimal() and b.is_integer():
            precision = len(str(2 ** (b.nbytes * 8 - int(b.is_signed_integer())) - 1))
            right = right.cast(dt.Decimal(precision, 0))
        elif b.is_decimal() and a.is_integer():
            precision = len(str(2 ** (a.nbytes * 8 - int(a.is_signed_integer())) - 1))
            left = left.cast(dt.Decimal(precision, 0))
    try:
        result = (
            left * right
            if operation == "multiply"
            else left - right
            if operation == "subtract"
            else left + right
        )
    except IbisTypeError:
        if target is None:
            raise
        # Ibis requires comparable Decimal metadata for some mixed p/s pairs.
        scale = max(
            a.scale or 0 if isinstance(a, dt.Decimal) else 0,
            b.scale or 0 if isinstance(b, dt.Decimal) else 0,
        )
        common = dt.Decimal(38, scale)
        left, right = left.cast(common), right.cast(common)
        result = (
            left * right
            if operation == "multiply"
            else left - right
            if operation == "subtract"
            else left + right
        )
    if target is not None:
        left_scale = (a.scale or 0) if isinstance(a, dt.Decimal) else 0
        right_scale = (b.scale or 0) if isinstance(b, dt.Decimal) else 0
        scale = (
            left_scale + right_scale if operation == "multiply" else max(left_scale, right_scale)
        )
        if target.scale < scale:
            result = result.round(target.scale)
        return force_cast(result, target)
    return result


def multiply_numeric(left: ir.Value, right: ir.Value) -> ir.Value:
    """Multiply with a bound Decimal result cast and native backend arithmetic."""
    return _binary_numeric("multiply", left, right)


def add_numeric(left: ir.Value, right: ir.Value) -> ir.Value:
    """Add with a bound Decimal result cast and native backend arithmetic."""
    return _binary_numeric("add", left, right)
