"""Invocation-local grouping and expression results over one fixed row domain."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pandas as pd

from marivo._definition.query import ValueExpression
from marivo.analysis.core.data_types import DataType
from marivo.analysis.methods.numeric_state import Number


def expression_key(expression: ValueExpression) -> str:
    """Use the closed records' typed representation, not numeric Python equality.

    In particular, ValueLiteral(1), ValueLiteral(1.0) and ValueLiteral(Decimal(1))
    must not share a numeric result. This key is never persisted.
    """
    return repr(expression)


@dataclass(frozen=True, slots=True)
class FrameGroupIndex:
    """Group numbering follows first appearance in the current input rows."""

    row_to_group: npt.NDArray[np.intp]
    members: tuple[list[int] | range, ...]
    representatives: list[int]


@dataclass
class FrameEvaluation:
    """Owned by one evaluate_frame call, including its nested partition kernels."""

    group_indices: dict[tuple[int, ...], FrameGroupIndex] = field(default_factory=dict)
    partitions: dict[str, pd.Series] = field(default_factory=dict)
    endpoint_series: dict[str, pd.Series] = field(default_factory=dict)
    endpoints: dict[tuple[str, DataType], tuple[Number | None, ...]] = field(default_factory=dict)
