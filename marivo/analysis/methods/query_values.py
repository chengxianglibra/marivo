"""Local kernels for checked Query and Frame values, including nested partitions."""

from __future__ import annotations

import operator
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from functools import partial

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

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
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.compiler.frame_plan import frame_program
from marivo.analysis.compiler.query_program import FrameProgram, ResultProgram
from marivo.analysis.compiler.query_stages import (
    FilterFusionStep,
    LocalCompute,
    SelectFusionStep,
    local_result,
)
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.core.frame_value_analysis import expressions
from marivo.analysis.core.value_normalization import physical_expression
from marivo.analysis.methods.frame_correlation import correlation
from marivo.analysis.methods.frame_evaluation import (
    FrameEvaluation,
    FrameGroupIndex,
    evaluate_select,
    expression_key,
    group_index,
)
from marivo.analysis.methods.frame_filtering import (
    evaluate_filter_chain,
    evaluate_result_filter,
    fixed_mask,
)
from marivo.analysis.methods.frame_numeric_reduction import (
    finish_reduced_values,
    reduce_numeric,
    singleton_arrow_numeric,
)
from marivo.analysis.methods.numeric_state import (
    Number,
    duration_arithmetic,
    finish_float64,
    nullable_arithmetic,
    typed_null_mask,
)
from marivo.analysis.methods.query_population import evaluate_population
from marivo.analysis.methods.query_value_scalars import (
    int64_array as _int64_array,
)
from marivo.analysis.methods.query_value_scalars import (
    int64_literal as _int64_literal,
)
from marivo.analysis.model.data_types import (
    DataType,
    ScalarDataType,
)
from marivo.analysis.model.errors import reject
from marivo.semantic.spi import numeric_failure

_ORDERED_COMPARISONS = {"lt": "less", "le": "less_equal", "gt": "greater", "ge": "greater_equal"}


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


def _unary_series(expression: ValueUnary, values: pd.Series) -> pd.Series:
    missing = typed_null_mask(values)
    if expression.operation == "is_null":
        return missing.astype("boolean")
    if expression.operation == "not":
        return ~values.astype("boolean")
    if isinstance(values.dtype, pd.ArrowDtype) and pa.types.is_duration(values.dtype.pyarrow_dtype):
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
    return -values if expression.operation == "neg" else values.abs()


def _comparison_series(expression: ValueBinary, left: pd.Series, right: pd.Series) -> pd.Series:
    left_awareness, right_awareness = _timestamp_awareness(left), _timestamp_awareness(right)
    different_awareness = (
        left_awareness is not None
        and right_awareness is not None
        and left_awareness != right_awareness
    )
    missing = typed_null_mask(left) | typed_null_mask(right)
    if _has_temporal_dtype(left) or _has_temporal_dtype(right):
        left, right = _temporal_comparison_values(left), _temporal_comparison_values(right)
    result: list[object] = []
    with localcontext():
        for a, b, absent in zip(left, right, missing, strict=True):
            if absent:
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
                result.append(None)
                continue
            try:
                value = _binary(expression.operation, a, b)
            except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
                numeric_failure("native result arithmetic", str(error))
            result.append(value)
    return pd.Series(result, dtype=object)


