"""Pandas kernels for checked result expressions, selection and statistics."""

from __future__ import annotations

import math
import operator
from datetime import datetime
from decimal import Decimal, localcontext

import pandas as pd
import pyarrow as pa

from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    KeyImage,
    LiteralValues,
    PairStatistic,
    PopulationValues,
    PresenceSlot,
    ReadFields,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.core.data_types import DecimalDataType, DurationDataType, ScalarDataType
from marivo.analysis.core.model import reject
from marivo.analysis.methods.numeric_state import checked_sum, finish_division
from marivo.analysis.methods.physical import DecimalType, DurationType, stage_arrow_type
from marivo.semantic._numeric_lowering import numeric_failure


def _binary(operation: str, left: object, right: object) -> object:
    operators = {
        "add": operator.add,
        "sub": operator.sub,
        "mul": operator.mul,
        "div": operator.truediv,
        "eq": operator.eq,
        "ne": operator.ne,
    }
    if operation in operators:
        result: object = operators[operation](left, right)
        return result
    a, b = pd.Series([left], dtype=object), pd.Series([right], dtype=object)
    comparison = (
        a.lt(b)
        if operation == "lt"
        else a.le(b)
        if operation == "le"
        else a.gt(b)
        if operation == "gt"
        else a.ge(b)
    )
    result = comparison.iloc[0]
    return result


def _temporal_comparison_values(values: pd.Series) -> pd.Series:
    """Read temporal validity and comparable ticks without Python date conversion."""
    literal = values.iloc[0] if values.dtype == object and len(values) else None
    if isinstance(literal, (pd.Timestamp, pd.Timedelta)):
        # Arrow's Python-object conversion can truncate out-of-range pandas
        # years or submicrosecond values, even with an explicit Arrow type.
        native: list[int | None] = []
        for value in values:
            if value is None or value is pd.NA or value is pd.NaT:
                native.append(None)
            elif isinstance(value, (pd.Timestamp, pd.Timedelta)):
                native.append(
                    int(value.asm8.view("i8"))
                    * {"s": 10**9, "ms": 10**6, "us": 10**3, "ns": 1}[value.unit]
                )
            else:
                numeric_failure("a compatible temporal literal", type(value).__name__)
        return pd.Series(native, dtype=object, index=values.index)
    array = pa.array(values, from_pandas=True)
    physical = array.type
    if pa.types.is_date32(physical):
        ticks, scale = array.cast(pa.int32()), 1
    elif pa.types.is_timestamp(physical) or pa.types.is_duration(physical):
        ticks = array.cast(pa.int64())
        scale = {"s": 10**9, "ms": 10**6, "us": 10**3, "ns": 1}[physical.unit]
    else:
        return values
    return pd.Series(
        [None if tick is None else int(tick) * scale for tick in ticks.to_pylist()],
        dtype=object,
        index=values.index,
    )


def _has_temporal_dtype(values: pd.Series) -> bool:
    dtype = values.dtype
    return isinstance(dtype, pd.ArrowDtype) and bool(pa.types.is_temporal(dtype.pyarrow_dtype))


def _timestamp_awareness(values: pd.Series) -> bool | None:
    dtype = values.dtype
    if isinstance(dtype, pd.ArrowDtype):
        return (
            dtype.pyarrow_dtype.tz is not None
            if pa.types.is_timestamp(dtype.pyarrow_dtype)
            else None
        )
    literal: object = values.iloc[0] if len(values) else None
    if isinstance(literal, datetime):
        return literal.tzinfo is not None and literal.utcoffset() is not None
    return None


