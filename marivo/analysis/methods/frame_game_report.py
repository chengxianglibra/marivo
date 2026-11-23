"""Render completed replacement games with exact parent and player coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.attribute import AttributeMethodPayload
from marivo.analysis.core.attribute_game import (
    AttributeGameProgram,
    CompletedGame,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frame_method_facts import (
    FrameAttributeMetric,
    FrameMethodCoordinate,
    FrameMethodParent,
    _key_order,
)
from marivo.analysis.core.frozen_calendar import frozen_key_rows, frozen_key_table
from marivo.analysis.methods.attribute_game_diagnostics import attribute_numeric_context
from marivo.analysis.methods.attribute_game_sampling import (
    _fail,
    _null,
    _number,
    _scaled_sum,
    _series,
    game_contexts,
)
from marivo.analysis.methods.attribute_numeric import _numeric_binary
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_attribute_report import _report_value
from marivo.analysis.model.data_types import DataColumn, DecimalDataType
from marivo.analysis.model.frozen_keys import FrozenKey
from marivo.analysis.model.method_targets import FrameMethodOutput, FrameMethodTarget
from marivo.analysis.model.method_values import FrameMethodValue
from marivo.semantic.spi import (
    numeric_failure,
)

_Number: TypeAlias = int | float | Decimal


@dataclass(frozen=True, slots=True)
class _GameParentResult:
    facts: FrameMethodParent
    means: tuple[float, ...]
    errors: tuple[float, ...]
    total: _Number
    observed: _Number


def _report_parent(
    program: AttributeGameProgram,
    parent: StageOutput,
    completed: CompletedGame,
    parent_columns: tuple[DataColumn, ...],
    output_columns: dict[tuple[str, ...], DataColumn],
    mappings: tuple[FrameMethodOutput, ...],
) -> _GameParentResult:
    operation, target = program.operation, program.operation.target
    context, sampled = completed.context, completed.sample
    key, parent_index = context.parent, context.parent_index
    means, errors = sampled.means, sampled.errors
    current = _series(sampled.current.value, sampled.current.column)
    baseline = _series(sampled.baseline.value, sampled.baseline.column)
    closure_budget = sampled.closure_budget
    change = _numeric_binary("sub", current, baseline)
    total = change
    if target.mode == "relative":
        denominator = _number(abs(_number(baseline.values.iloc[0])))
        if denominator == 0:
            _fail(
                f"{'.'.join(target.path)}: baseline=0; relative attribution requires nonzero baseline; check the baseline window and source retention/filter conditions or select absolute change",
                metric=operation.games[0].metric,
                parent=key,
            )
        total = _numeric_binary("div", change, _series(denominator, baseline.column))
        relative_roundoff = tuple(
            math.ulp(v / float(denominator))
            + abs(v / float(denominator)) * (math.ulp(float(denominator)) / float(denominator))
            for v in means
        )
        closure_budget = closure_budget / float(denominator) + _scaled_sum(relative_roundoff)[0]
        means, errors = (
            tuple(v / float(denominator) for v in means),
            tuple(v / float(denominator) for v in errors),
        )
        if not all(math.isfinite(v) for v in (*means, *errors, closure_budget)):
            numeric_failure(
                "finite Float64 relative contributions, standard errors and roundoff",
                f"baseline magnitude={denominator!r}",
            )
    total_path = ("total", *target.path)
    observed_path = ("observed_total", *target.path)
    total_column = output_columns[total_path]
    observed_column = output_columns[observed_path]
    observed = _number(
        parent.table[program.parent_schema.values[target.observed].slot_id].iloc[parent_index]
    )
    total = _report_value(total, total_column)
    total_value = _number(total.values.iloc[0])
    original_observed = program.parent_schema.values[target.observed]
    observed = _number(
        _report_value(_series(observed, original_observed), observed_column).values.iloc[0]
    )
    typed_total = FrameMethodValue(total_column, total_value)
    metric = FrameAttributeMetric(
        target.path,
        target.mode,
        completed.method,
        FrameMethodValue(observed_column, observed),
        typed_total,
        mappings,
        "zero_total" if total_value == 0 else "defined",
    )
    facts = FrameMethodParent(
        tuple(
            FrameMethodValue(column, value)
            for column, (_, value) in zip(parent_columns, key, strict=True)
        ),
        (metric,),
    )
    contribution_sum, sum_roundoff = _scaled_sum(means)
    closure_budget, budget_roundoff = _scaled_sum(
        (closure_budget, math.ulp(float(total_value)), sum_roundoff)
    )
    closure_budget += budget_roundoff
    if isinstance(total.column.dtype, DecimalDataType):
        closure_budget += float(Decimal(1).scaleb(-total.column.dtype.scale)) / 2
    if not math.isfinite(closure_budget):
        numeric_failure("a finite Float64 closure roundoff budget", repr(closure_budget))
    if abs(contribution_sum - float(total_value)) > closure_budget:
        _fail(f"parent={key!r}: sampled contributions do not close to the cached endpoint change")
    return _GameParentResult(facts, means, errors, total_value, observed)


def _player_detail(
    program: AttributeGameProgram,
    left: StageOutput,
    right: StageOutput,
    current_rows: dict[FrozenKey, int],
    baseline_rows: dict[FrozenKey, int],
    player: FrozenKey,
    output_columns: dict[tuple[str, ...], DataColumn],
    record: dict[str, object],
) -> None:
    operation, target = program.operation, program.operation.target
    values = []
    for source, schema, rows in (
        (left, program.left_schema, current_rows),
        (right, program.right_schema, baseline_rows),
    ):
        values.append(
            None
            if player not in rows
            else source.table[
                schema.values[operation.games[0 if source is left else 1].target_slot].slot_id
            ].iloc[rows[player]]
        )
    for detail_prefix, value in zip(
        (("detail", "current"), ("detail", "baseline"), ("detail", "absolute")),
        (
            *values,
            None
            if any(_null(v) for v in values)
            else _number(
                _numeric_binary(
                    "sub",
                    _series(
                        _number(values[0]),
                        program.left_schema.values[operation.games[0].target_slot],
                    ),
                    _series(
                        _number(values[1]),
                        program.right_schema.values[operation.games[1].target_slot],
                    ),
                ).values.iloc[0]
            ),
        ),
        strict=True,
    ):
        record[output_columns[detail_prefix + target.path].slot_id] = value
    for column, present in zip(
        program.output_schema.presence,
        (player in current_rows, player in baseline_rows),
        strict=True,
    ):
        record[column.slot_id] = present


def assemble_game_report(
    program: AttributeGameProgram,
    inputs: tuple[StageOutput, ...],
    completed_games: tuple[CompletedGame, ...],
) -> tuple[pd.DataFrame, AttributeMethodPayload]:
    parent, left, right, _, _ = inputs
    operation, target = program.operation, program.operation.target
    parent_columns = tuple(c for axis in program.parent_schema.axes for c in axis.columns)
    fine_columns = tuple(
        c
        for axis in program.output_schema.axes[len(program.parent_schema.axes) :]
        for c in axis.columns
    )
    output_columns = dict(
        zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
    )
    result_keys: list[FrozenKey] = []
    records: list[dict[str, object]] = []
    parents = []
    mappings = (
        FrameMethodOutput("contribution", ("contribution", *target.path)),
        FrameMethodOutput("total", ("total", *target.path)),
        FrameMethodOutput("observed_total", ("observed_total", *target.path)),
        FrameMethodOutput("share", ("contribution_share", *target.path)),
        FrameMethodOutput("contribution_std_error", ("contribution_std_error", *target.path)),
        FrameMethodOutput("current", ("detail", "current", *target.path)),
        FrameMethodOutput("baseline", ("detail", "baseline", *target.path)),
        FrameMethodOutput("absolute", ("detail", "absolute", *target.path)),
        FrameMethodOutput("current_present", ("detail", "current", "present", *target.path)),
        FrameMethodOutput("baseline_present", ("detail", "baseline", "present", *target.path)),
    )
    if tuple(item.context for item in completed_games) != game_contexts(program, inputs):
        _fail("completed samples must retain the exact frozen parent/player directory")
    for completed in completed_games:
        context = completed.context
        key, players = context.parent, context.players
        current_rows, baseline_rows = dict(context.current_rows), dict(context.baseline_rows)
        with attribute_numeric_context(
            (left, right), operation.games[0].metric, key, parent_columns
        ):
            report = _report_parent(
                program, parent, completed, parent_columns, output_columns, mappings
            )
            parents.append(report.facts)
            means, errors = report.means, report.errors
            total_value, observed = report.total, report.observed
            for index, player in enumerate(players):
                result_keys.append((*key, *player))
                record: dict[str, object] = {}
                for prefix, value in (
                    (("contribution",), means[index]),
                    (("total",), total_value),
                    (("observed_total",), observed),
                    (
                        ("contribution_share",),
                        None if total_value == 0 else means[index] / float(total_value),
                    ),
                    (("contribution_std_error",), errors[index]),
                ):
                    record[output_columns[prefix + target.path].slot_id] = value
                _player_detail(
                    program,
                    left,
                    right,
                    current_rows,
                    baseline_rows,
                    player,
                    output_columns,
                    record,
                )
                records.append(record)
    result = frozen_key_table((*parent_columns, *fine_columns), tuple(result_keys))
    for column in (*program.output_schema.values, *program.output_schema.presence):
        result[column.slot_id] = pd.Series(
            pd.arrays.ArrowExtensionArray(
                pa.array(
                    [
                        None if _null(record[column.slot_id]) else record[column.slot_id]
                        for record in records
                    ],
                    type=stage_arrow_type(column.dtype),
                )
            )
        )
    coordinates = tuple(
        FrameMethodCoordinate(
            a.name,
            a.schema,
            tuple(tuple(v for _, v in key) for key in frozen_key_rows(a.rows, a.schema.columns)),
        )
        for a in sorted(parent.frame_attachments, key=lambda a: a.name)
    )
    payload = AttributeMethodPayload(
        program.output_schema,
        len(program.parent_schema.axes),
        tuple(sorted(parents, key=_key_order)),
        coordinates,
        (FrameMethodTarget(target.path, target.mode, mappings),),
        parent_schema=program.parent_schema,
    )
    return result, payload