def _arithmetic_series(
    expression: ValueBinary,
    program: ResultProgram | FrameProgram,
    left: pd.Series,
    right: pd.Series,
) -> pd.Series:
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
        )

    return nullable_arithmetic(
        {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[expression.operation],
        left,
        right,
    )


def _binary_series(
    expression: ValueBinary,
    frame: pd.DataFrame,
    program: ResultProgram | FrameProgram,
    evaluation: FrameEvaluation | None,
) -> pd.Series:
    kernel = _ORDERED_COMPARISONS.get(expression.operation)
    left_scalar = _int64_literal(expression.left) if kernel is not None else None
    right_scalar = _int64_literal(expression.right) if kernel is not None else None
    left = (
        expression_series(expression.left, frame, program, evaluation=evaluation)
        if left_scalar is None
        else None
    )
    right = (
        expression_series(expression.right, frame, program, evaluation=evaluation)
        if right_scalar is None
        else None
    )
    if kernel is not None and (left is not None or right is not None):
        arrow_left = _int64_array(expression.left, left) if left is not None else left_scalar
        arrow_right = _int64_array(expression.right, right) if right is not None else right_scalar
        if arrow_left is not None and arrow_right is not None:
            comparison = pc.call_function(kernel, [arrow_left, arrow_right])
            return pd.Series(pd.arrays.ArrowExtensionArray(comparison))
    if left is None:
        left = expression_series(expression.left, frame, program, evaluation=evaluation)
    if right is None:
        right = expression_series(expression.right, frame, program, evaluation=evaluation)
    if expression.operation in ("add", "sub", "mul", "div"):
        return _arithmetic_series(expression, program, left, right)
    if expression.operation in ("and", "or"):
        a, b = left.astype("boolean"), right.astype("boolean")
        return a & b if expression.operation == "and" else a | b
    return _comparison_series(expression, left, right)


def _evaluate_partition(
    expression: ValuePartition,
    frame: pd.DataFrame,
    program: FrameProgram,
    evaluation: FrameEvaluation,
) -> pd.Series:
    if not evaluation.memoize:
        return partition_series(expression, frame, program, evaluation)
    key = expression_key(expression)
    if key not in evaluation.partitions:
        evaluation.partitions[key] = partition_series(expression, frame, program, evaluation)
    return evaluation.partitions[key]


def expression_series(
    expression: ValueExpression,
    frame: pd.DataFrame,
    program: ResultProgram | FrameProgram,
    *,
    evaluation: FrameEvaluation | None = None,
) -> pd.Series:
    if isinstance(expression, ValueShare):
        if not isinstance(program, FrameProgram):
            reject(
                "a Frame-bound share expression",
                repr(expression),
                "Evaluate shares on their original AnalysisFrame input.",
                "Column.share",
            )
        return expression_series(
            physical_expression(expression, len(program.input_schema.axes)),
            frame,
            program,
            evaluation=evaluation,
        )
    if isinstance(expression, ValuePartition):
        if not isinstance(program, FrameProgram):
            reject(
                "a Frame-bound partition expression",
                repr(expression),
                "Evaluate partitions on their original AnalysisFrame input.",
                "frame.over",
            )
        return _evaluate_partition(expression, frame, program, evaluation or FrameEvaluation())
    if isinstance(expression, ValueSlot):
        if (
            evaluation is not None
            and (binding := evaluation.bound_selection(expression.index)) is not None
        ):
            return binding
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
        if isinstance(literal, (datetime, date, timedelta, pd.Timestamp, pd.Timedelta)):
            return pd.Series([literal] * len(frame), dtype=object)
        return pd.Series(pd.arrays.ArrowExtensionArray(pa.array([literal] * len(frame))))
    if isinstance(expression, ValueUnary):
        values = expression_series(expression.operand, frame, program, evaluation=evaluation)
        return _unary_series(expression, values)
    return _binary_series(expression, frame, program, evaluation)


def evaluate_result(stage: LocalCompute, inputs: tuple[pd.DataFrame, ...]) -> pd.DataFrame:
    program = local_result(stage)
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
            *(slot for group in program.key_groups[: len(program.output_groups)] for slot in group),
            *(program.key_groups[position][0] for position in operation.positions),
        ]
        result = frame.loc[:, columns].copy()
        result.columns = [column.slot_id for column in stage.columns]
        return result
    if isinstance(operation, SelectValues):
        result = frame.loc[:, list(program.keys)].copy()
        for index, (_, expression) in enumerate(operation.values):
            slot = f"value.{index}"
            computed = expression_series(expression, frame, program)
            dtype = next(column.dtype for column in stage.columns if column.slot_id == slot)
            result[slot] = (
                finish_float64(computed, "Column.select")
                if dtype == ScalarDataType("float64") and not isinstance(expression, ValueSlot)
                else computed
            )
        if program.input_layout is not None:
            for column in program.input_layout.columns:
                if column.slot_id not in program.keys and column not in program.input_layout.values:
                    result[column.slot_id] = frame[column.slot_id]
        return result
    if isinstance(operation, FilterValues):
        return evaluate_result_filter(program, operation, frame, stage.columns, expression_series)
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
    reject(
        "a planned result kernel",
        type(operation).__name__,
        "Compile an operation supported by AnalysisFrame.compute().",
        "analysis_frame.compute",
    )


def _pair_values(
    values: pd.Series, dtype: DataType, *, endpoint: str, location: str
) -> tuple[Number | None, ...]:
    return tuple(
        None if absent else value
        for value, absent in zip(values.tolist(), typed_null_mask(values), strict=True)
    )


def evaluate_frame(
    program: FrameProgram,
    inputs: tuple[pd.DataFrame, ...],
    *,
    filter_steps: tuple[FilterFusionStep, ...] = (),
    select_steps: tuple[SelectFusionStep, ...] = (),
    memoize: bool = True,
) -> pd.DataFrame:
    """Evaluate a Frame operation with a fresh current-input memo."""
    source = inputs[0].reset_index(drop=True)
    evaluation = FrameEvaluation(memoize=memoize, singleton_partitions=program.singleton_partitions)
    if filter_steps:
        return evaluate_filter_chain(
            filter_steps, source, partial(_true_mask, evaluation=evaluation)
        )
    for step in select_steps:
        evaluation.bind_select_step(step, source, partial(expression_series, evaluation=evaluation))
    return _evaluate_frame(program, source, evaluation)