def expression_series(
    expression: ValueExpression, frame: pd.DataFrame, program: ResultProgram | FrameProgram
) -> pd.Series:
    if isinstance(expression, ValueSlot):
        columns = (
            program.input_schema.values
            if isinstance(program, FrameProgram)
            else program.input_layout.values
            if program.input_layout is not None
            else tuple(column for column in program.columns if column.slot_id not in program.keys)
        )
        return frame[columns[expression.index].slot_id].reset_index(drop=True)
    if isinstance(expression, CoordinateSlot):
        group = program.key_groups[expression.index]
        if len(group) == 1:
            return frame[group[0]].reset_index(drop=True)
        return pd.Series(
            list(frame.loc[:, list(group)].itertuples(index=False, name=None)), dtype=object
        )
    if isinstance(expression, PresenceSlot):
        if isinstance(program, FrameProgram):
            return frame[
                program.input_schema.presence[
                    program.input_schema.presence_paths.index(expression.path)
                ].slot_id
            ].reset_index(drop=True)
        reject(
            "a Frame-bound presence slot",
            repr(expression.path),
            "Acquire presence from the current AnalysisFrame.",
            "frame.expression",
        )
    if isinstance(expression, ValueLiteral):
        literal = (
            Decimal(expression.value)
            if type(expression.value) is int and not -(2**63) <= expression.value < 2**63
            else expression.value
        )
        return pd.Series([literal] * len(frame), dtype=object)
    if isinstance(expression, ValueUnary):
        values = expression_series(expression.operand, frame, program)
        if expression.operation == "is_null":
            return values.isna().astype("boolean")
        if expression.operation == "not":
            return ~values.astype("boolean")
        if isinstance(values.dtype, pd.ArrowDtype) and pa.types.is_duration(
            values.dtype.pyarrow_dtype
        ):
            array = pa.array(values, from_pandas=True)
            output: list[int | None] = []
            for tick in array.cast(pa.int64()).to_pylist():
                computed_tick = (
                    None
                    if tick is None
                    else -int(tick)
                    if expression.operation == "neg"
                    else abs(int(tick))
                )
                if computed_tick is not None and not -(2**63) <= computed_tick < 2**63:
                    numeric_failure(
                        "exact signed int64 Duration arithmetic",
                        f"{expression.operation}: Duration tick overflow",
                    )
                output.append(computed_tick)
            return pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array(output, type=pa.int64()).cast(array.type)),
                index=values.index,
            )
        return pd.Series(
            [
                None
                if pd.isna(value)
                else value.copy_negate()
                if isinstance(value, Decimal) and expression.operation == "neg"
                else value.copy_abs()
                if isinstance(value, Decimal)
                else -value
                if expression.operation == "neg"
                else abs(value)
                for value in values
            ],
            dtype=object,
        )
    left = expression_series(expression.left, frame, program)
    right = expression_series(expression.right, frame, program)
    if expression.operation in ("add", "sub", "mul", "div"):
        from marivo.analysis.methods.numeric_state import duration_arithmetic, nullable_arithmetic

        duration = next(
            (item for item in program.duration_operations if item.expression is expression), None
        )
        if duration is not None:
            return duration_arithmetic(
                {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                    expression.operation
                ],
                left,
                right,
                left_type=duration.left,
                right_type=duration.right,
                output=duration.output,
                zero_denominator=expression.zero_denominator,
            )

        return nullable_arithmetic(
            {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                expression.operation
            ],
            left,
            right,
            zero_denominator=expression.zero_denominator,
        )
    if expression.operation in ("and", "or"):
        a, b = left.astype("boolean"), right.astype("boolean")
        return a & b if expression.operation == "and" else a | b
    left_awareness, right_awareness = _timestamp_awareness(left), _timestamp_awareness(right)
    different_awareness = (
        left_awareness is not None
        and right_awareness is not None
        and left_awareness != right_awareness
    )
    if _has_temporal_dtype(left) or _has_temporal_dtype(right):
        left, right = _temporal_comparison_values(left), _temporal_comparison_values(right)
    result: list[object] = []
    with localcontext() as context:
        context.prec = 120
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            if pd.isna(a) or pd.isna(b):
                result.append(None)
                continue
            if different_awareness:
                if expression.operation in ("eq", "ne"):
                    result.append(expression.operation == "ne")
                    continue
                numeric_failure(
                    "matching timestamp timezone awareness for ordered comparisons",
                    "a timezone-aware timestamp and a timezone-free timestamp",
                )
            if expression.operation == "div" and b == 0:
                if expression.zero_denominator == "error":
                    numeric_failure(
                        "a nonzero denominator for demanded non-NA values",
                        f"zero denominator at coordinate {tuple(frame.iloc[index][key] for key in program.keys)!r}",
                    )
                result.append(None)
                continue
            try:
                value = _binary(expression.operation, a, b)
            except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
                numeric_failure("valid finite result arithmetic", str(error))
            if (isinstance(value, float) and not math.isfinite(value)) or (
                isinstance(value, Decimal) and not value.is_finite()
            ):
                numeric_failure("finite result arithmetic", repr(value))
            result.append(value)
    return pd.Series(result, dtype=object)


