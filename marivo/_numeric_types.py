"""Pure Ibis numeric literal binding and bounded Decimal result metadata."""

from __future__ import annotations

from decimal import Decimal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir


def exact_unsigned(value: ir.Value) -> ir.Value:
    """Represent the complete UInt64 domain without signed overflow or floating loss."""
    if value.type().is_unsigned_integer() and value.type().nbytes == 8:
        return value.cast(dt.Decimal(20, 0))
    return value


def bind_numeric_literal(value: ir.Value) -> ir.Value:
    """Bind unspecified Ibis Decimal literals from their literal precision/scale."""
    node = value.op()
    if (
        isinstance(node, ops.Literal)
        and isinstance(node.value, Decimal)
        and isinstance(node.dtype, dt.Decimal)
        and node.dtype.precision is None
    ):
        exponent = node.value.as_tuple().exponent
        assert isinstance(exponent, int)
        scale = max(0, -exponent)
        precision = max(1, max(0, node.value.adjusted() + 1) + scale)
        return ibis.literal(node.value, type=dt.Decimal(precision, scale))
    return value


def decimal_result_type(operation: str, a: dt.DataType, b: dt.DataType) -> dt.Decimal | None:
    """Infer bound Decimal add/subtract/multiply metadata, capped at precision 38."""
    if a.is_floating() or b.is_floating() or not (a.is_decimal() or b.is_decimal()):
        return None

    def parts(dtype: dt.DataType) -> tuple[int, int]:
        if isinstance(dtype, dt.Decimal):
            return dtype.precision or 38, dtype.scale or 0
        # Use the bound integer width, including literal types.
        return len(str(2 ** (dtype.nbytes * 8 - int(dtype.is_signed_integer())) - 1)), 0

    p1, s1 = parts(a)
    p2, s2 = parts(b)
    scale = s1 + s2 if operation == "multiply" else max(s1, s2)
    precision = p1 + p2 if operation == "multiply" else max(p1 - s1, p2 - s2) + scale + 1
    return dt.Decimal(min(38, precision), max(0, scale - max(0, precision - 38)))
