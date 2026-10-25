"""Invocation-local successful results for expressions over one fixed row domain."""

from __future__ import annotations

from dataclasses import dataclass, field

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


@dataclass
class FrameEvaluation:
    """Owned by one evaluate_frame call, including its nested partition kernels."""

    partitions: dict[str, pd.Series] = field(default_factory=dict)
    endpoint_series: dict[str, pd.Series] = field(default_factory=dict)
    endpoints: dict[tuple[str, DataType], tuple[Number | None, ...]] = field(default_factory=dict)
