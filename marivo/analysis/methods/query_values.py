"""Pandas kernels for checked result expressions, selection and statistics."""

from __future__ import annotations

import math
import operator
from decimal import Decimal, localcontext

import pandas as pd
import pyarrow as pa

from marivo._definition.query import (
    CombineValues,
    CoordinateSlot,
    FilterValues,
    KeyImage,
    LiteralValues,
    PopulationValues,
    PresenceSlot,
    ProjectKeys,
    ReadFields,
    ReduceValues,
    SelectValues,
    TopValues,
    ValueBinary,
    ValueBroadcast,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.compiler.query_program import ResultProgram
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


def expression_series(
    expression: ValueExpression, frame: pd.DataFrame, program: ResultProgram
) -> pd.Series:
    if isinstance(expression, ValueBroadcast):
        index = next(
            index
            for index, reference in enumerate(program.references)
            if reference.definition == expression.source
        )
        return frame[f"__broadcast.{index}"].reset_index(drop=True)
    if isinstance(expression, ValueSlot):
        columns = (
            program.input_layout.values
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
        assert program.input_layout is not None
        return frame[
            next(
                item.column
                for item in program.input_layout.presence
                if item.path == expression.path
            )
        ].reset_index(drop=True)
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
    result: list[object] = []
    with localcontext() as context:
        context.prec = 120
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            if pd.isna(a) or pd.isna(b):
                result.append(None)
                continue
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
    if isinstance(operation, CombineValues):
        from marivo.analysis.methods.query_comparison import align_frames, compare_frames

        assert program.comparison is not None
        relation_frame = None
        if program.comparison.relation_requirement is not None:
            requirement = program.comparison.relation_requirement
            relation_frame = (
                inputs[2]
                .loc[:, [slot for group in program.relation_groups for slot in group]]
                .copy()
            )
            relation_frame.columns = (*requirement.left_keys, *requirement.right_keys)
        return (
            align_frames(program.comparison, inputs[0], inputs[1], relation_frame=relation_frame)
            if operation.operation == "align"
            else compare_frames(
                program.comparison,
                inputs[0],
                inputs[1],
                zero_denominator=operation.zero_denominator,
                relation_frame=relation_frame,
            )
        ).frame
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
        return pd.DataFrame(flattened, columns=[column.slot_id for column in stage.columns])
    frame = inputs[0].reset_index(drop=True).copy()
    if isinstance(operation, ProjectKeys):
        assert program.input_layout is not None
        projected_columns: list[str] = []
        assert program.output_layout is not None
        for axis_index, (position, path) in enumerate(
            zip(operation.positions, operation.paths, strict=True)
        ):
            if path is None:
                projected_columns.extend(program.key_groups[position])
                continue
            projection = (
                next(
                    item
                    for item in program.input_layout.projections
                    if item.path == path and item.axis == program.output_layout.axes[axis_index]
                )
                if program.output_layout is not None
                else next(item for item in program.input_layout.projections if item.path == path)
            )
            present = next(
                item.column for item in program.input_layout.presence if item.path == path
            )
            if not frame[present].all():
                reject(
                    "a total endpoint coordinate projection",
                    repr(path),
                    "Filter this Query by the endpoint presence Column before population().project.",
                    "Population.project",
                )
            projected_columns.extend(projection.columns)
        result = frame.loc[:, projected_columns].drop_duplicates().reset_index(drop=True)
        result.columns = [column.slot_id for column in stage.columns]
        return result
    if program.references:
        from marivo.analysis.methods.query_comparison import broadcast_series

        for index, reference in enumerate(program.references):
            frame[f"__broadcast.{index}"] = broadcast_series(
                reference.reference,
                inputs[reference.input_index],
                reference.target,
                frame.loc[:, [column.slot_id for column in reference.target.columns]],
                value_slot=reference.reference.values[0].slot_id,
                mapping=reference.mapping,
            )
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
        if not isinstance(
            operation.predicate,
            (
                ValueSlot,
                CoordinateSlot,
                PresenceSlot,
                ValueLiteral,
                ValueUnary,
                ValueBinary,
                ValueBroadcast,
            ),
        ):
            from marivo.analysis.methods.query_population import condition_mask

            mask = condition_mask(
                operation.predicate,
                frame,
                program.axes,
                program.key_groups,
                program.population_inputs,
                inputs[1 : 1 + len(program.population_inputs)],
                program.population_layouts,
            )
        else:
            mask = (
                expression_series(operation.predicate, frame, program)
                .astype("boolean")
                .fillna(False)
            )
        return (
            frame.loc[mask, [column.slot_id for column in stage.columns]]
            .reset_index(drop=True)
            .copy()
        )
    if isinstance(operation, TopValues):
        score = expression_series(operation.score, frame, program)
        ordered = frame.assign(__score=score)
        partitions = tuple(
            key for position in operation.partitions for key in program.key_groups[position]
        )
        # Stable score sorting preserves canonical complete-key order on ties.
        # Missing scores remain last independently of null coordinate categories.
        if program.keys:
            ordered = ordered.sort_values(list(program.keys), na_position="first", kind="stable")
        ordered = ordered.sort_values(
            "__score", ascending=not operation.descending, na_position="last", kind="stable"
        )
        if partitions:
            ordered = ordered.sort_values(list(partitions), na_position="first", kind="stable")
            ordered = ordered.groupby(list(partitions), dropna=False, sort=False).head(operation.n)
        else:
            ordered = ordered.head(operation.n)
        return (
            ordered.loc[:, [column.slot_id for column in stage.columns]]
            .reset_index(drop=True)
            .copy()
        )
    if isinstance(operation, ReduceValues):
        evaluated = {
            name: expression_series(statistic.value, frame, program)
            for name, statistic in operation.values
            if statistic.value is not None
        }
        for position, mapping in operation.calendar_maps:
            calendar_key = program.key_groups[operation.positions[position]][0]
            frame[calendar_key] = frame[calendar_key].map(dict(enumerate(mapping)))
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
            for index, (name, statistic) in enumerate(operation.values):
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
        "Compile a qualified Query operation.",
        "Query.execute",
    )
