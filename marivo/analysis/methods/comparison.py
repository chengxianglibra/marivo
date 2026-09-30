"""Typed paired arithmetic with exact intermediates and one final rounding."""

from __future__ import annotations

import math
from decimal import Decimal
from fractions import Fraction
from typing import Literal, TypeAlias

from marivo.analysis.core.model import Defined, Undefined
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType, ValueType

ComparisonMethod: TypeAlias = Literal["difference", "relative_change", "ratio"]
Number: TypeAlias = int | float | Decimal


def output_type(method: ComparisonMethod, left: ValueType, right: ValueType) -> ValueType:
    """Resolve homogeneous operand families without implicit scale/unit conversion."""
    if method not in ("difference", "relative_change", "ratio"):
        raise ValueError("unknown comparison method")
    if isinstance(left, DecimalType) and isinstance(right, DecimalType):
        if method == "difference" and left.scale != right.scale:
            raise ValueError("absolute difference requires equal Decimal scales")
        return DecimalType(
            38, left.scale if method == "difference" else max(left.scale, right.scale, 6)
        )
    if left != right or not (
        isinstance(left, DurationType) or left in (ScalarType("int64"), ScalarType("float64"))
    ):
        raise ValueError("comparison requires homogeneous numeric types and exact Duration units")
    return left if method == "difference" else ScalarType("float64")


def exact_value(value: object, physical: ValueType) -> Fraction:
    """Validate a represented numeric operand and retain it without float coercion."""
    if isinstance(physical, DecimalType):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("expected a finite Decimal operand")
        exact = Fraction(value)
        if (exact * 10**physical.scale).denominator != 1:
            raise ValueError("Decimal operand exceeds its declared scale")
        if abs(exact) >= 10 ** (physical.precision - physical.scale):
            raise OverflowError("Decimal operand exceeds its declared precision")
        return exact
    if isinstance(physical, DurationType) or physical == ScalarType("int64"):
        if type(value) is not int:
            raise ValueError("expected exact int64 or Duration ticks, excluding bool")
        if not -(2**63) <= value < 2**63:
            raise OverflowError("operand exceeds int64 storage")
        return Fraction(value)
    if physical == ScalarType("float64") and type(value) is float and math.isfinite(value):
        return Fraction(value)
    raise ValueError("expected a finite float64 operand")


def _finish(exact: Fraction, physical: ValueType) -> Number:
    if isinstance(physical, DecimalType):
        coefficient = round(exact * 10**physical.scale)
        if abs(coefficient) >= 10**physical.precision:
            raise OverflowError("comparison exceeds Decimal output precision")
        # Tuple construction is exact even under a caller's small decimal context.
        digits = tuple(int(digit) for digit in str(abs(coefficient)))
        return Decimal((int(coefficient < 0), digits, -physical.scale))
    if isinstance(physical, DurationType) or physical == ScalarType("int64"):
        if exact.denominator != 1 or not -(2**63) <= exact.numerator < 2**63:
            raise OverflowError("comparison exceeds int64 output storage")
        return exact.numerator
    value = float(exact)
    if not math.isfinite(value):
        raise OverflowError("comparison exceeds finite float64 output")
    return value


def evaluate(
    method: ComparisonMethod,
    left: object,
    right: object,
    left_type: ValueType,
    right_type: ValueType,
) -> Defined | Undefined:
    """Evaluate two present Defined operands; missing-side policy belongs to pairing."""
    physical = output_type(method, left_type, right_type)
    first, second = exact_value(left, left_type), exact_value(right, right_type)
    if method == "difference":
        result = first - second
    elif second == 0:
        return Undefined("zero_baseline" if method == "relative_change" else "zero_denominator")
    elif method == "relative_change":
        result = (first - second) / abs(second)
    else:
        result = first / second
    return Defined(_finish(result, physical))


def roundoff(value: float) -> float:
    """Return the R5 represented-float rounding envelope."""
    return 1e-12 * (1.0 + abs(value))


def propagated_error(
    method: ComparisonMethod,
    left: object,
    right: object,
    result: object,
    left_error: float,
    right_error: float,
) -> float:
    """Propagate retained bounds; a nonzero denominator interval must exclude zero."""
    if any(not math.isfinite(error) or error < 0 for error in (left_error, right_error)):
        raise ValueError("operand error bounds must be finite and nonnegative")
    if result is None or type(result) is not float:
        return 0.0
    if method == "difference":
        bound = left_error + right_error + roundoff(result)
    else:
        if not isinstance(right, (int, float, Decimal)):
            raise ValueError("missing exact denominator")
        magnitude = abs(float(right))
        if magnitude <= right_error:
            raise ValueError("nonzero denominator interval spans zero")
        numerator_error = left_error + right_error if method == "relative_change" else left_error
        bound = (numerator_error + abs(result) * right_error) / (
            magnitude - right_error
        ) + roundoff(result)
    if not math.isfinite(bound):
        raise OverflowError("comparison error bound is nonfinite")
    return bound
