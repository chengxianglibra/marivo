"""Stable correlation of already paired, admitted current-row numeric values."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd

from marivo.analysis.core.data_types import DataType
from marivo.analysis.methods.numeric_state import Number


def _scale_overflowing(values: Sequence[Number]) -> Sequence[Number]:
    """Divide by the peak magnitude when centered products cannot fit in float64.

    A positive scale preserves Pearson correlation for values float64 can represent
    relative to that peak. Nonfinite values, and values float() cannot convert,
    keep their original carriers.
    """
    converted: list[float] = []
    maximum = 0.0
    for value in values:
        try:
            number = float(value)
        except (OverflowError, ValueError, ArithmeticError):
            return values
        if not math.isfinite(number):
            return values
        converted.append(number)
        magnitude = abs(number)
        if magnitude > maximum:
            maximum = magnitude
    count = len(converted)
    if maximum <= 1.0 or count < 2:
        return values
    # |x - mean| can reach twice the peak, so each square can reach 4 * peak**2.
    if maximum <= math.sqrt(float(np.finfo(np.float64).max) / (4 * count)):
        return values
    return [number / maximum for number in converted]


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
        a, b = pd.Series(list(left)), pd.Series(list(right))
        a, b = a.rank(method="average"), b.rank(method="average")
    else:
        a = pd.Series(list(_scale_overflowing(left)))
        b = pd.Series(list(_scale_overflowing(right)))
    # Keep the native coefficient when covariance or normalization is nonfinite.
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(a.corr(b))
