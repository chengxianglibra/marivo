"""Admit and lower finite scalar operations for native Query fusion."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from math import isfinite

import ibis
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.query import (
    CoordinateSlot,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.compiler.source_predicates import string_bytes as _string_bytes
from marivo.analysis.core.value_types import expression_value
from marivo.semantic.spi import NumericValue, numeric_binary


def _decimal_shape(value: Decimal) -> tuple[int, int] | None:
    if not value.is_finite():
        return None
    parts = value.as_tuple()
    exponent = parts.exponent
    assert isinstance(exponent, int)
    scale = max(-exponent, 0)
    precision = max(len(parts.digits) + max(exponent, 0), scale, 1)
    return (precision, scale) if precision <= 38 else None


def _predicate_value(
    value: ValueExpression,
    values: tuple[ir.Value, ...],
    coordinates: tuple[ir.Value, ...],
    provider: str,
) -> ir.Value:
    if isinstance(value, ValueLiteral) and isinstance(value.value, Decimal):
        shape = _decimal_shape(value.value)
        assert shape is not None
        return ibis.literal(value.value, type=f"decimal({shape[0]},{shape[1]})")
    if isinstance(value, ValueBinary):
        left = _predicate_value(value.left, values, coordinates, provider)
        right = _predicate_value(value.right, values, coordinates, provider)
        if (
            value.operation in ("eq", "ne", "lt", "le", "gt", "ge")
            and left.type().is_string()
            and right.type().is_string()
        ):
            left, right = _string_bytes(left, provider), _string_bytes(right, provider)
        return expression_value(
            ValueBinary(value.operation, ValueSlot(0), ValueSlot(1)), (left, right), ()
        )
    if isinstance(value, ValueUnary):
        operand = _predicate_value(value.operand, values, coordinates, provider)
        return expression_value(ValueUnary(value.operation, ValueSlot(0)), (operand,), ())
    return expression_value(value, values, coordinates)


def _checked_value(
    value: ValueExpression,
    values: tuple[ir.Value, ...],
    coordinates: tuple[ir.Value, ...],
    provider: str,
) -> NumericValue | str:
    """Lower a finite Column expression without discarding demanded checks."""
    if isinstance(value, ValueBinary):
        left = _checked_value(value.left, values, coordinates, provider)
        right = _checked_value(value.right, values, coordinates, provider)
        if isinstance(left, str):
            return left
        if isinstance(right, str):
            return right
        if value.operation in ("add", "sub", "mul", "div"):
            if not (left.value.type().is_numeric() and right.value.type().is_numeric()):
                return "Column arithmetic retains a nonnumeric or Duration finish"
            result = numeric_binary(
                {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                    value.operation
                ],
                left,
                right,
            )
            return result
        expression = _predicate_value(
            replace(value, left=ValueSlot(0), right=ValueSlot(1)),
            (left.value, right.value),
            (),
            provider,
        )
        return NumericValue(expression, (*left.checks, *right.checks))
    if isinstance(value, ValueUnary):
        operand = _checked_value(value.operand, values, coordinates, provider)
        if isinstance(operand, str):
            return operand
        if value.operation in ("neg", "abs") and (
            not operand.value.type().is_numeric() or operand.value.type().is_integer()
        ):
            return "integer unary Columns retain their existing local normalization"
        return NumericValue(
            expression_value(replace(value, operand=ValueSlot(0)), (operand.value,), ()),
            operand.checks,
        )
    return NumericValue(_predicate_value(value, values, coordinates, provider))


def _safe_expression(
    value: ValueExpression, *, native_float: bool = False, native_arithmetic: bool = False
) -> bool:
    if isinstance(value, (ValueSlot, CoordinateSlot, ValueLiteral)):
        return not isinstance(value, ValueLiteral) or not (
            isinstance(value.value, timedelta)
            or (isinstance(value.value, Decimal) and _decimal_shape(value.value) is None)
            or (isinstance(value.value, float) and not (native_float and isfinite(value.value)))
            or (isinstance(value.value, str) and "\x00" in value.value)
            or (isinstance(value.value, pd.Timestamp) and value.value.nanosecond != 0)
        )
    if isinstance(value, ValueUnary):
        return (value.operation in ("not", "is_null") or native_arithmetic) and _safe_expression(
            value.operand, native_float=native_float, native_arithmetic=native_arithmetic
        )
    if isinstance(value, ValueBinary):
        return (
            (
                value.operation in ("eq", "ne", "lt", "le", "gt", "ge", "and", "or")
                or native_arithmetic
            )
            and _safe_expression(
                value.left, native_float=native_float, native_arithmetic=native_arithmetic
            )
            and _safe_expression(
                value.right, native_float=native_float, native_arithmetic=native_arithmetic
            )
        )
    return False
