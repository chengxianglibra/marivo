"""Short-circuit row positions over the original typed Frame operands."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import numpy as np
import pandas as pd

from marivo._definition.query import FilterValues, ValueExpression
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.compiler.query_stages import FilterFusionStep
from marivo.analysis.core.frame_fix import validate_fixed_key
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.core.frozen_calendar import frozen_key_rows
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.local_laws import selection_predicate_columns

FilterMask: TypeAlias = Callable[[ValueExpression, pd.DataFrame, FrameProgram], pd.Series]


def evaluate_filter_chain(
    steps: tuple[FilterFusionStep, ...], source: pd.DataFrame, mask_for: FilterMask
) -> pd.DataFrame:
    """Evaluate each original predicate on its demanded operands, then take rows once."""
    positions = np.arange(len(source))
    for step in steps:
        if not positions.size:
            break
        program = step.program
        operation = program.operation
        operands = source.loc[:, list(step.columns)].take(positions)
        if isinstance(operation, FilterValues):
            mask = mask_for(operation.predicate, operands, program).to_numpy(dtype=bool)
        else:
            assert isinstance(operation, FixFrameValues)
            mask = np.ravel(np.asarray(fixed_mask(operation, program, operands), dtype=bool))
        positions = positions[mask]
    columns = tuple(column.slot_id for column in steps[-1].program.output_schema.columns)
    result = source.take(positions)
    if columns != tuple(result.columns):
        result = result.loc[:, list(columns)]
    return result.reset_index(drop=True)


def fixed_mask(
    operation: FixFrameValues, program: FrameProgram, source: pd.DataFrame
) -> list[bool]:
    axis = program.input_schema.axes[operation.position]
    fixed_key = validate_fixed_key(axis, operation.key)
    return [item == fixed_key for item in frozen_key_rows(source, axis.columns)]


def evaluate_result_filter(
    program: ResultProgram,
    operation: FilterValues,
    source: pd.DataFrame,
    columns: tuple[DataColumn, ...],
    evaluate: Callable[[ValueExpression, pd.DataFrame, ResultProgram], pd.Series],
) -> pd.DataFrame:
    """Use Kleene conjunction only while the materialized carriers prove totality."""
    steps: tuple[FilterValues, ...] = (operation,)
    if (
        program.selection_steps
        and selection_predicate_columns(
            operation.predicate, program.value_columns, program.columns, program.key_groups
        )
        is None
    ):
        steps = program.selection_steps
    for step in steps:
        mask = evaluate(step.predicate, source, program).astype("boolean").fillna(False)
        source = source.loc[mask].reset_index(drop=True)
    return source.loc[:, [column.slot_id for column in columns]].copy()
