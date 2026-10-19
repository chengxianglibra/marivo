"""Apply source-effective types to the existing local Query programs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import ibis
import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo._definition.query import (
    CoordinateSlot,
    ReadFields,
    ReduceValues,
    SelectValues,
    ValueExpression,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.graph_plan import LocalCompute, QueryPlan, SourceRead
from marivo.analysis.compiler.observation import (
    FormulaProgram,
    data_type,
    formula_columns,
    ibis_type,
)
from marivo.analysis.compiler.queries import expression_value
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput


def source_value_keys(plan: QueryPlan) -> dict[str, tuple[str, ...]]:
    """Identify Session.read's temporary classification slots used only as values."""
    by_id = {stage.stage_id: stage for stage in plan.stages}
    result: dict[str, tuple[str, ...]] = {}
    for stage in plan.stages:
        if (
            not isinstance(stage, LocalCompute)
            or stage.result is None
            or not isinstance(stage.result.operation, ReadFields)
        ):
            continue
        receiver = by_id[stage.inputs[0]]
        if (
            not isinstance(receiver, LocalCompute)
            or receiver.program is None
            or receiver.program.values
        ):
            continue
        for population in receiver.program.populations:
            if population.definition != receiver.program.output_population:
                continue
            source = by_id[population.stage_id]
            if not isinstance(source, SourceRead) or plan.consumers_of(source.stage_id) != (
                receiver.stage_id,
            ):
                continue
            if plan.consumers_of(receiver.stage_id) != (stage.stage_id,):
                continue
            result[source.stage_id] = tuple(
                population.keys[receiver.keys.index(slot)]
                for position in stage.result.operation.positions
                for slot in stage.result.key_groups[position]
            )
    return result


def _column(column: DataColumn, actual: Mapping[str, DataColumn]) -> DataColumn:
    if not isinstance(column.dtype, DecimalDataType) and column.dtype not in (
        ScalarDataType("int64"),
        ScalarDataType("float64"),
    ):
        return column
    effective = actual.get(column.slot_id)
    if effective is None or effective.name != column.name:
        effective = next((item for item in actual.values() if item.name == column.name), None)
    return (
        column
        if effective is None
        or effective.name != column.name
        or (
            not isinstance(effective.dtype, DecimalDataType)
            and effective.dtype not in (ScalarDataType("int64"), ScalarDataType("float64"))
        )
        else replace(column, dtype=effective.dtype)
    )


def _endpoint(
    endpoint: ComparisonEndpoint | None, actual: Mapping[str, DataColumn]
) -> ComparisonEndpoint | None:
    return (
        replace(endpoint, columns=tuple(_column(column, actual) for column in endpoint.columns))
        if endpoint is not None
        else None
    )


def _formula(
    program: FormulaProgram | None, actual: Mapping[str, DataColumn]
) -> FormulaProgram | None:
    return (
        replace(
            program,
            values=tuple(
                replace(value, column=_column(value.column, actual)) for value in program.values
            ),
        )
        if program is not None
        else None
    )


def _shared_key(column: DataColumn, inputs: tuple[tuple[DataColumn, pd.Series], ...]) -> DataColumn:
    """Choose one actual key type before any contributing coordinates are combined."""
    first = inputs[0][0].dtype
    dtype = (
        first
        if all(item.dtype == first for item, _ in inputs)
        else data_type(dt.highest_precedence(ibis_type(item.dtype) for item, _ in inputs))
    )
    if dtype == ScalarDataType("float64"):
        for item, series in inputs:
            if item.dtype != ScalarDataType("int64"):
                continue
            values = pa.array(series, from_pandas=True)
            if pc.any(
                pc.or_(pc.greater_equal(values, 2**53), pc.less_equal(values, -(2**53)))
            ).as_py():
                raise MaterializationError(
                    expected="complete integer keys preserved by the shared coordinate carrier",
                    received=f"{column.name}: integer keys outside the unambiguous float64 range",
                    repair="Use a source route that preserves one exact key type across all contributions.",
                    stage="stage_output",
                    help_target="analysis_frame.compute",
                )
    return replace(column, dtype=dtype)


