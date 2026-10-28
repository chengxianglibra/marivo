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
    Statistic,
    ValueExpression,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.binding.correspondence import ComparisonEndpoint
from marivo.analysis.compiler.frame_plan import frame_program
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    LocalCompute,
    QueryPlan,
    SourceRead,
    local_result,
)
from marivo.analysis.compiler.observation import FormulaProgram, formula_columns
from marivo.analysis.compiler.query_program import (
    FrameCombineProgram,
    FrameCompareProgram,
    ResultProgram,
)
from marivo.analysis.core.attribute_game import AttributeGameProgram
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.frame_expression import (
    infer_frame_combine_schema,
    infer_frame_compare_schema,
)
from marivo.analysis.core.value_types import data_type, expression_value, ibis_type
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput


def effective_frame_schema(schema: DataSchema, columns: tuple[DataColumn, ...]) -> DataSchema:
    """Carry exact-slot native numeric value types without changing coordinates."""
    actual = {column.slot_id: column for column in columns}

    def numeric(dtype: DataType) -> bool:
        return isinstance(dtype, DecimalDataType) or dtype in (
            ScalarDataType("int64"),
            ScalarDataType("float64"),
        )

    def value(column: DataColumn) -> DataColumn:
        effective = actual.get(column.slot_id)
        return (
            replace(column, dtype=effective.dtype)
            if effective is not None and numeric(column.dtype) and numeric(effective.dtype)
            else column
        )

    return replace(schema, values=tuple(value(column) for column in schema.values))


def effective_game_program(
    program: AttributeGameProgram, inputs: tuple[StageOutput, ...]
) -> AttributeGameProgram:
    """Resolve one game's report carriers from its consumed typed source results."""
    from marivo.analysis.core.attribute_game import game_schema

    schemas = tuple(
        item.frame_schema or effective_frame_schema(schema, item.columns)
        for schema, item in zip(
            (program.parent_schema, program.left_schema, program.right_schema),
            inputs[:3],
            strict=True,
        )
    )
    output = game_schema(schemas[1], schemas[2], schemas[0], program.operation)
    assert isinstance(output, DataSchema)
    return replace(
        program,
        parent_schema=schemas[0],
        left_schema=schemas[1],
        right_schema=schemas[2],
        output_schema=output,
    )