def _true_mask(
    predicate: ValueExpression,
    source: pd.DataFrame,
    program: FrameProgram,
    evaluation: FrameEvaluation,
) -> pd.Series:
    return (
        expression_series(predicate, source, program, evaluation=evaluation)
        .astype("boolean")
        .fillna(False)
    )


def _evaluate_frame(
    program: FrameProgram,
    source: pd.DataFrame,
    evaluation: FrameEvaluation,
) -> pd.DataFrame:
    """Evaluate one unary Frame program; binary execution owns attachment handling."""
    operation = program.operation
    output = program.output_schema
    if isinstance(operation, FixFrameValues):
        fixed_rows = fixed_mask(operation, program, source)
        return (
            source.loc[fixed_rows, [c.slot_id for c in output.columns]]
            .reset_index(drop=True)
            .copy()
        )
    if isinstance(operation, SelectValues):
        return evaluate_select(
            program, operation, source, partial(expression_series, evaluation=evaluation)
        )
    if isinstance(operation, FilterValues):
        mask = _true_mask(expressions(operation)[0], source, program, evaluation)
        return source.loc[mask].reset_index(drop=True).copy()
    if isinstance(operation, TopValues):
        score = expression_series(operation.score, source, program, evaluation=evaluation)
        if operation.n == 0:
            return source.iloc[:0].copy()
        ranked = source.assign(__frame_score=score)
        for key in reversed(program.keys):
            ranked = ranked.sort_values(key, kind="stable", na_position="first")
        ranked = ranked.sort_values(
            "__frame_score", ascending=not operation.descending, kind="stable", na_position="last"
        )
        return ranked.head(operation.n).loc[:, list(source.columns)].reset_index(drop=True).copy()
    assert isinstance(operation, ReduceValues)
    result, groups = _reduce(program, source, evaluation)
    for key in output.key_fields:
        result[key] = (
            source[key].take(groups.representatives).reset_index(drop=True).copy(deep=True)
        )
    result = result.loc[:, [column.slot_id for column in output.columns]]
    for key in reversed(output.key_fields):
        result = result.sort_values(key, na_position="first", kind="stable")
    return result.reset_index(drop=True)


def _pair_endpoints(
    statistic: PairStatistic,
    program: FrameProgram,
    source: pd.DataFrame,
    evaluation: FrameEvaluation,
) -> tuple[tuple[str, str], tuple[pd.Series, pd.Series]]:
    # Complete both expressions before applying either valid-value mask.
    # A missing counterpart cannot hide a strict child arithmetic error.
    endpoints = (statistic.left, statistic.right)
    keys = (expression_key(statistic.left), expression_key(statistic.right))
    values = evaluation.endpoint_series if evaluation.memoize else {}
    for key, endpoint in zip(keys, endpoints, strict=True):
        if key not in values:
            values[key] = expression_series(endpoint, source, program, evaluation=evaluation)
    return keys, (values[keys[0]], values[keys[1]])


def _prepare_pair(
    statistic: PairStatistic,
    types: tuple[DataType, ...],
    program: FrameProgram,
    source: pd.DataFrame,
    evaluation: FrameEvaluation,
) -> tuple[
    tuple[str, str, tuple[DataType, ...]],
    tuple[tuple[Number | None, ...], tuple[Number | None, ...]],
]:
    endpoints = (statistic.left, statistic.right)
    keys, values = _pair_endpoints(statistic, program, source, evaluation)
    converted = evaluation.endpoints if evaluation.memoize else {}
    for side, key, endpoint, dtype, series in zip(
        ("left", "right"), keys, endpoints, types, values, strict=True
    ):
        cache_key = (key, dtype)
        if cache_key not in converted:
            converted[cache_key] = _pair_values(
                series,
                dtype,
                endpoint=f"{side} operand {endpoint!r}",
                location=statistic.location,
            )
    return (keys[0], keys[1], types), (
        converted[(keys[0], types[0])],
        converted[(keys[1], types[1])],
    )