def evaluate_result(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    program = stage.result
    assert program is not None
    operation = program.operation
    if isinstance(operation, KeyImage):
        frame = inputs[0].loc[:, list(program.keys)].copy()
        frame.columns = [column.slot_id for column in stage.columns]
        return frame
    if isinstance(operation, LiteralValues):
        rows: list[dict[str, object]] = []
        for keys, values in operation.rows:
            flat = tuple(
                component
                for key in keys
                for component in (key if isinstance(key, tuple) else (key,))
            )
            rows.append(
                dict(
                    zip((column.slot_id for column in stage.columns), (*flat, *values), strict=True)
                )
            )
        return pd.DataFrame(rows, columns=[column.slot_id for column in stage.columns])
    if isinstance(operation, PopulationValues):
        from marivo.analysis.methods.query_population import evaluate_population

        population_rows = evaluate_population(
            operation.definition, program.population_inputs, inputs, program.population_layouts
        )
        flattened = [
            tuple(
                component
                for axis, key in zip(operation.definition.axes, row, strict=True)
                for component in (key if isinstance(key, tuple) else (key,))
            )
            for row in population_rows
        ]
        return pd.DataFrame(
            flattened, columns=[column.slot_id for column in stage.columns], dtype=object
        )
    frame = inputs[0].reset_index(drop=True).copy()
    if isinstance(operation, ReadFields):
        columns = [
            *program.key_groups[0],
            *(program.key_groups[position][0] for position in operation.positions),
        ]
        result = frame.loc[:, columns].copy()
        result.columns = [column.slot_id for column in stage.columns]
        return result
    if isinstance(operation, SelectValues):
        result = frame.loc[:, list(program.keys)].copy()
        for index, (_, expression) in enumerate(operation.values):
            from marivo.analysis.methods.numeric_state import finish_float64

            slot = f"value.{index}"
            computed = expression_series(expression, frame, program)
            dtype = next(column.dtype for column in stage.columns if column.slot_id == slot)
            result[slot] = (
                finish_float64(computed, "Column.select")
                if dtype == ScalarDataType("float64")
                else computed
            )
        if program.input_layout is not None:
            for column in program.input_layout.columns:
                if column.slot_id not in program.keys and column not in program.input_layout.values:
                    result[column.slot_id] = frame[column.slot_id]
        return result
    if isinstance(operation, FilterValues):
        mask = (
            expression_series(operation.predicate, frame, program).astype("boolean").fillna(False)
        )
        return (
            frame.loc[mask, [column.slot_id for column in stage.columns]]
            .reset_index(drop=True)
            .copy()
        )
    if isinstance(operation, TopValues):
        score = expression_series(operation.score, frame, program)
        ordered = frame.assign(__score=score)
        # Stable score sorting preserves canonical complete-key order on ties.
        # Missing scores remain last independently of null coordinate categories.
        if program.keys:
            ordered = ordered.sort_values(list(program.keys), na_position="first", kind="stable")
        ordered = ordered.sort_values(
            "__score", ascending=not operation.descending, na_position="last", kind="stable"
        )
        ordered = ordered.head(operation.n)
        return (
            ordered.loc[:, [column.slot_id for column in stage.columns]]
            .reset_index(drop=True)
            .copy()
        )
    if isinstance(operation, ReduceValues):
        statistics: list[tuple[str, Statistic]] = []
        for name, statistic in operation.values:
            if isinstance(statistic, PairStatistic):
                reject(
                    "paired statistics evaluated by the local Frame route",
                    statistic.method,
                    "Keep paired statistics on their owning AnalysisFrame.",
                    statistic.location,
                )
            statistics.append((name, statistic))
        evaluated = {
            name: expression_series(statistic.value, frame, program)
            for name, statistic in statistics
            if statistic.value is not None
        }
        selected = tuple(
            key for position in operation.positions for key in program.key_groups[position]
        )
        groups = (
            (((), frame),)
            if not selected
            else tuple(frame.groupby(list(selected), dropna=False, sort=True, observed=True))
        )
        rows = []
        for key, subset in groups:
            key_tuple = key if isinstance(key, tuple) else (key,)
            row: dict[str, object] = dict(zip(stage.keys, key_tuple, strict=True))
            for index, (name, statistic) in enumerate(statistics):
                if statistic.method == "row_count":
                    value: object = len(subset)
                else:
                    active_series = evaluated[name].iloc[subset.index].dropna()
                    active = active_series.tolist()
                    column = stage.columns[len(stage.keys) + index]
                    try:
                        input_type = program.statistic_types[index]
                        if isinstance(input_type, DurationDataType):
                            ticks = tuple(
                                int(tick)
                                for tick in pa.array(
                                    active_series,
                                    type=pa.duration(input_type.unit),
                                    from_pandas=True,
                                )
                                .cast(pa.int64())
                                .to_pylist()
                            )
                            value = (
                                finish_division(
                                    sum(ticks),
                                    1 if statistic.method == "sum" else len(ticks),
                                    DurationType(input_type.unit),
                                )
                                if ticks
                                else None
                            )
                        else:
                            total = checked_sum(active, stage_arrow_type(input_type))
                            value = (
                                total
                                if active and statistic.method == "sum"
                                else finish_division(
                                    total,
                                    len(active),
                                    DecimalType(column.dtype.precision, column.dtype.scale),
                                )
                                if active and isinstance(column.dtype, DecimalDataType)
                                else total / len(active)
                                if active
                                else None
                            )
                    except (ValueError, OverflowError) as error:
                        numeric_failure("a qualified finite value statistic", str(error))
                row[f"value.{index}"] = value
            rows.append(row)
        result = pd.DataFrame(
            rows, columns=[column.slot_id for column in stage.columns], dtype=object
        )
        for column in stage.columns[len(stage.keys) :]:
            if isinstance(column.dtype, DurationDataType):
                result[column.slot_id] = pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        pa.array(result[column.slot_id], type=pa.int64(), from_pandas=True).cast(
                            pa.duration(column.dtype.unit)
                        )
                    )
                )
        return result
    reject(
        "a planned result kernel",
        type(operation).__name__,
        "Compile an operation supported by AnalysisFrame.compute().",
        "analysis_frame.compute",
    )
