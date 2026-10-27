"""Stable correlation of already paired, admitted current-row numeric values."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import pandas as pd

from marivo.analysis.core.data_types import DataType
from marivo.analysis.methods.numeric_state import Number


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
    a, b = pd.Series(list(left)), pd.Series(list(right))
    if method == "spearman":
        a, b = a.rank(method="average"), b.rank(method="average")
    return float(a.corr(b))
