"""Ibis numeric expressions and their retained transport types."""

from __future__ import annotations

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.exceptions import IbisTypeError

from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType, ValueType


def multiply(left: ir.Value, right: ir.Value) -> ir.Value:
    """Use native multiplication with necessary Decimal/float and scale adaptation."""
    a, b = left.type(), right.type()
    if a.is_floating() and isinstance(b, dt.Decimal):
        right = right.cast(a)
    elif b.is_floating() and isinstance(a, dt.Decimal):
        left = left.cast(b)
    try:
        return left * right
    except IbisTypeError:
        a, b = left.type(), right.type()
        if not isinstance(a, dt.Decimal) or not isinstance(b, dt.Decimal):
            raise
        scale = max(a.scale or 0, b.scale or 0)
        precision = (
            max((a.precision or 38) - (a.scale or 0), (b.precision or 38) - (b.scale or 0)) + scale
        )
        common = dt.Decimal(min(38, precision), scale)
        return left.cast(common) * right.cast(common)


def weighted_state_types(amount: str, weight: str) -> tuple[tuple[str, str], ...]:
    """Infer numerator and denominator independently through the same Ibis operations."""
    table = ibis.table(
        {"amount": "int64" if amount.startswith("interval(") else amount, "weight": weight}
    )
    product = multiply(table.amount, table.weight)
    return (
        ("weighted_numerator", str(product.sum().type())),
        ("weight_sum", str(table.weight.sum().type())),
        ("absolute_weighted_numerator", str(product.sum().type())),
        ("absolute_weight_sum", str(table.weight.sum().type())),
    )


def transport_cast(value: ir.Value, dtype: str) -> ir.Value:
    """Emit an output cast even when Ibis and the engine infer different types."""
    return ops.Cast(value, to=dt.dtype(dtype)).to_expr()


def adapt_measure(value: ir.Value, declared: str) -> ir.Value:
    """Widen narrow numeric measures at the existing graph carrier boundary."""
    dtype = value.type()
    if (declared == "int64" and dtype.is_signed_integer() and dtype.nbytes < 8) or (
        declared == "float64" and dtype.is_floating() and dtype.nbytes < 8
    ):
        return value.cast(declared)
    return value


def add(left: ir.Value, right: ir.Value) -> ir.Value:
    """Add numeric components with the minimal Ibis Decimal adaptation."""
    a, b = left.type(), right.type()
    if a.is_floating() and isinstance(b, dt.Decimal):
        right = right.cast(a)
    elif b.is_floating() and isinstance(a, dt.Decimal):
        left = left.cast(b)
    try:
        return left + right
    except IbisTypeError:
        if not isinstance(a, dt.Decimal) or not isinstance(b, dt.Decimal):
            raise
        scale = max(a.scale or 0, b.scale or 0)
        precision = (
            max((a.precision or 38) - (a.scale or 0), (b.precision or 38) - (b.scale or 0)) + scale
        )
        common = dt.Decimal(min(38, precision), scale)
        return left.cast(common) + right.cast(common)


def linear_type(types: tuple[ValueType, ...]) -> ValueType:
    """Bind native numeric output while keeping Duration units separate."""
    if all(isinstance(t, DurationType) for t in types) and len(set(types)) == 1:
        return types[0]
    if any(
        isinstance(t, DurationType) or t.name in ("boolean", "string", "date", "timestamp")
        for t in types
    ):
        raise ValueError("linear requires numeric components or identical Duration units")
    table = ibis.table({f"v{i}": t.name for i, t in enumerate(types)})
    value = table.v0
    for i in range(1, len(types)):
        value = add(value, table[f"v{i}"])
    dtype = value.type()
    if isinstance(dtype, dt.Decimal):
        return DecimalType(dtype.precision or 38, dtype.scale or 0)
    return ScalarType("float64" if dtype.is_floating() else "int64")