def effective_local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> LocalCompute:
    """Reuse the compiler's numeric rules with this invocation's consumed types."""
    actual = {
        column.slot_id: column
        for source in inputs
        for component in source.components
        for column in (*component.layout.columns, *component.layout.raw_columns)
    }
    actual.update(
        {column.slot_id: column for source in reversed(inputs) for column in source.columns}
    )
    if stage.program is not None:
        formula = _formula(stage.program, actual)
        assert formula is not None
        types = {column.slot_id: column.dtype for column in formula_columns(formula)}
        if formula.output_population is not None:
            for index, population in enumerate(formula.populations):
                if population.definition == formula.output_population:
                    source = inputs[len(formula.values) + index]
                    types.update(
                        (target, source.columns[source.keys.index(slot)].dtype)
                        for target, slot in zip(stage.keys, population.keys, strict=True)
                    )
        for column in stage.columns:
            if column.slot_id not in stage.keys:
                continue
            consumed = tuple(
                (actual, source.table[actual.slot_id])
                for source in inputs[: len(formula.values)]
                for actual in source.columns
                if actual.slot_id in source.keys and actual.name == column.name
            )
            if consumed:
                types[column.slot_id] = _shared_key(column, consumed).dtype
        return replace(
            stage,
            program=formula,
            columns=tuple(
                replace(column, dtype=types[column.slot_id])
                if column.slot_id in types
                else _column(column, actual)
                for column in stage.columns
            ),
        )
    program = stage.result
    if program is None or not inputs:
        return stage
    receiver = {column.slot_id: column for column in inputs[0].columns}
    program = replace(
        program,
        columns=tuple(_column(column, receiver) for column in program.columns),
        input_layout=_endpoint(program.input_layout, receiver),
    )
    columns = tuple(_column(column, receiver) for column in stage.columns)
    operation = program.operation
    value_columns = (
        program.input_layout.values
        if program.input_layout is not None
        else tuple(column for column in program.columns if column.slot_id not in program.keys)
    )
    values = tuple(ibis.null().cast(ibis_type(column.dtype)) for column in value_columns)
    coordinates = tuple(
        ibis.null().cast(ibis_type(receiver[group[0]].dtype)) for group in program.key_groups
    )

    def dtype(expression: ValueExpression) -> DataType:
        assert program is not None
        if isinstance(expression, ValueSlot):
            return value_columns[expression.index].dtype
        if isinstance(expression, ValueUnary) and expression.operation in ("abs", "neg"):
            return dtype(expression.operand)
        if isinstance(expression, CoordinateSlot):
            return receiver[program.key_groups[expression.index][0]].dtype
        duration = next(
            (item for item in program.duration_operations if item.expression is expression), None
        )
        if duration is not None:
            return duration.output
        return data_type(expression_value(expression, values, coordinates).type())

    if isinstance(operation, SelectValues):
        selected = {
            f"value.{index}": dtype(expression)
            for index, (_, expression) in enumerate(operation.values)
        }
        columns = tuple(
            replace(column, dtype=selected[column.slot_id])
            if column.slot_id in selected and not isinstance(column.dtype, DurationDataType)
            else column
            for column in columns
        )
    elif isinstance(operation, ReduceValues):
        statistic_types = tuple(
            dtype(statistic.value) if statistic.value is not None else ScalarDataType("int64")
            for _, statistic in operation.values
        )
        program = replace(program, statistic_types=statistic_types)
        output_types = {
            f"value.{index}": ScalarDataType("float64")
            if statistic.method == "mean" and input_type == ScalarDataType("int64")
            else input_type
            for index, ((_, statistic), input_type) in enumerate(
                zip(operation.values, statistic_types, strict=True)
            )
        }
        columns = tuple(
            replace(column, dtype=output_types[column.slot_id])
            if column.slot_id in output_types
            else column
            for column in columns
        )
    elif isinstance(operation, ReadFields):
        output_types = {
            f"value.{index}": receiver[program.key_groups[position][0]].dtype
            for index, position in enumerate(operation.positions)
        }
        columns = tuple(
            replace(column, dtype=output_types[column.slot_id])
            if column.slot_id in output_types
            else column
            for column in columns
        )
    return replace(
        stage,
        columns=columns,
        result=replace(
            program,
            output_layout=_endpoint(
                program.output_layout, {column.slot_id: column for column in columns}
            ),
        ),
    )
