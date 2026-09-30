"""Typed original-state merging and one final rounding for fixed relations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from typing import Literal, TypeAlias

import pyarrow as pa

from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType, ValueType

Number: TypeAlias = int | float | Decimal


def checked_sum(values: Sequence[object], physical: pa.DataType) -> Number:
    """Accumulate exactly (or compensated float64), then check the stored type."""
    if pa.types.is_decimal(physical):
        if any(type(value) is not Decimal or not value.is_finite() for value in values):
            raise ValueError("Decimal state has a non-Decimal or nonfinite component")
        with localcontext() as context:
            context.prec = 120
            result = sum((value for value in values if isinstance(value, Decimal)), Decimal(0))
            if abs(result) >= Decimal(10) ** (physical.precision - physical.scale):
                raise OverflowError("Decimal state precision overflow")
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
    if not -(2**63) <= integer < 2**63:
        raise OverflowError("int64 state overflow")
    return integer


def finish_division(numerator: Number, denominator: Number, output: ValueType) -> Number:
    """Round a ratio of validated retained components once to the output type."""
    exact = Fraction(numerator) / Fraction(denominator)
    if isinstance(output, DurationType):
        ticks = round(exact)
        if not -(2**63) <= ticks < 2**63:
            raise OverflowError("Duration finish tick overflow")
        return ticks
    if isinstance(output, DecimalType):
        with localcontext() as context:
            context.prec = 120
            value = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
                Decimal(1).scaleb(-output.scale), rounding=ROUND_HALF_EVEN
            )
            if abs(value) >= Decimal(10) ** (output.precision - output.scale):
                raise OverflowError("Decimal finish precision overflow")
            return value
    value_float = float(exact)
    if not math.isfinite(value_float):
        raise OverflowError("float64 finish overflow")
    return value_float


def merge_original(
    rows: Sequence[Mapping[str, object]],
    schema: pa.Schema,
    components: tuple[str, ...],
    method: str,
    output: ValueType,
    empty_rules: tuple[Literal["null", "zero"], ...],
) -> tuple[dict[str, Number], Number | None, str, str | None]:
    """Merge complete state rows; do not reconstruct omitted or damaged components."""
    totals: dict[str, Number] = {}
    for name in components:
        values = [row[name] for row in rows]
        if name in ("min", "max"):
            active = [
                value
                for row in rows
                if row["non_null_count"] != 0
                for value in (row[name],)
                if isinstance(value, (int, float, Decimal))
            ]
            physical = schema.field("original_state__" + name).type
            zero: Number = (
                Decimal(0)
                if pa.types.is_decimal(physical)
                else 0.0
                if pa.types.is_floating(physical)
                else 0
            )
            values = [(min(active) if name == "min" else max(active)) if active else zero]
        totals[name] = checked_sum(values, schema.field("original_state__" + name).type)
    value: Number | None
    tag, reason = "defined", None
    if method == "linear":
        if not all(
            totals[name] > 0 or rule == "zero"
            for name, rule in zip(
                components[1 : 2 * len(empty_rules) : 2], empty_rules, strict=True
            )
        ):
            return totals, None, "null", "empty_contribution"
        with localcontext() as context:
            context.prec = 120
            signed = [
                totals[name] * (1 if name.startswith("plus_") else -1)
                for name in components[: 2 * len(empty_rules) : 2]
            ]
            dtype = (
                pa.decimal128(output.precision, output.scale)
                if isinstance(output, DecimalType)
                else pa.float64()
                if output == ScalarType("float64")
                else pa.int64()
            )
            value = checked_sum(signed, dtype)
    elif method in ("mean", "weighted_mean", "ratio"):
        if method == "mean":
            numerator, denominator = totals["sum"], totals["non_null_count"]
            contributed = denominator > 0
        elif method == "weighted_mean":
            numerator, denominator = totals["weighted_numerator"], totals["weight_sum"]
            contributed = totals["non_null_pair_count"] > 0
        else:
            numerator, denominator = totals["numerator_sum"], totals["denominator_sum"]
            contributed = all(
                totals[f"{prefix}_non_null_count"] > 0 or rule == "zero"
                for prefix, rule in zip(("numerator", "denominator"), empty_rules, strict=True)
            )
        if not contributed:
            return totals, None, "null", "empty_contribution"
        if denominator == 0:
            return (
                totals,
                None,
                "undefined" if method == "ratio" else "null",
                "zero_denominator" if method == "ratio" else "zero_weight_sum",
            )
        value = finish_division(numerator, denominator, output)
    elif method == "count":
        value = totals["count"]
    else:
        value = totals[method if method in ("min", "max") else "sum"]
        if totals["non_null_count"] == 0 and method != "sum_zero":
            value, tag, reason = None, "null", "empty_contribution"
    return totals, value, tag, reason
