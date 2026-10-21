"""Shared literal classification and finite nullable Ibis value type adapters."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo._definition.query import (
    CoordinateSlot,
    PresenceSlot,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo._definition.types import LiteralValue
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_structure import ValueKind
from marivo.analysis.core.model import reject
from marivo.semantic._numeric_lowering import numeric_failure


def literal_kind(value: LiteralValue) -> ValueKind:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float, Decimal)):
        return "numeric"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, timedelta):
        return "duration"
    return "unresolved"


def ibis_type(dtype: DataType) -> dt.DataType:
    if isinstance(dtype, ScalarDataType):
        return dt.dtype(dtype.name)
    if isinstance(dtype, DecimalDataType):
        return dt.Decimal(dtype.precision, dtype.scale)
    if isinstance(dtype, TimestampDataType):
        return dt.Timestamp(
            timezone=dtype.timezone, scale={"s": 0, "ms": 3, "us": 6, "ns": 9}[dtype.unit]
        )
    return dt.int64


def data_type(dtype: dt.DataType) -> DataType:
    if dtype.is_signed_integer():
        return ScalarDataType("int64")
    if dtype.is_floating():
        return ScalarDataType("float64")
    if isinstance(dtype, dt.Decimal):
        return DecimalDataType(dtype.precision or 38, dtype.scale or 0)
    if isinstance(dtype, dt.Timestamp):
        units: dict[int, Literal["s", "ms", "us", "ns"]] = {0: "s", 3: "ms", 6: "us", 9: "ns"}
        unit = units.get(dtype.scale or 0)
        if unit in ("s", "ms", "us", "ns"):
            return TimestampDataType(unit, dtype.timezone)
    if dtype.is_date():
        return ScalarDataType("date")
    if dtype.is_boolean():
        return ScalarDataType("boolean")
    if dtype.is_string():
        return ScalarDataType("string")
    if isinstance(dtype, dt.Interval) and dtype.unit.short in ("s", "ms", "us", "ns"):
        return DurationDataType(dtype.unit.short)
    numeric_failure("a supported nullable output type", str(dtype))


def expression_value(
    expression: ValueExpression,
    values: tuple[ir.Value, ...],
    keys: tuple[ir.Value, ...],
) -> ir.Value:
    """Resolve only the finite result grammar over typed scalar placeholders."""
    if isinstance(expression, ValueSlot):
        return values[expression.index]
    if isinstance(expression, CoordinateSlot):
        return keys[expression.index]
    if isinstance(expression, PresenceSlot):
        return ibis.null().cast("boolean")
    if isinstance(expression, ValueLiteral):
        if isinstance(expression.value, timedelta) and expression.value == timedelta(0):
            return ibis.interval(0, unit="us")
        if type(expression.value) is int and not -(2**63) <= expression.value < 2**63:
            from decimal import Decimal

            digits = len(str(abs(expression.value)))
            if digits > 38:
                reject(
                    "a losslessly representable integer or Decimal literal",
                    repr(expression.value),
                    "Use a value within decimal(38,0) or int64.",
                    "Query.prepare",
                )
            return ibis.literal(Decimal(expression.value), type=f"decimal({digits},0)")
        from marivo._numeric_types import bind_numeric_literal

        return bind_numeric_literal(ibis.literal(expression.value))
    if isinstance(expression, ValueUnary):
        value = expression_value(expression.operand, values, keys)
        if expression.operation == "is_null":
            return value.isnull()
        if expression.operation == "not":
            return ~value
        if expression.operation == "abs":
            return value.abs()
        return -value
    left = expression_value(expression.left, values, keys)
    right = expression_value(expression.right, values, keys)
    if expression.operation in ("add", "sub", "mul", "div"):
        if not all(
            value.type().is_numeric() or value.type().is_interval() for value in (left, right)
        ):
            reject(
                "numeric or Duration arithmetic",
                repr((left.type(), right.type())),
                "Use arithmetic on compatible numeric Columns.",
                "Query.prepare",
            )
        from marivo.semantic._numeric_lowering import NumericValue, numeric_binary

        return numeric_binary(
            {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                expression.operation
            ],
            NumericValue(left),
            NumericValue(right),
            zero_denominator=expression.zero_denominator,
        ).value
    if expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
        left_type, right_type = left.type(), right.type()
        temporal = (
            left_type.is_date()
            or left_type.is_timestamp()
            or right_type.is_date()
            or right_type.is_timestamp()
        )
        if temporal and not (
            (left_type.is_date() and right_type.is_date())
            or (left_type.is_timestamp() and right_type.is_timestamp())
        ):
            reject(
                "matching temporal value kinds",
                f"{left_type}, {right_type}",
                "Compare dates with dates and timezone-aware timestamps with timestamps.",
                "Query.prepare",
            )
    if expression.operation == "add":
        return left + right
    if expression.operation == "sub":
        return left - right
    if expression.operation == "mul":
        return left * right
    if expression.operation == "div":
        return left / right
    if expression.operation == "eq":
        return left == right
    if expression.operation == "ne":
        return left != right
    if expression.operation == "lt":
        return left < right
    if expression.operation == "le":
        return left <= right
    if expression.operation == "gt":
        return left > right
    if expression.operation == "ge":
        return left >= right
    if expression.operation == "and":
        return left & right
    return left | right
