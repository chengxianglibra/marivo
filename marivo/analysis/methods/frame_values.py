"""Ordinary data-only kernels preserving current rows and coordinate structure."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from marivo._definition.query import FilterValues, ReduceValues, SelectValues, TopValues
from marivo.analysis.compiler.graph_plan import FrameCompute
from marivo.analysis.compiler.query_program import FrameCombineProgram
from marivo.analysis.core.data_types import DecimalDataType, DurationDataType, ScalarDataType
from marivo.analysis.methods.numeric_state import checked_sum, finish_division, finish_float64
from marivo.analysis.methods.physical import DecimalType, DurationType, stage_arrow_type
from marivo.analysis.methods.query_values import expression_series
from marivo.semantic._numeric_lowering import numeric_failure


def evaluate_frame(stage: FrameCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    program = stage.program
    if isinstance(program, FrameCombineProgram):
        from marivo.analysis.methods.frame_comparison import combine_frames

        return combine_frames(program, inputs)
    operation = program.operation
    source = inputs[0].reset_index(drop=True)
    output = program.output_schema
    if isinstance(operation, SelectValues):
        result = source.loc[
            :,
            [
                *program.keys,
                *(c.slot_id for c in output.presence),
                *(c.slot_id for c in output.projection_columns),
            ],
        ].copy()
        for column, (_, expression) in zip(output.values, operation.values, strict=True):
            values = expression_series(expression, source, program)
            result[column.slot_id] = (
                finish_float64(values, "Frame.select")
                if column.dtype == ScalarDataType("float64")
                else values
            )
        return result.loc[:, [c.slot_id for c in output.columns]].copy()
    if isinstance(operation, FilterValues):
        from marivo.analysis.core.frame_expression import expressions

        mask = (
            expression_series(expressions(operation)[0], source, program)
            .astype("boolean")
            .fillna(False)
        )
        return source.loc[mask].reset_index(drop=True).copy()
    if isinstance(operation, TopValues):
        score = expression_series(operation.score, source, program)
        ranked = source.assign(__frame_score=score)
        if program.keys:
            ranked = ranked.sort_values(list(program.keys), kind="stable", na_position="first")
        ranked = ranked.sort_values(
            "__frame_score", ascending=not operation.descending, kind="stable", na_position="last"
        )
        return ranked.head(operation.n).loc[:, list(source.columns)].reset_index(drop=True).copy()
    assert isinstance(operation, ReduceValues)
    evaluated = {
        name: expression_series(stat.value, source, program)
        for name, stat in operation.values
        if stat.value is not None
    }
    selected = tuple(
        key for position in operation.positions for key in program.key_groups[position]
    )
    groups = (
        (((), source),)
        if not selected
        else tuple(source.groupby(list(selected), dropna=False, sort=False, observed=True))
    )
    rows: list[dict[str, object]] = []
    for key, subset in groups:
        keys = key if isinstance(key, tuple) else (key,)
        row: dict[str, object] = dict(zip(output.key_fields, keys, strict=True))
        for index, (name, statistic) in enumerate(operation.values):
            column = output.values[index]
            value: object
            if statistic.method == "row_count":
                value = len(subset)
            else:
                active = evaluated[name].iloc[subset.index].dropna()
                dtype = program.statistic_types[index]
                try:
                    if isinstance(dtype, DurationDataType):
                        ticks = tuple(
                            int(tick)
                            for tick in (
                                pa.array(active, type=pa.duration(dtype.unit), from_pandas=True)
                                .cast(pa.int64())
                                .to_pylist()
                            )
                        )
                        value = (
                            finish_division(
                                sum(ticks),
                                1 if statistic.method == "sum" else len(ticks),
                                DurationType(dtype.unit),
                            )
                            if ticks
                            else None
                        )
                    else:
                        total = checked_sum(active.tolist(), stage_arrow_type(dtype))
                        value = (
                            None
                            if not len(active)
                            else total
                            if statistic.method == "sum"
                            else finish_division(
                                total,
                                len(active),
                                DecimalType(column.dtype.precision, column.dtype.scale),
                            )
                            if isinstance(column.dtype, DecimalDataType)
                            else total / len(active)
                        )
                except (ValueError, OverflowError) as error:
                    numeric_failure("a qualified finite current-row statistic", str(error))
            row[column.slot_id] = value
        rows.append(row)
    result = pd.DataFrame(rows, columns=[c.slot_id for c in output.columns], dtype=object)
    for column in output.values:
        if isinstance(column.dtype, DurationDataType):
            result[column.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(
                    pa.array(result[column.slot_id], type=pa.int64(), from_pandas=True).cast(
                        pa.duration(column.dtype.unit)
                    )
                )
            )
        elif column.dtype == ScalarDataType("float64"):
            result[column.slot_id] = finish_float64(result[column.slot_id], "Frame.reduce")
    if output.key_fields:
        result = result.sort_values(list(output.key_fields), na_position="first", kind="stable")
    return result.reset_index(drop=True)