def _reduce_pair(
    statistic: PairStatistic,
    types: tuple[DataType, ...],
    positions: list[int] | range,
    pair_key: tuple[str, str, tuple[DataType, ...]],
    left_values: tuple[Number | None, ...],
    right_values: tuple[Number | None, ...],
    active_pairs: dict[tuple[str, str, tuple[DataType, ...]], tuple[list[Number], list[Number]]],
) -> object:
    if pair_key not in active_pairs:
        active_left: list[Number] = []
        active_right: list[Number] = []
        for position in positions:
            left_value, right_value = left_values[position], right_values[position]
            if left_value is not None and right_value is not None:
                active_left.append(left_value)
                active_right.append(right_value)
        active_pairs[pair_key] = (active_left, active_right)
    active_left, active_right = active_pairs[pair_key]
    if statistic.method == "pair_count":
        return len(active_left)
    left_type, right_type = types
    try:
        return correlation(
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


def _reduce(
    program: FrameProgram, source: pd.DataFrame, evaluation: FrameEvaluation
) -> tuple[pd.DataFrame, FrameGroupIndex]:
    """Reduce values in group-number order after completing their dependencies."""
    operation = program.operation
    output = program.output_schema
    assert isinstance(operation, ReduceValues)
    evaluated: dict[str, pd.Series] = {}
    paired: dict[str, tuple[tuple[Number | None, ...], tuple[Number | None, ...]]] = {}
    pair_keys: dict[str, tuple[str, str, tuple[DataType, ...]]] = {}
    for index, (name, statistic) in enumerate(operation.values):
        if isinstance(statistic, PairStatistic):
            pair_keys[name], paired[name] = _prepare_pair(
                statistic, program.statistic_types[index], program, source, evaluation
            )
        elif statistic.value is not None:
            evaluated[name] = expression_series(
                statistic.value, source, program, evaluation=evaluation
            )
    group_key = tuple(sorted(operation.positions))
    group_indices = evaluation.group_indices if evaluation.memoize else {}
    if group_key not in group_indices:
        columns = tuple(
            column
            for position in group_key
            for column in program.input_schema.axes[position].columns
        )
        group_indices[group_key] = group_index(source, columns)
    groups = group_indices[group_key]
    rows: list[dict[str, object]] = []
    for positions in groups.members:
        row: dict[str, object] = {}
        active_pairs: dict[
            tuple[str, str, tuple[DataType, ...]], tuple[list[Number], list[Number]]
        ] = {}
        for index, (name, statistic) in enumerate(operation.values):
            column = output.values[index]
            value: object
            if isinstance(statistic, PairStatistic):
                left_values, right_values = paired[name]
                pair_key = pair_keys[name]
                value = _reduce_pair(
                    statistic,
                    program.statistic_types[index],
                    positions,
                    pair_key,
                    left_values,
                    right_values,
                    active_pairs if evaluation.memoize else {},
                )
            elif statistic.method == "row_count":
                value = len(positions)
            else:
                series = evaluated[name].take(positions)
                (dtype,) = program.statistic_types[index]
                value = reduce_numeric(series, dtype, statistic.method, empty_group=not positions)
            row[column.slot_id] = value
        rows.append(row)
    result = pd.DataFrame(rows, columns=[column.slot_id for column in output.values], dtype=object)
    for column in output.values:
        values = result[column.slot_id]
        finished = finish_reduced_values(values, column)
        if finished is not values:
            result[column.slot_id] = finished
    return result, groups


def _singleton_partition(
    expression: ValuePartition,
    source: pd.DataFrame,
    reduction: FrameProgram,
    evaluation: FrameEvaluation,
) -> pd.Series:
    statistic = expression.statistic
    if isinstance(statistic, PairStatistic):
        _, (left, right) = _pair_endpoints(statistic, reduction, source, evaluation)
        if statistic.method == "pair_count":
            active = ~(typed_null_mask(left) | typed_null_mask(right))
            return active.astype("int64").astype(object)
        return pd.Series(pd.arrays.ArrowExtensionArray(pa.nulls(len(source), type=pa.float64())))
    if statistic.method == "row_count":
        return pd.Series(1, index=source.index, dtype=object)
    assert statistic.value is not None
    values = expression_series(statistic.value, source, reduction, evaluation=evaluation)
    (dtype,) = reduction.statistic_types[0]
    direct = singleton_arrow_numeric(values, dtype, statistic.method)
    if direct is not None:
        return direct
    # Object/intermediate carriers retain their exact kernel and error behavior.
    rows = pd.Series(
        [
            reduce_numeric(values.iloc[i : i + 1], dtype, statistic.method, empty_group=False)
            for i in range(len(values))
        ],
        dtype=object,
    )
    return finish_reduced_values(rows, reduction.output_schema.values[0])


def partition_series(
    expression: ValuePartition,
    source: pd.DataFrame,
    program: FrameProgram,
    evaluation: FrameEvaluation,
) -> pd.Series:
    """Broadcast reduced values by their group numbers in original row order."""
    reduction = frame_program(
        program.input_schema,
        ReduceValues((("partition", expression.statistic),), expression.positions),
    )
    if expression.positions in evaluation.singleton_partitions:
        return _singleton_partition(expression, source, reduction, evaluation)
    reduced, groups = _reduce(reduction, source, evaluation)
    column = reduction.output_schema.values[0]
    return reduced[column.slot_id].take(groups.row_to_group).reset_index(drop=True)
