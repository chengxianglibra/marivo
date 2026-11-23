"""Prepare checked contribution inputs over their exact parent and fine keys."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.observation import formula_columns
from marivo.analysis.compiler.query_stages import LocalCompute
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeFormulaContext,
    AttributeInputError,
    AttributeMatrixColumn,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frozen_calendar import frozen_key_rows, frozen_key_table
from marivo.analysis.methods.attribute_numeric import _Value
from marivo.analysis.methods.carriers.stage_components import select_attribute_root
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_attribute import attribute_source_values
from marivo.analysis.methods.local import query_combine
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.frozen_keys import FrozenKey


@dataclass(frozen=True, slots=True)
class ContributionInputs:
    parent_columns: tuple[DataColumn, ...]
    fine_columns: tuple[DataColumn, ...]
    parent_rows: tuple[FrozenKey, ...]
    fine_parents: tuple[FrozenKey, ...]
    fine_to_parent: list[int]
    originals: dict[tuple[str, str], pd.DataFrame]
    values: dict[tuple[str, str, tuple[int, ...], str], _Value]


def _mapped_source(
    program: AttributeContributionProgram,
    inputs: tuple[StageOutput, ...],
    item: AttributeMatrixColumn,
) -> tuple[pd.DataFrame, ...]:
    prepared, _, left_projection, right_projection = inputs
    projection = left_projection if item.side == "left" else right_projection
    frames: list[pd.DataFrame] = []
    for source in item.sources:
        root = select_attribute_root(prepared.attribute_inputs, item.scope, source)
        frames.append(
            attribute_source_values(
                program.input_program, root, projection, source, item.scope
            ).table
        )
    return tuple(frames)


def _original(
    program: AttributeContributionProgram,
    inputs: tuple[StageOutput, ...],
    side: Literal["left", "right"],
    scope: Literal["parent", "fine"],
    mapped_source: Callable[[AttributeMatrixColumn], tuple[pd.DataFrame, ...]],
) -> pd.DataFrame:
    formula = program.originals[0 if side == "left" else 1]
    keys = tuple(
        c
        for axis in (
            program.input_program.parent_schema.axes
            if scope == "parent"
            else program.output_schema.axes
        )
        for c in axis.columns
    )
    frames: list[pd.DataFrame] = []
    bindings = []
    for value in formula.values:
        item = next(
            item
            for item in program.input_program.matrix
            if item.side == side
            and item.scope == scope
            and item.role in ("value", "native_value")
            and any(source.source == value.column.slot_id for source in item.sources)
        )
        source = item.sources[0]
        frame = mapped_source(item)[0]
        if scope == "fine":
            # Preserve explicit output candidates. Activation uses the structural
            # parent/window mapping, never the original value's NA state.
            parent_item = next(
                m
                for m in program.input_program.matrix
                if m.side == side
                and m.scope == "parent"
                and m.role in ("value", "native_value")
                and m.sources[0].source == source.source
            )
            active = mapped_source(parent_item)[0]
            parent_columns = tuple(
                c for a in program.input_program.parent_schema.axes for c in a.columns
            )
            active_parents = set(frozen_key_rows(active, parent_columns))
            domain = inputs[0].table.loc[:, [c.slot_id for c in keys]]
            selected = [
                i
                for i, key in enumerate(frozen_key_rows(domain, parent_columns))
                if key in active_parents
            ]
            domain = domain.take(selected).reset_index(drop=True)
            actual_keys = tuple(frozen_key_rows(frame, keys))
            by_key = dict(zip(actual_keys, range(len(frame)), strict=True))
            wanted = frozen_key_rows(domain, keys)
            domain[source.source] = pd.Series(
                [
                    frame[source.source].iloc[by_key[key]] if key in by_key else None
                    for key in wanted
                ],
                dtype=frame[source.source].dtype,
            )
            domain["__numeric_present"] = pd.Series([key in by_key for key in wanted], dtype=bool)
            frame = domain
        frames.append(frame)
        bindings.append(
            replace(
                value,
                column=replace(value.column, dtype=item.target.dtype),
                keys=tuple(c.slot_id for c in keys),
                key_groups=(),
                checks=(),
            )
        )
    formula = replace(
        formula,
        calendar=None,
        populations=(),
        output_population=None,
        counts=(),
        parent_scope=None,
        parent_slots=(),
        values=tuple(bindings),
    )
    outputs = formula_columns(formula)
    if scope == "parent" and not len(inputs[1].table):
        empty = frozen_key_table(keys, ())
        for column in outputs:
            empty[column.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([], type=stage_arrow_type(column.dtype)))
            )
        return empty
    stage = LocalCompute(
        "attribute.original", (), (*keys, *outputs), tuple(c.slot_id for c in keys), formula
    )
    parent_columns = tuple(c for a in program.input_program.parent_schema.axes for c in a.columns)
    uses = tuple(
        dict.fromkeys(
            source.use
            for item in program.input_program.matrix
            if item.side == side
            for source in item.sources
        )
    )
    parents = frozen_key_rows(inputs[1].table, parent_columns)
    return query_combine(
        stage,
        tuple(frames),
        attribute=AttributeFormulaContext(
            uses,
            keys,
            parent_columns,
            parent_columns,
            tuple(c for c in keys if c.slot_id not in {p.slot_id for p in parent_columns}),
            tuple((key, key) for key in parents),
        ),
    )


def prepare_contribution_inputs(
    program: AttributeContributionProgram, inputs: tuple[StageOutput, ...], *, level: bool
) -> ContributionInputs:
    """Read checked originals and map each component once per invocation."""
    prepared, parent, _, _ = inputs
    sides: tuple[Literal["left", "right"], ...] = ("left",) if level else ("left", "right")
    parent_columns = tuple(c for a in program.input_program.parent_schema.axes for c in a.columns)
    fine_columns = tuple(
        c
        for a in program.output_schema.axes[len(program.input_program.parent_schema.axes) :]
        for c in a.columns
    )
    parent_rows = tuple(frozen_key_rows(parent.table, parent_columns))
    fine_parents = tuple(frozen_key_rows(prepared.table, parent_columns))
    parent_indices = {key: i for i, key in enumerate(parent_rows)}
    fine_to_parent = [parent_indices[key] for key in fine_parents]
    # These checked inputs stay immutable throughout this contribution invocation.
    # Originals, fine activation and arithmetic can consume the same matrix column.
    mapped: dict[int, tuple[pd.DataFrame, ...]] = {}

    def mapped_source(item: AttributeMatrixColumn) -> tuple[pd.DataFrame, ...]:
        key = id(item)
        if key not in mapped:
            mapped[key] = _mapped_source(program, inputs, item)
        return mapped[key]

    originals: dict[tuple[str, str], pd.DataFrame] = {
        (side, scope): _original(program, inputs, side, scope, mapped_source)
        for side in sides
        for scope in ("parent", "fine")
    }
    values: dict[tuple[str, str, tuple[int, ...], str], _Value] = {}
    for item in program.input_program.matrix:
        if item.scope == "fine":
            series = prepared.table[item.target.slot_id]
        else:
            frames = mapped_source(item)
            cells: dict[FrozenKey, object] = {}
            for source, frame in zip(item.sources, frames, strict=True):
                for i, key in enumerate(frozen_key_rows(frame, parent_columns)):
                    if key in cells:
                        raise AttributeInputError(
                            reason="missing_component_proof",
                            received="multiple structurally active inputs for one complete parent",
                        )
                    cells[key] = frame[source.source].iloc[i]
            series = pd.Series([cells.get(key) for key in parent_rows], dtype=object)
        values[(item.side, item.scope, item.occurrence, item.role)] = _Value(series, item.target)
    return ContributionInputs(
        parent_columns, fine_columns, parent_rows, fine_parents, fine_to_parent, originals, values
    )
