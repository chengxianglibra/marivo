"""Ordinary data-only kernels preserving current rows and coordinate structure."""

from __future__ import annotations

from fractions import Fraction

import pandas as pd
import pyarrow as pa

from marivo._definition.query import (
    FilterValues,
    PairStatistic,
    ReduceValues,
    SelectValues,
    TopValues,
)
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frozen_calendar import FrozenKey, frozen_key_rows
from marivo.analysis.core.model import reject
from marivo.analysis.methods.frame_correlation import correlation
from marivo.analysis.methods.numeric_state import (
    Number,
    checked_sum,
    finish_division,
    finish_float64,
)
from marivo.analysis.methods.physical import DecimalType, DurationType
from marivo.analysis.methods.query_values import expression_series
from marivo.semantic._numeric_lowering import numeric_failure
from marivo.semantic.errors import SemanticExpressionError


def _pair_values(
    values: pd.Series, dtype: DataType, *, endpoint: str, location: str
) -> tuple[Number | None, ...]:
    try:
        if dtype == ScalarDataType("float64"):
            values = finish_float64(values, "paired endpoint")
        return tuple(
            None if pd.isna(value) else checked_sum((value,), stage_arrow_type(dtype))
            for value in values.tolist()
        )
    except (ValueError, OverflowError, SemanticExpressionError) as error:
        received = error.received if isinstance(error, SemanticExpressionError) else str(error)
        reject(
            "finite admitted numeric inputs for paired statistics",
            f"{endpoint}: {received}",
            "Repair the input values to satisfy their numeric types, then retry.",
            location,
        )


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
    evaluated: dict[str, pd.Series] = {}
    paired: dict[str, tuple[tuple[Number | None, ...], tuple[Number | None, ...]]] = {}
    for index, (name, statistic) in enumerate(operation.values):
        if isinstance(statistic, PairStatistic):
            # Complete both expressions before applying either valid-value mask.
            # A missing counterpart cannot hide a strict child arithmetic error.
            left = expression_series(statistic.left, source, program)
            right = expression_series(statistic.right, source, program)
            left_type, right_type = program.statistic_types[index]
            paired[name] = (
                _pair_values(
                    left,
                    left_type,
                    endpoint=f"left operand {statistic.left!r}",
                    location=statistic.location,
                ),
                _pair_values(
                    right,
                    right_type,
                    endpoint=f"right operand {statistic.right!r}",
                    location=statistic.location,
                ),
            )
        elif statistic.value is not None:
            evaluated[name] = expression_series(statistic.value, source, program)
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
            if isinstance(statistic, PairStatistic):
                left_values, right_values = paired[name]
                active_left: list[Number] = []
                active_right: list[Number] = []
                for position in positions:
                    left_value, right_value = left_values[position], right_values[position]
                    if left_value is not None and right_value is not None:
                        active_left.append(left_value)
                        active_right.append(right_value)
                if statistic.method == "pair_count":
                    value = len(active_left)
                else:
                    left_type, right_type = program.statistic_types[index]
                    try:
                        value = correlation(
                            active_left,
                            active_right,
                            left_type=left_type,
                            right_type=right_type,
                            method=statistic.method,
                        )
                    except (ValueError, OverflowError) as error:
                        reject(
                            "finite nonzero numeric scales for nonconstant paired statistics",
                            f"{statistic.method}({statistic.left!r}, {statistic.right!r}): {error}",
                            "Use finite representable numeric inputs, then retry the paired statistic.",
                            statistic.location,
                        )
            elif statistic.method == "row_count":
                value = len(positions)
            else:
                active = evaluated[name].take(positions).dropna()
                (dtype,) = program.statistic_types[index]
                try:
                    if not positions and statistic.method == "sum":
                        value = (
                            checked_sum((), stage_arrow_type(dtype))
                            if not isinstance(dtype, DurationDataType)
                            else 0
                        )
                    elif isinstance(dtype, DurationDataType):
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
