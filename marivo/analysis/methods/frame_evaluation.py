"""Invocation-local grouping and expression results over one fixed row domain."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

import numpy as np
import numpy.typing as npt
import pandas as pd

from marivo._definition.query import ValueExpression, ValueSlot
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.core.data_validation import normalize_stage_column
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.methods.numeric_state import Number, finish_float64
from marivo.analysis.model.data_types import DataColumn, DataType, ScalarDataType
from marivo.analysis.model.frozen_keys import FrozenKey

if TYPE_CHECKING:
    from marivo._definition.query import SelectValues
    from marivo.analysis.compiler.query_stages import SelectFusionStep

FrameValueKernel: TypeAlias = Callable[[ValueExpression, pd.DataFrame, FrameProgram], pd.Series]


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
    """One evaluate_frame call's kernel memo and typed projection bindings."""

    memoize: bool = True
    singleton_partitions: frozenset[tuple[int, ...]] = frozenset()
    group_indices: dict[tuple[int, ...], FrameGroupIndex] = field(default_factory=dict)
    partitions: dict[str, pd.Series] = field(default_factory=dict)
    endpoint_series: dict[str, pd.Series] = field(default_factory=dict)
    endpoints: dict[tuple[str, DataType], tuple[Number | None, ...]] = field(default_factory=dict)
    _selected_values: dict[int, pd.Series] | None = field(default=None, init=False)

    def bound_selection(self, index: int) -> pd.Series | None:
        """Resolve the preceding projection's exact carrier for this value slot."""
        return None if self._selected_values is None else self._selected_values[index]

    def bind_select_step(
        self, step: SelectFusionStep, source: pd.DataFrame, evaluate: FrameValueKernel
    ) -> None:
        """Evaluate every live or fallible output before normalizing typed bindings."""
        expressions = dict((*step.live_values, *step.retained_check_expressions))
        columns = step.program.output_schema.values
        # Ordinary Select computes every expression before it normalizes any
        # output carrier. Keep both error phases in the original column order.
        raw = {
            index: _selected_value(column, expressions[index], source, step.program, evaluate)
            for index, column in enumerate(columns)
            if index in expressions
        }
        normalized = {
            index: normalize_stage_column(value, columns[index]) for index, value in raw.items()
        }
        self._selected_values = {index: normalized[index] for index, _ in step.live_values}


def _selected_value(
    column: DataColumn,
    expression: ValueExpression,
    source: pd.DataFrame,
    program: FrameProgram,
    evaluate: FrameValueKernel,
) -> pd.Series:
    values = evaluate(expression, source, program)
    if column.dtype == ScalarDataType("float64") and not isinstance(expression, ValueSlot):
        return finish_float64(values, "Frame.select")
    return values


def evaluate_select(
    program: FrameProgram,
    operation: SelectValues,
    source: pd.DataFrame,
    evaluate: FrameValueKernel,
) -> pd.DataFrame:
    """Finish one projection's columns, keeping the exact coordinate directories."""
    output = program.output_schema
    result = source.loc[
        :,
        [
            *program.keys,
            *(c.slot_id for c in output.presence),
            *(c.slot_id for c in output.projection_columns),
        ],
    ].copy()
    for column, (_, expression) in zip(output.values, operation.values, strict=True):
        result[column.slot_id] = _selected_value(column, expression, source, program, evaluate)
    return result.loc[:, [c.slot_id for c in output.columns]].copy()
