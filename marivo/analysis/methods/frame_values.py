"""Ordinary data-only kernels preserving current rows and coordinate structure."""

from __future__ import annotations

from fractions import Fraction

import pandas as pd
import pyarrow as pa

from marivo._definition.query import FilterValues, ReduceValues, SelectValues, TopValues
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.core.data_types import DecimalDataType, DurationDataType, ScalarDataType
from marivo.analysis.core.frozen_calendar import FrozenKey, frozen_key_rows
from marivo.analysis.methods.numeric_state import checked_sum, finish_division, finish_float64
from marivo.analysis.methods.physical import DecimalType, DurationType, stage_arrow_type
from marivo.analysis.methods.query_values import expression_series
from marivo.semantic._numeric_lowering import numeric_failure


def evaluate_frame(program: FrameProgram, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    """Evaluate one unary Frame program; binary execution owns attachment handling."""
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
        for key in reversed(program.keys):
            ranked = ranked.sort_values(key, kind="stable", na_position="first")
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
    grouped: dict[FrozenKey, list[int]] = {}
    if selected:
        columns = {column.slot_id: column for column in program.input_schema.columns}
        for position, frozen_key in enumerate(
            frozen_key_rows(source, tuple(columns[key] for key in selected))
        ):
            grouped.setdefault(frozen_key, []).append(position)
        groups = tuple(grouped.values())
    else:
        groups = (list(range(len(source))),)
    rows: list[dict[str, object]] = []
    for positions in groups:
        row: dict[str, object] = {}
        for index, (name, statistic) in enumerate(operation.values):
            column = output.values[index]
            value: object
            if statistic.method == "row_count":
                value = len(positions)
            else:
                active = evaluated[name].take(positions).dropna()
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
                    elif statistic.method == "mean" and (
                        dtype == ScalarDataType("int64") or isinstance(dtype, DecimalDataType)
                    ):
                        # Components keep their admitted type; only the temporary total is wide.
                        exact = sum(
                            (
                                Fraction(checked_sum((item,), stage_arrow_type(dtype)))
                                for item in active.tolist()
                            ),
                            Fraction(),
                        )
                        if not len(active):
                            value = None
                        else:
                            exact /= len(active)
                            value = (
                                finish_division(
                                    exact.numerator,
                                    exact.denominator,
                                    DecimalType(column.dtype.precision, column.dtype.scale),
                                )
                                if isinstance(column.dtype, DecimalDataType)
                                else float(exact)
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
    result = pd.DataFrame(rows, columns=[column.slot_id for column in output.values], dtype=object)
    if selected:
        first_positions = [positions[0] for positions in groups]
        for key in selected:
            result[key] = source[key].take(first_positions).reset_index(drop=True).copy(deep=True)
    result = result.loc[:, [column.slot_id for column in output.columns]]
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
    for key in reversed(output.key_fields):
        result = result.sort_values(key, na_position="first", kind="stable")
    return result.reset_index(drop=True)
