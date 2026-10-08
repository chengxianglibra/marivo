"""Exact reference arithmetic, independent of execution routes and source access."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction

from marivo.analysis.core.model import Cell, Defined, Undefined
from marivo.analysis.methods.comparison import evaluate, exact_value, output_type, roundoff
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType, ValueType


def standardized_type(values: ValueType, weights: ValueType) -> ValueType:
    if isinstance(values, DecimalType) and isinstance(weights, DecimalType):
        if values.scale + weights.scale > 38:
            raise ValueError("Decimal product scale exceeds 38")
        return DecimalType(38, max(values.scale, 6))
    if values in (ScalarType("int64"), ScalarType("float64")) and weights in (
        ScalarType("int64"),
        ScalarType("float64"),
    ):
        return ScalarType("float64")
    raise ValueError(
        "standardize requires I/F values and I/F weights or Decimal values and Decimal weights; Duration rejects"
    )


def reference_type(kind: str, values: ValueType, reference: ValueType) -> ValueType:
    if kind == "penetration":
        return ScalarType("float64")
    if kind == "standardize":
        return standardized_type(values, reference)
    return output_type("ratio", values, reference)


def number(value: object, physical: ValueType) -> int | float | Decimal:
    if isinstance(physical, DurationType):
        if type(value) is not int:
            raise ValueError("Duration reference operands require exact ticks")
        physical = ScalarType("int64")
    exact_value(value, physical)
    if type(value) not in (int, float, Decimal):
        raise ValueError("a reference operand must be a concrete finite numeric value")
    assert isinstance(value, (int, float, Decimal))
    return value


def weight_sum(weights: tuple[object, ...], physical: ValueType) -> Fraction:
    if not weights:
        raise ValueError("reference strata must be nonempty")
    values = tuple(number(value, physical) for value in weights)
    if any(value < 0 for value in values):
        raise ValueError("reference weights must be nonnegative")
    total = sum((Fraction(value) for value in values), Fraction())
    tolerance = Fraction.from_float(1e-12) if physical == ScalarType("float64") else Fraction()
    if abs(total - 1) > tolerance:
        raise ValueError("reference weights must sum to one without normalization")
    return total


def standardized_error(
    values: tuple[Cell, ...],
    weights: tuple[object, ...],
    value_errors: tuple[float, ...],
    weight_errors: tuple[float, ...],
    result: object,
) -> float:
    """Propagate retained operand envelopes and finish once using represented weights."""
    if type(result) is not float:
        return 0.0
    total = Fraction()
    for cell, weight, error_v, error_w in zip(
        values, weights, value_errors, weight_errors, strict=True
    ):
        if any(not math.isfinite(error) or error < 0 for error in (error_v, error_w)):
            raise ValueError("reference error bounds must be finite and nonnegative")
        if weight == 0 and not isinstance(cell, Defined):
            continue
        if (
            not isinstance(cell, Defined)
            or not isinstance(cell.value, (int, float, Decimal))
            or not isinstance(weight, (int, float, Decimal))
        ):
            raise ValueError("positive stratum lacks numeric operands")
        total += (
            abs(Fraction(weight)) * Fraction(error_v)
            + abs(Fraction(cell.value)) * Fraction(error_w)
            + Fraction(error_v) * Fraction(error_w)
        )
    bound = float(total) + roundoff(result)
    if not math.isfinite(bound):
        raise OverflowError("standardized error bound is nonfinite")
    return bound


def standardize(
    values: tuple[Cell, ...],
    weights: tuple[object, ...],
    value_type: ValueType,
    weight_type: ValueType,
) -> Defined:
    result_type = standardized_type(value_type, weight_type)
    weight_sum(weights, weight_type)
    if len(values) != len(weights):
        raise ValueError("every reference stratum requires exactly one value")
    total = Fraction()
    for cell, raw_weight in zip(values, weights, strict=True):
        weight = number(raw_weight, weight_type)
        if isinstance(cell, Defined):
            value = number(cell.value, value_type)
        elif weight == 0:
            continue
        else:
            raise ValueError("every positive-weight stratum requires a finite Defined value")
        total += Fraction(value) * Fraction(weight)
    if isinstance(result_type, DecimalType):
        with localcontext() as context:
            context.prec = 160
            result = (Decimal(total.numerator) / Decimal(total.denominator)).quantize(
                Decimal(1).scaleb(-result_type.scale), rounding=ROUND_HALF_EVEN
            )
        number(result, result_type)
        return Defined(result)
    finished = float(total)
    if not math.isfinite(finished):
        raise OverflowError("standardized float result is not finite")
    return Defined(finished)


def share(
    value: object, reference: object, value_type: ValueType, reference_type: ValueType
) -> Cell:
    return evaluate("ratio", value, reference, value_type, reference_type)


def penetration(intersection: int, total: int) -> Cell:
    if not 0 <= intersection <= total <= 2**63 - 1:
        raise ValueError("complete identity counts must fit int64")
    return (
        Undefined("empty_reference")
        if total == 0
        else Defined(float(Fraction(intersection, total)))
    )
