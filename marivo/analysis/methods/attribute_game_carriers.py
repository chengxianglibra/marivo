"""Numeric carrier metadata used by the fixed Attribute state proof."""

from __future__ import annotations

import math
import struct
import sys
from decimal import Clamped, DecimalException, Inexact, Rounded, Subnormal, getcontext
from fractions import Fraction
from typing import TypeAlias

import ibis.expr.operations as ops

IntegerCarrier: TypeAlias = tuple[int, bool]  # Width in bits and signedness.
_ARROW_MATH = (ops.Sqrt, ops.Ln, ops.Log, ops.Log2, ops.Log10, ops.Exp)


def arrow_float32(node: ops.Value) -> bool:
    """Follow Arrow operations whose local output can differ from the Ibis dtype."""
    if isinstance(node, (ops.Alias, ops.Negate, ops.Abs, ops.NullIf, ops.Clip)):
        return arrow_float32(node.arg)
    if isinstance(node, _ARROW_MATH):
        return arrow_float32(node.arg) and (
            not isinstance(node, ops.Log) or node.base is None or arrow_float32(node.base)
        )
    if isinstance(node, ops.IfElse):
        branches = (node.true_expr, node.false_null_expr)
        single_precision = tuple(arrow_float32(branch) for branch in branches)
        return any(single_precision) and all(
            single or branch.dtype.is_integer() or branch.dtype.is_null()
            for single, branch in zip(single_precision, branches, strict=True)
        )
    return bool(node.dtype.is_float32())


def float_limit(width: int) -> Fraction:
    return Fraction(2**128 - 2**104 if width == 4 else sys.float_info.max)


def float32_literal(value: Fraction) -> Fraction:
    (rounded,) = struct.unpack("!f", struct.pack("!f", float(value)))
    return Fraction(rounded)


def outward_float_bounds(
    node: ops.Value, width: int, lower: Fraction, upper: Fraction
) -> tuple[Fraction, Fraction]:
    limit = float_limit(width)
    if width == 4:
        # Enclose native rounding, including subnormals and the additional
        # error from coefficient scaling when Arrow converts a Decimal.
        precision = (
            21 if isinstance(node, (ops.Cast, ops.TryCast)) and node.arg.dtype.is_decimal() else 23
        )
        padding = max(abs(lower), abs(upper)) / 2**precision + Fraction(1, 2**149)
        return max(-limit, lower - padding), min(limit, upper + padding)
    rounded_low = math.nextafter(float(lower), -math.inf)
    rounded_high = math.nextafter(float(upper), math.inf)
    return (
        max(-limit, Fraction(rounded_low)) if math.isfinite(rounded_low) else -limit,
        min(limit, Fraction(rounded_high)) if math.isfinite(rounded_high) else limit,
    )


def declared_integer(node: ops.Value) -> IntegerCarrier:
    return int(node.dtype.nbytes) * 8, bool(node.dtype.is_signed_integer())


def arrow_integer(node: ops.Value) -> IntegerCarrier:
    # Ibis metadata can widen integer arithmetic beyond the actual Arrow
    # checked kernel. Cast and materializing CASE/Coalesce bind a new type.
    if isinstance(node, (ops.Alias, ops.Negate, ops.Abs, ops.NullIf, ops.Clip)):
        return arrow_integer(node.arg)
    if (
        isinstance(node, (ops.Add, ops.Subtract, ops.Multiply))
        and node.left.dtype.is_integer()
        and node.right.dtype.is_integer()
    ):
        return common_integer(arrow_integer(node.left), arrow_integer(node.right))
    if (
        isinstance(node, ops.IfElse)
        and node.true_expr.dtype.is_integer()
        and node.false_null_expr.dtype.is_integer()
    ):
        return common_integer(arrow_integer(node.true_expr), arrow_integer(node.false_null_expr))
    return declared_integer(node)


def common_integer(left: IntegerCarrier, right: IntegerCarrier) -> IntegerCarrier:
    left_bits, left_signed = left
    right_bits, right_signed = right
    if left_signed == right_signed:
        return max(left_bits, right_bits), left_signed
    signed, unsigned = (left_bits, right_bits) if left_signed else (right_bits, left_bits)
    return min(64, max(signed, unsigned * 2)), True


def integer_bounds(carrier: IntegerCarrier) -> tuple[int, int]:
    width, signed = carrier
    return -(2 ** (width - 1)) if signed else 0, 2 ** (width - int(signed)) - 1


def nonzero_quantum(node: ops.Value) -> Fraction | None:
    if node.dtype.is_integer():
        return Fraction(1)
    if node.dtype.is_decimal():
        return Fraction(1, 10 ** (node.dtype.scale or 0))
    if isinstance(node, ops.Cast) and node.to.is_floating():
        quantum = nonzero_quantum(node.arg)
        return None if quantum is None else quantum / 2
    return None


def decimal_operation_bounds(
    node: ops.Add | ops.Subtract | ops.Multiply, lower: Fraction, upper: Fraction
) -> tuple[Fraction, Fraction] | None:
    """Round an outward fixed-point interval using the kernel's Decimal context."""
    scales = tuple(
        dtype.scale or 0 if dtype.is_decimal() else 0
        for dtype in (node.left.dtype, node.right.dtype)
    )
    scale = sum(scales) if isinstance(node, ops.Multiply) else max(scales)
    factor = 10**scale
    minimum, maximum = math.floor(lower * factor), math.ceil(upper * factor)
    context = getcontext().copy()
    if context.Emin > -scale or (
        (context.traps[Inexact] or context.traps[Rounded])
        and len(str(max(abs(minimum), abs(maximum)))) > context.prec
    ):
        # Exact endpoints may have trailing zeroes while an interior lattice
        # value needs every coefficient digit and traps during rounding.
        return None
    try:
        low = context.create_decimal(f"{minimum}e-{scale}")
        high = context.create_decimal(f"{maximum}e-{scale}")
    except DecimalException:
        return None
    if not low.is_finite() or not high.is_finite():
        return None
    return Fraction(low), Fraction(high)


def decimal_round_fits(
    node: ops.Round, lower: Fraction | None, upper: Fraction | None, digits: int
) -> bool:
    if lower is None or upper is None:
        return False
    context = getcontext()
    if digits < (node.arg.dtype.scale or 0) and (context.traps[Inexact] or context.traps[Rounded]):
        return False
    exponent = -digits
    if context.traps[Subnormal] and exponent < context.Emin:
        return False
    if context.clamp and context.traps[Clamped] and exponent > context.Etop():
        return False
    # Decimal quantize requires both the resulting coefficient and its
    # adjusted exponent to fit. The context can round away from zero.
    coefficient = math.ceil(max(abs(lower), abs(upper)) * Fraction(10) ** digits)
    size = len(str(coefficient))
    return (
        size <= context.prec and context.Etiny() <= exponent and exponent + size - 1 <= context.Emax
    )