def effective_frame(stage: FrameCompute, inputs: tuple[StageOutput, ...]) -> FrameCompute:
    """Infer this invocation's local Frame program from consumed value types."""
    program = stage.program

    if isinstance(program, AttributeGameProgram):
        return replace(stage, program=effective_game_program(program, inputs))
    from marivo.analysis.core.frame_attribute_merge import (
        AttributeMergeProgram,
        infer_attribute_merge_schema,
    )

    if isinstance(program, AttributeMergeProgram):
        schemas = tuple(
            item.frame_schema or effective_frame_schema(schema, item.columns)
            for schema, item in zip(program.input_schemas, inputs, strict=True)
        )
        return replace(
            stage,
            program=AttributeMergeProgram(
                program.operation, schemas, infer_attribute_merge_schema(schemas)
            ),
        )
    from marivo.analysis.core.attribute import (
        AttributeContributionProgram,
        AttributeInputProgram,
        AttributeProjectProgram,
    )

    def attribute_program(
        program: AttributeInputProgram, columns: Mapping[str, DataColumn]
    ) -> AttributeInputProgram:
        matrix = tuple(
            replace(item, target=replace(item.target, dtype=columns[item.target.slot_id].dtype))
            if item.target.slot_id in columns
            else item
            for item in program.matrix
        )
        sides: list[DataSchema] = []
        for side, schema in (("left", program.left_schema), ("right", program.right_schema)):
            values = list(schema.values)
            for item in matrix:
                if (
                    item.side == side
                    and item.role == "native_value"
                    and item.scope == "fine"
                    and len(item.occurrence) == 1
                ):
                    index = item.occurrence[0]
                    if program.operation.source_indices is not None:
                        selected = program.operation.source_indices[0 if side == "left" else 1]
                        if index != selected:
                            continue
                        index = 0
                    values[index] = replace(values[index], dtype=item.target.dtype)
            sides.append(replace(schema, values=tuple(values)))
        return replace(
            program,
            matrix=matrix,
            left_schema=sides[0],
            right_schema=sides[1],
            output_schema=replace(
                program.output_schema, values=tuple(item.target for item in matrix)
            ),
        )

    if isinstance(program, AttributeContributionProgram):
        from marivo.analysis.core.attribute import contribution_schema

        updated = attribute_program(
            program.input_program, {column.slot_id: column for column in inputs[0].columns}
        )
        output = contribution_schema(updated, program.operation)
        exact_carriers = all(
            column.dtype == ScalarDataType("int64")
            or (isinstance(column.dtype, DecimalDataType) and column.dtype.scale == 0)
            for path, column in zip(output.value_paths, output.values, strict=True)
            if path[0] in ("total", "contribution")
        ) and all(
            item.target.dtype == ScalarDataType("int64")
            or (isinstance(item.target.dtype, DecimalDataType) and item.target.dtype.scale == 0)
            for item in updated.matrix
            if item.role in ("value", "native_value", "additive_value")
        )
        return replace(
            stage,
            program=replace(
                program,
                input_program=updated,
                output_schema=output,
                exact_integer_closure=program.exact_integer_closure and exact_carriers,
            ),
        )
    if isinstance(program, AttributeInputProgram):
        from marivo.analysis.materialization.query_components import select_attribute_root

        roots = tuple(
            root
            for binding in program.reads
            for root in inputs[binding.input_index].attribute_inputs
        )
        columns = {}
        for item in program.matrix:
            source = item.sources[0]
            root = select_attribute_root(roots, item.scope, source)
            columns[item.target.slot_id] = next(
                column for column in root.layout.columns if column.slot_id == source.source
            )
        return replace(stage, program=attribute_program(program, columns))
    if isinstance(program, AttributeProjectProgram):
        return stage
    if isinstance(program, FrameCompareProgram):
        schemas = tuple(
            item.frame_schema or effective_frame_schema(schema, item.columns)
            for schema, item in zip(program.input_schemas, inputs, strict=True)
        )
        return replace(
            stage,
            program=FrameCompareProgram(
                program.operation, schemas, infer_frame_compare_schema(schemas, program.operation)
            ),
        )
    if isinstance(program, FrameCombineProgram):
        left = inputs[0].frame_schema or effective_frame_schema(
            program.left_schema, inputs[0].columns
        )
        right = inputs[1].frame_schema or effective_frame_schema(
            program.right_schema, inputs[1].columns
        )
        return replace(
            stage,
            program=FrameCombineProgram(
                program.operation,
                left,
                right,
                infer_frame_combine_schema(left, right, program.operation),
            ),
        )
    schema = inputs[0].frame_schema or effective_frame_schema(
        program.input_schema, inputs[0].columns
    )
    return replace(stage, program=frame_program(schema, program.operation))


def source_value_keys(plan: QueryPlan) -> dict[str, tuple[str, ...]]:
    """Identify Session.read's temporary classification slots used only as values."""
    by_id = {stage.stage_id: stage for stage in plan.stages}
    result: dict[str, tuple[str, ...]] = {}
    for stage in plan.stages:
        if (
            not isinstance(stage, LocalCompute)
            or not isinstance(stage.program, ResultProgram)
            or not isinstance(stage.program.operation, ReadFields)
        ):
            continue
        receiver = by_id[stage.inputs[0]]
        if (
            not isinstance(receiver, LocalCompute)
            or not isinstance(receiver.program, FormulaProgram)
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
                for position in stage.program.operation.positions
                for slot in stage.program.key_groups[position]
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
    if isinstance(stage.program, FormulaProgram):
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
    program = local_result(stage)
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
        return data_type(
            expression_value(expression, values, coordinates, for_type_inference=True).type()
        )

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
        input_types: list[DataType] = []
        for _, statistic in operation.values:
            assert isinstance(statistic, Statistic)  # Enforced by ResultProgram.__post_init__.
            input_types.append(
                dtype(statistic.value) if statistic.value is not None else ScalarDataType("int64")
            )
        statistic_types = tuple(input_types)
        program = replace(program, statistic_types=statistic_types)
        output_types = {
            f"value.{index}": ScalarDataType("float64")
            if statistic.method == "mean" and input_type == ScalarDataType("int64")
            else DecimalDataType(
                38, input_type.scale if isinstance(input_type, DecimalDataType) else 0
            )
            if statistic.method == "sum"
            and (input_type == ScalarDataType("int64") or isinstance(input_type, DecimalDataType))
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
        program=replace(
            program,
            output_layout=_endpoint(
                program.output_layout, {column.slot_id: column for column in columns}
            ),
        ),
    )
