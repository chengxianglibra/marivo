"""Invocation-local grouping and expression results over one fixed row domain."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pandas as pd

from marivo._definition.query import ValueExpression
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.methods.numeric_state import Number
from marivo.analysis.model.data_types import DataColumn, DataType
from marivo.analysis.model.frozen_keys import FrozenKey


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


def group_index(source: pd.DataFrame, columns: tuple[DataColumn, ...]) -> FrameGroupIndex:
    """Index complete grouping keys in their first appearance order."""
    if not columns:
        return FrameGroupIndex(np.zeros(len(source), dtype=np.intp), (range(len(source)),), [])
    row_to_group = np.empty(len(source), dtype=np.intp)
    group_ids: dict[FrozenKey, int] = {}
    members: list[list[int]] = []
    representatives: list[int] = []
    for position, key in enumerate(frozen_key_rows(source, columns)):
        group = group_ids.setdefault(key, len(members))
        if group == len(members):
            members.append([])
            representatives.append(position)
        members[group].append(position)
        row_to_group[position] = group
    return FrameGroupIndex(row_to_group, tuple(members), representatives)


@dataclass
class FrameEvaluation:
    """Owned by one evaluate_frame call, including its nested partition kernels."""

    memoize: bool = True
    group_indices: dict[tuple[int, ...], FrameGroupIndex] = field(default_factory=dict)
    partitions: dict[str, pd.Series] = field(default_factory=dict)
    endpoint_series: dict[str, pd.Series] = field(default_factory=dict)
    endpoints: dict[tuple[str, DataType], tuple[Number | None, ...]] = field(default_factory=dict)
