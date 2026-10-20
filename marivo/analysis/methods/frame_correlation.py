"""Stable correlation of already paired, admitted current-row numeric values."""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction
from typing import Literal

from marivo.analysis.core.data_types import DataType, ScalarDataType
from marivo.analysis.methods.numeric_state import Number


def _finite(values: Sequence[float]) -> None:
    if any(not math.isfinite(value) for value in values):
        raise ValueError("correlation conversion or intermediate value is nonfinite")


def _scale(values: Sequence[float]) -> tuple[float, ...]:
    _finite(values)
    maximum = max(map(abs, values))
    if maximum == 0:
        raise ValueError("nonconstant correlation input has zero computational scale")
    return tuple(value / maximum for value in values)


def _differences(values: Sequence[Number], dtype: DataType) -> tuple[float, ...]:
    if dtype == ScalarDataType("float64"):
        floating = tuple(float(value) for value in values)
        _finite(floating)
        exponent = math.frexp(max(map(abs, floating)))[1]
        scaled = tuple(math.ldexp(value, -exponent) for value in floating)
        return tuple(value - scaled[0] for value in scaled)
    # Fraction subtraction is exact for both wide Python integers and Decimal's
    # same-scale integer values, independently of the active Decimal context.
    reference = Fraction(values[0])
    return tuple(float(Fraction(value) - reference) for value in values)


def _residuals(values: Sequence[float]) -> tuple[float, ...]:
    scaled = _scale(values)
    mean = math.fsum(scaled) / len(scaled)
    return _scale(tuple(value - mean for value in scaled))


def _coefficient(left: Sequence[float], right: Sequence[float]) -> float:
    a, b = _residuals(left), _residuals(right)
    left_norm = math.sqrt(math.fsum(value * value for value in a))
    right_norm = math.sqrt(math.fsum(value * value for value in b))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("nonconstant correlation input has zero residual norm")
    result = math.fsum(x * y for x, y in zip(a, b, strict=True)) / (left_norm * right_norm)
    _finite((left_norm, right_norm, result))
    return max(-1.0, min(1.0, result))


def _ranks(values: Sequence[Number]) -> tuple[float, ...]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[start]] == values[ordered[end]]:
            end += 1
        rank = (start + 1 + end) / 2
        for position in ordered[start:end]:
            result[position] = rank
        start = end
    return tuple(result)


def correlation(
    left: Sequence[Number],
    right: Sequence[Number],
    *,
    left_type: DataType,
    right_type: DataType,
    method: Literal["pearson", "spearman"],
) -> float | None:
    """Finish one paired vector without owning masks, grouping or Frame state."""
    if (
        len(left) < 2
        or all(value == left[0] for value in left)
        or all(value == right[0] for value in right)
    ):
        return None
    if method == "spearman":
        return _coefficient(_ranks(left), _ranks(right))
    return _coefficient(_differences(left, left_type), _differences(right, right_type))
