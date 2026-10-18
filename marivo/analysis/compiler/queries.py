"""Compile finite result operations into the existing pandas stage schedule."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo._definition.query import (
    CombineValues,
    ComponentKeys,
    CoordinateSlot,
    DrillValues,
    FilterValues,
    LiteralValues,
    PopulationValues,
    PresenceSlot,
    ProjectKeys,
    ReadFields,
    ReduceValues,
    ResultQuery,
    RollupValues,
    SelectValues,
    ShareValues,
    TopValues,
    ValueBinary,
    ValueBroadcast,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
    operation_dependencies,
)
from marivo._definition.relations import ObserveQuery, QueryKeys
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.binding.correspondence import (
    ComparisonEndpoint,
    bind_correspondence,
    broadcast_mapping,
)
from marivo.analysis.compiler.graph_plan import (
    LocalCompute,
    QueryPlan,
    SourceField,
    SourceRead,
    SourceSchema,
    plan_query,
)
from marivo.analysis.compiler.observation import FormulaProgram, data_type, ibis_type
from marivo.analysis.compiler.query_program import (
    EMPTY_QUERY_CONTEXT,
    BroadcastInput,
    DurationBinary,
    QueryContext,
    ResultProgram,
    SourceParameter,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.graph import BoundGraph, capture_query, input_graph
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.compiler.optimization import InvocationMemo


def endpoint(graph: BoundGraph, plan: QueryPlan) -> ComparisonEndpoint:
    stage = next(item for item in plan.stages if item.stage_id == plan.publish)
    if (
        isinstance(stage, LocalCompute)
        and stage.result is not None
        and stage.result.output_layout is not None
    ):
        return stage.result.output_layout
    return ComparisonEndpoint(
        stage.columns,
        stage.keys,
        graph.signature.axes,
        plan.output_groups or key_groups(graph, stage.columns, stage.keys),
    )


def original_program(plan: QueryPlan) -> FormulaProgram | None:
    if plan.original is not None:
        return plan.original
    stage = next(item for item in plan.stages if item.stage_id == plan.publish)
    if isinstance(stage, LocalCompute):
        return (
            stage.program
            if stage.program is not None
            else stage.result.original
            if stage.result is not None
            else None
        )
    return None


def _ordered_stages(
    plans: tuple[QueryPlan, ...], final: LocalCompute
) -> tuple[SourceRead | LocalCompute, ...]:
    pending = {stage.stage_id: stage for plan in plans for stage in plan.stages}
    pending[final.stage_id] = final
    result: list[SourceRead | LocalCompute] = []
    complete: set[str] = set()
    while pending:
        ready = tuple(
            stage for stage in pending.values() if all(key in complete for key in stage.inputs)
        )
        if not ready:
            reject(
                "an acyclic complete stage dependency graph",
                repr(tuple(pending)),
                "Keep every shared producer before all its consumers.",
                "Query.plan",
            )
        for stage in ready:
            result.append(stage)
            complete.add(stage.stage_id)
            del pending[stage.stage_id]
    return tuple(result)


def _consumers(
    stages: tuple[SourceRead | LocalCompute, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            stage.stage_id,
            tuple(
                consumer.stage_id
                for consumer in stages
                for dependency in consumer.inputs
                if dependency == stage.stage_id
            ),
        )
        for stage in stages
    )


def prefixed_input(captured: QueryKeys, prefix: tuple[int, ...]) -> BoundGraph:
    """Give source occurrences their invocation-local dependency positions."""
    graph = input_graph(captured)
    if isinstance(graph.root, ResultQuery) and prefix:
        return BoundGraph(
            replace(
                graph.root,
                inputs=tuple(
                    capture_query(prefixed_input(item, prefix)) for item in graph.root.inputs
                ),
            ),
            graph.signature,
        )
    observation = graph.signature.observation
    if observation is None or not prefix:
        return graph
    return BoundGraph(
        graph.root,
        replace(
            graph.signature,
            observation=replace(
                observation,
                contributions=tuple(
                    replace(
                        item,
                        occurrence=(*prefix, *item.occurrence),
                        logical_occurrence=item.logical_occurrence or item.occurrence,
                    )
                    for item in observation.contributions
                ),
            ),
        ),
    )


def query_leaves(
    graph: BoundGraph, prefix: tuple[int, ...] = (), context: QueryContext = EMPTY_QUERY_CONTEXT
) -> tuple[BoundGraph, ...]:
    if isinstance(graph.root, ObserveQuery):
        return (graph,)
    result = tuple(
        leaf
        for index, item in enumerate(
            (*graph.root.inputs, *operation_dependencies(graph.root.operation))
        )
        for leaf in query_leaves(prefixed_input(item, (*prefix, index)), (*prefix, index), context)
    )
    if isinstance(graph.root.operation, CombineValues):
        candidates = tuple(
            item
            for item in context.relations
            if item.query is not None
            and item.relation.left_axis in graph.root.inputs[0].axes
            and item.relation.right_axis in graph.root.inputs[1].axes
            and item.relation.via == getattr(graph.root.operation.match, "via", ())
        )
        if len(candidates) == 1:
            captured = candidates[0].query
            assert captured is not None
            result = (
                *result,
                *query_leaves(prefixed_input(captured, (*prefix, 2)), (*prefix, 2), context),
            )
    return result


def expression_value(
    expression: ValueExpression,
    values: tuple[ir.Value, ...],
    keys: tuple[ir.Value, ...],
    references: tuple[tuple[QueryKeys, ir.Value], ...] = (),
) -> ir.Value:
    """Resolve only the finite result grammar over typed scalar placeholders."""
    if isinstance(expression, ValueBroadcast):
        return next(value for definition, value in references if definition == expression.source)
    if isinstance(expression, ValueSlot):
        return values[expression.index]
    if isinstance(expression, CoordinateSlot):
        return keys[expression.index]
    if isinstance(expression, PresenceSlot):
        return ibis.null().cast("boolean")
    if isinstance(expression, ValueLiteral):
        if type(expression.value) is int and not -(2**63) <= expression.value < 2**63:
            from decimal import Decimal

            digits = len(str(abs(expression.value)))
            if digits > 38:
                reject(
                    "a losslessly representable integer or Decimal literal",
                    repr(expression.value),
                    "Use a value within decimal(38,0) or int64.",
                    "Query.prepare",
                )
            return ibis.literal(Decimal(expression.value), type=f"decimal({digits},0)")
        return ibis.literal(expression.value)
    if isinstance(expression, ValueUnary):
        value = expression_value(expression.operand, values, keys, references)
        if expression.operation == "is_null":
            return value.isnull()
        if expression.operation == "not":
            return ~value
        if expression.operation == "abs":
            return value.abs()
        return -value
    left = expression_value(expression.left, values, keys, references)
    right = expression_value(expression.right, values, keys, references)
    if expression.operation in ("add", "sub", "mul", "div"):
        if not all(
            value.type().is_numeric() or value.type().is_interval() for value in (left, right)
        ):
            reject(
                "numeric or Duration arithmetic",
                repr((left.type(), right.type())),
                "Use arithmetic on compatible numeric Columns.",
                "Query.prepare",
            )
        from marivo.semantic._numeric_lowering import NumericValue, numeric_binary

        return numeric_binary(
            {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                expression.operation
            ],
            NumericValue(left),
            NumericValue(right),
            zero_denominator=expression.zero_denominator,
        ).value
    if expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
        left_type, right_type = left.type(), right.type()
        temporal = (
            left_type.is_date()
            or left_type.is_timestamp()
            or right_type.is_date()
            or right_type.is_timestamp()
        )
        if temporal and not (
            (left_type.is_date() and right_type.is_date())
            or (left_type.is_timestamp() and right_type.is_timestamp())
        ):
            reject(
                "matching temporal value kinds",
                f"{left_type}, {right_type}",
                "Compare dates with dates and timezone-aware timestamps with timestamps.",
                "Query.prepare",
            )
    if expression.operation == "add":
        return left + right
    if expression.operation == "sub":
        return left - right
    if expression.operation == "mul":
        return left * right
    if expression.operation == "div":
        return left / right
    if expression.operation == "eq":
        return left == right
    if expression.operation == "ne":
        return left != right
    if expression.operation == "lt":
        return left < right
    if expression.operation == "le":
        return left <= right
    if expression.operation == "gt":
        return left > right
    if expression.operation == "ge":
        return left >= right
    if expression.operation == "and":
        return left & right
    return left | right


def key_groups(
    graph: BoundGraph, columns: tuple[DataColumn, ...], keys: tuple[str, ...]
) -> tuple[tuple[str, ...], ...]:
    """Keep composite identities within their original coordinate boundary."""
    axes = graph.signature.axes
    if not axes:
        return ()
    observation = graph.signature.observation
    if observation is not None and observation.contributions:
        from marivo._definition.types import CalendarCoordinate, coordinate_read_keys

        contribution = observation.contributions[0]
        widths = iter(len(coordinate_read_keys(read)) for read in contribution.coordinates)
        result = []
        offset = 0
        for axis in axes:
            width = 1 if isinstance(axis, CalendarCoordinate) else next(widths)
            result.append(keys[offset : offset + width])
            offset += width
        return tuple(result)
    if len(axes) != len(keys):
        reject(
            "complete coordinate group metadata",
            repr(keys),
            "Preserve composite identity groups when building the input.",
            "query.keys",
        )
    return tuple((key,) for key in keys)


def plan_result(
    graph: BoundGraph,
    schemas: tuple[SourceSchema, ...],
    prefix: tuple[int, ...] = (),
    *,
    context: QueryContext = EMPTY_QUERY_CONTEXT,
    require_original: bool = False,
    memo: InvocationMemo | None = None,
) -> QueryPlan:
    root = graph.root
    assert isinstance(root, ResultQuery)
    if isinstance(root.operation, LiteralValues):
        return plan_literal(graph, root.operation, schemas)
    plans = []
    inputs = []
    for index, captured in enumerate((*root.inputs, *operation_dependencies(root.operation))):
        if memo is not None:
            from marivo.analysis.compiler.optimization import compilation_key

            child = memo.capture(
                compilation_key(
                    input_graph(captured),
                    schemas,
                    context,
                    required=("capture-prefix:" + repr((*prefix, index)),),
                ),
                partial(prefixed_input, captured, (*prefix, index)),
            )
        else:
            child = prefixed_input(captured, (*prefix, index))
        plan = plan_query(
            child,
            schemas,
            context=context,
            require_original=require_original
            or isinstance(root.operation, (RollupValues, ShareValues, DrillValues)),
            memo=memo,
            prefix=(*prefix, index),
        )
        plans.append(plan)
        inputs.append(child)
    if isinstance(root.operation, CombineValues):
        left, right = (endpoint(child, plan) for child, plan in zip(inputs, plans, strict=True))
        combination = root.operation
        relation_inputs = tuple(
            item
            for item in context.relations
            if item.relation.left_axis in left.axes
            and item.relation.right_axis in right.axes
            and item.relation.via == getattr(combination.match, "via", ())
        )
        binding = bind_correspondence(
            left,
            right,
            match=combination.match,
            how=combination.how,
            operation=combination.operation,
            time_axis=combination.time_axis,
            calendar_snapshot=next(
                (axis.snapshot for axis in left.axes if isinstance(axis, CalendarCoordinate)), None
            ),
            relations=tuple(item.relation for item in relation_inputs),
        )
        layout = binding.layout
        relation_groups: tuple[tuple[str, ...], ...] = ()
        if binding.relation_requirement is not None:
            relation_input = next(
                item for item in relation_inputs if item.relation == binding.relation
            )
            assert relation_input.query is not None
            mapping_graph = prefixed_input(relation_input.query, (*prefix, 2))
            mapping_plan = plan_query(
                mapping_graph, schemas, context=context, memo=memo, prefix=(*prefix, 2)
            )
            relation_groups = mapping_plan.output_groups
            plans.append(mapping_plan)
        stage = LocalCompute(
            "local:result:" + graph.fingerprint,
            tuple(plan.publish for plan in plans),
            "query.result@v1",
            layout.columns,
            layout.keys,
            result=ResultProgram(
                combination,
                left.columns,
                left.keys,
                left.axes,
                left.key_groups,
                layout.key_groups,
                input_layout=left,
                output_layout=layout,
                comparison=binding,
                relation_groups=relation_groups,
            ),
        )
        stages = _ordered_stages(tuple(plans), stage)
        consumers = _consumers(stages)
        return QueryPlan(
            stages,
            tuple(check for plan in plans for check in plan.checks),
            consumers,
            stage.stage_id,
            layout.key_groups,
        )
    if isinstance(root.operation, PopulationValues):
        from marivo.analysis.compiler.populations import plan_population_result

        stage = plan_population_result(graph, tuple(plans), tuple(inputs))
        stages = _ordered_stages(tuple(plans), stage)
        consumers = _consumers(stages)
        return QueryPlan(
            stages,
            tuple(check for plan in plans for check in plan.checks),
            consumers,
            stage.stage_id,
            stage.result.output_groups if stage.result is not None else (),
        )
    source = next(stage for stage in plans[0].stages if stage.stage_id == plans[0].publish)
    input_columns = source.columns
    input_layout = endpoint(inputs[0], plans[0])
    value_columns = input_layout.values
    support_columns = tuple(
        column
        for column in input_columns
        if column.slot_id not in source.keys and column not in value_columns
    )
    groups = plans[0].output_groups or (
        key_groups(inputs[0], input_columns, source.keys)
        if isinstance(inputs[0].root, ObserveQuery)
        else next(
            stage.result.output_groups
            for stage in plans[0].stages
            if isinstance(stage, LocalCompute)
            and stage.stage_id == plans[0].publish
            and stage.result is not None
        )
    )
    key_columns = tuple(
        next(column for column in input_columns if column.slot_id == key) for key in source.keys
    )
    placeholders = tuple(ibis.null().cast(ibis_type(column.dtype)) for column in value_columns)
    coordinates = tuple(
        ibis.null().cast(
            ibis_type(next(column for column in key_columns if column.slot_id == group[0]).dtype)
        )
        for group in groups
    )

    broadcast_target = replace(
        input_layout,
        columns=tuple(column for column in input_layout.columns if column not in value_columns),
    )
    references = tuple(
        BroadcastInput(
            definition,
            len(root.inputs) + index,
            endpoint(inputs[len(root.inputs) + index], plans[len(root.inputs) + index]),
            broadcast_target,
            broadcast_mapping(
                endpoint(inputs[len(root.inputs) + index], plans[len(root.inputs) + index]),
                input_layout,
            ),
        )
        for index, definition in enumerate(operation_dependencies(root.operation))
    )
    reference_values = tuple(
        (reference.definition, ibis.null().cast(ibis_type(reference.reference.values[0].dtype)))
        for reference in references
    )

    def scalar_coordinates(expression: ValueExpression) -> None:
        if isinstance(expression, CoordinateSlot):
            group = groups[expression.index]
            if len(group) != 1:
                reject(
                    "a single-component scalar coordinate Column",
                    f"axis {expression.index} has {len(group)} components: {group!r}",
                    "Keep the complete identity as an output coordinate, use Query.population() "
                    "with mv.in_population(...) for tuple predicates, or select a single field "
                    "axis as a value Column.",
                    "Query.coord",
                )
        elif isinstance(expression, ValueUnary):
            scalar_coordinates(expression.operand)
        elif isinstance(expression, ValueBinary):
            scalar_coordinates(expression.left)
            scalar_coordinates(expression.right)

    duration_operations: dict[int, DurationBinary] = {}

    def dtype(expression: ValueExpression) -> DataType:
        scalar_coordinates(expression)
        if isinstance(expression, ValueSlot):
            return value_columns[expression.index].dtype
        if isinstance(expression, ValueBroadcast):
            return next(
                reference.reference.values[0].dtype
                for reference in references
                if reference.definition == expression.source
            )
        if isinstance(expression, ValueUnary) and expression.operation in ("neg", "abs"):
            return dtype(expression.operand)
        if isinstance(expression, CoordinateSlot):
            return next(
                column.dtype
                for column in key_columns
                if column.slot_id == groups[expression.index][0]
            )
        if isinstance(expression, ValueBinary):
            left, right = dtype(expression.left), dtype(expression.right)
            if isinstance(left, DurationDataType) or isinstance(right, DurationDataType):
                numeric_left = isinstance(left, DecimalDataType) or left in (
                    ScalarDataType("int64"),
                    ScalarDataType("float64"),
                )
                numeric_right = isinstance(right, DecimalDataType) or right in (
                    ScalarDataType("int64"),
                    ScalarDataType("float64"),
                )
                output: DurationDataType | ScalarDataType
                if isinstance(left, DurationDataType) and isinstance(right, DurationDataType):
                    if left != right:
                        reject(
                            "matching exact Duration units",
                            repr((left, right)),
                            "Use Duration Columns with the same declared tick unit.",
                            "Query.prepare",
                        )
                    if expression.operation in ("add", "sub"):
                        output = left
                    elif expression.operation == "div":
                        output = ScalarDataType("float64")
                    elif expression.operation in ("eq", "ne", "lt", "le", "gt", "ge"):
                        return ScalarDataType("boolean")
                    else:
                        reject(
                            "Duration addition, subtraction or dimensionless division",
                            expression.operation,
                            "Scale a Duration by a numeric Column or compare matching Duration values.",
                            "Query.prepare",
                        )
                elif (
                    isinstance(left, DurationDataType)
                    and numeric_right
                    and expression.operation in ("mul", "div")
                ):
                    output = left
                elif (
                    isinstance(right, DurationDataType)
                    and numeric_left
                    and expression.operation == "mul"
                ):
                    output = right
                else:
                    reject(
                        "compatible Duration arithmetic operands",
                        repr((expression.operation, left, right)),
                        "Add/subtract matching Duration units, or multiply/divide a Duration by a numeric value.",
                        "Query.prepare",
                    )
                duration_operations[id(expression)] = DurationBinary(
                    expression, left, right, output
                )
                return output
        try:
            return data_type(
                expression_value(expression, placeholders, coordinates, reference_values).type()
            )
        except (TypeError, ValueError) as error:
            reject(
                "a qualified result expression over the prepared types",
                str(error),
                "Use compatible value kinds and arithmetic.",
                "Query.prepare",
            )

    operation = root.operation
    keys = source.keys
    output_keys = key_columns
    final_groups = groups
    statistic_types: tuple[DataType, ...] = ()
    output_groups: list[tuple[str, ...]]
    original = original_program(plans[0])
    if isinstance(operation, ReadFields):
        output_keys = tuple(
            replace(
                next(column for column in key_columns if column.slot_id == slot),
                slot_id=f"key.{index}",
            )
            for index, slot in enumerate(groups[0])
        )
        keys = tuple(column.slot_id for column in output_keys)
        final_groups = (keys,)
        output_values = tuple(
            replace(
                next(column for column in key_columns if column.slot_id == groups[position][0]),
                slot_id=f"value.{index}",
                name=quantity.name,
                unit=quantity.unit,
                nullable=True,
            )
            for index, (position, quantity) in enumerate(
                zip(operation.positions, graph.signature.quantities, strict=True)
            )
        )
    elif isinstance(operation, ProjectKeys):
        projected_groups = tuple(
            groups[position]
            if path is None
            else next(
                projection.columns
                for projection in input_layout.projections
                if projection.path == path and projection.axis == graph.signature.axes[index]
            )
            for index, (position, path) in enumerate(
                zip(operation.positions, operation.paths, strict=True)
            )
        )
        output_keys = tuple(
            replace(
                next(column for column in input_columns if column.slot_id == key),
                slot_id=f"key.{index}",
            )
            for index, key in enumerate(key for group in projected_groups for key in group)
        )
        keys = tuple(column.slot_id for column in output_keys)
        final = []
        offset = 0
        for group in projected_groups:
            final.append(keys[offset : offset + len(group)])
            offset += len(group)
        final_groups = tuple(final)
        output_values = ()
        support_columns = ()
    elif isinstance(operation, SelectValues):
        output_values = tuple(
            DataColumn(f"value.{index}", name, dtype(expression), True, quantity.unit)
            for index, ((name, expression), quantity) in enumerate(
                zip(operation.values, graph.signature.quantities, strict=True)
            )
        )
        if original is not None and all(
            isinstance(expression, ValueSlot) and expression.index < len(original.outputs)
            for _, expression in operation.values
        ):
            selected_values = tuple(
                expression.index
                for _, expression in operation.values
                if isinstance(expression, ValueSlot)
            )
            original = replace(
                original,
                outputs=tuple(
                    (name, original.outputs[index][1])
                    for (name, _), index in zip(operation.values, selected_values, strict=True)
                ),
                values=tuple(
                    replace(value, occurrence=(output_index, *value.occurrence[1:]))
                    for output_index, input_index in enumerate(selected_values)
                    for value in original.values
                    if value.occurrence[0] == input_index
                ),
            )
        else:
            original = None
    elif isinstance(operation, RollupValues):
        if original is None:
            reject(
                "original Metric sufficient inputs",
                "no original formula",
                "Roll up a source Query or a selection of its original columns.",
                "Query.rollup",
            )
        selected = tuple(key for position in operation.positions for key in groups[position])
        output_keys = tuple(
            replace(
                next(column for column in key_columns if column.slot_id == key),
                slot_id=f"key.{index}",
            )
            for index, key in enumerate(selected)
        )
        keys = tuple(column.slot_id for column in output_keys)
        output_groups = []
        offset = 0
        for position in operation.positions:
            width = len(groups[position])
            output_groups.append(keys[offset : offset + width])
            offset += width
        final_groups = tuple(output_groups)
        output_values = value_columns
        support_columns = ()
    elif isinstance(operation, DrillValues):
        if original is None:
            reject(
                "original Metric sufficient inputs",
                "no original formula",
                "Drill a source Query retaining original columns.",
                "Query.drill",
            )
        drills = next(
            (items for identifier, items in context.drills if identifier == graph.fingerprint), ()
        )
        if not drills:
            reject(
                "prepared new attribute templates",
                "no drill schemas",
                "Prepare the source Query before drilling.",
                "Query.drill",
            )
        added_columns = tuple(
            column for column in drills[0].columns if column.slot_id not in drills[0].keys
        )
        output_keys = (
            *key_columns,
            *(
                replace(column, slot_id=f"key.{len(key_columns) + index}")
                for index, column in enumerate(added_columns)
            ),
        )
        keys = tuple(column.slot_id for column in output_keys)
        final_groups = (*groups, *((column.slot_id,) for column in output_keys[len(key_columns) :]))
        output_values = value_columns
        support_columns = ()
        extra_stages: list[LocalCompute | SourceRead] = []
        for schema in drills:
            selector = LocalCompute(
                "local:drill-identities:" + schema.source_id,
                (source.stage_id,),
                "query.result@v1",
                tuple(column for column, _ in schema.identity),
                schema.keys,
                result=ResultProgram(
                    ComponentKeys(schema.value_slot),
                    input_columns,
                    source.keys,
                    inputs[0].signature.axes,
                    groups,
                    (),
                    original=original,
                ),
            )
            attribute = SourceRead(
                "source:" + schema.source_id,
                schema.source_id,
                (),
                schema.columns,
                schema.keys,
                schema.projections,
                (),
                (),
                None,
                preparation_checks=schema.preparation_checks,
                inputs=(selector.stage_id,),
                parameters=(
                    SourceParameter(selector.stage_id, schema.parameter, selector.columns),
                ),
            )
            extra_stages.extend((selector, attribute))
        plans[0] = replace(plans[0], stages=(*plans[0].stages, *extra_stages))
    elif isinstance(operation, ShareValues):
        if original is None:
            reject(
                "original additive Metric sufficient inputs",
                "no original formula",
                "Compute share from a source Query with retained original columns.",
                "Query.share",
            )
        output_values = (
            *value_columns,
            DataColumn(
                f"value.share.{operation.value_index}",
                graph.signature.quantities[-1].name,
                ScalarDataType("float64"),
                True,
            ),
        )
    elif isinstance(operation, ReduceValues):
        selected = tuple(key for position in operation.positions for key in groups[position])
        output_keys = tuple(
            replace(
                next(column for column in key_columns if column.slot_id == key),
                slot_id=f"key.{index}",
            )
            for index, key in enumerate(selected)
        )
        keys = tuple(column.slot_id for column in output_keys)
        output_groups = []
        offset = 0
        for position in operation.positions:
            width = len(groups[position])
            output_groups.append(keys[offset : offset + width])
            offset += width
        final_groups = tuple(output_groups)
        statistic_types = tuple(
            dtype(statistic.value) if statistic.value is not None else ScalarDataType("int64")
            for _, statistic in operation.values
        )
        output_values = tuple(
            DataColumn(
                f"value.{index}",
                name,
                ScalarDataType("int64")
                if statistic.method == "row_count"
                else ScalarDataType("float64")
                if statistic.method == "mean"
                and statistic.value is not None
                and dtype(statistic.value) == ScalarDataType("int64")
                else dtype(statistic.value)
                if statistic.value is not None
                else ScalarDataType("int64"),
                True,
                quantity.unit,
            )
            for index, ((name, statistic), quantity) in enumerate(
                zip(operation.values, graph.signature.quantities, strict=True)
            )
        )
    else:
        output_values = value_columns
        if isinstance(operation, TopValues):
            dtype(operation.score)
        elif (
            isinstance(operation, FilterValues)
            and isinstance(
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
            )
            and dtype(operation.predicate) != ScalarDataType("boolean")
        ):
            reject(
                "a boolean result predicate",
                repr(dtype(operation.predicate)),
                "Compare compatible values.",
                "Query.where",
            )
    stage = LocalCompute(
        "local:result:" + graph.fingerprint,
        (
            *tuple(plan.publish for plan in plans),
            *(item.stage_id for item in extra_stages if isinstance(item, SourceRead)),
        )
        if isinstance(operation, DrillValues)
        else tuple(plan.publish for plan in plans),
        "query.result@v1",
        (*output_keys, *output_values, *support_columns)
        if not isinstance(operation, (ReadFields, ReduceValues, ProjectKeys))
        else (*output_keys, *output_values),
        keys,
        result=ResultProgram(
            operation,
            input_columns,
            source.keys,
            inputs[0].signature.axes,
            groups,
            final_groups,
            statistic_types,
            population_inputs=operation.domains if isinstance(operation, FilterValues) else (),
            population_layouts=tuple(plan.output_groups for plan in plans[1 : len(root.inputs)])
            if isinstance(operation, FilterValues)
            else (),
            input_layout=input_layout,
            references=references,
            duration_operations=tuple(duration_operations.values()),
            output_layout=ComparisonEndpoint(
                (*output_keys, *output_values, *support_columns),
                keys,
                graph.signature.axes,
                final_groups,
                input_layout.presence,
                input_layout.projections,
            )
            if not isinstance(operation, (ReadFields, ReduceValues, ProjectKeys))
            else ComparisonEndpoint(output_keys, keys, graph.signature.axes, final_groups)
            if isinstance(operation, ProjectKeys)
            else None,
            original=original
            if isinstance(
                operation,
                (SelectValues, FilterValues, TopValues, RollupValues, ShareValues, DrillValues),
            )
            else None,
            drills=drills if isinstance(operation, DrillValues) else (),
        ),
    )
    stages = _ordered_stages(tuple(plans), stage)
    consumers = _consumers(stages)
    return QueryPlan(
        stages,
        tuple(check for plan in plans for check in plan.checks),
        consumers,
        stage.stage_id,
        final_groups,
    )


def plan_literal(
    graph: BoundGraph, operation: LiteralValues, schemas: tuple[SourceSchema, ...] = ()
) -> QueryPlan:
    """Infer finite literal carriers once and execute through a pandas input stage."""
    import math
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    import ibis.expr.datatypes as dt

    from marivo._definition.types import AxisBinding, LiteralValue

    normalized = []
    for index in range(len(operation.columns)):
        values = tuple(row[index] for _, row in operation.rows)
        floating = any(isinstance(value, float) for value in values)
        decimal = any(
            isinstance(value, Decimal) or (type(value) is int and not -(2**63) <= value < 2**63)
            for value in values
        )
        converted = tuple(
            float(value)
            if floating and isinstance(value, (int, float, Decimal))
            else Decimal(value)
            if decimal and isinstance(value, (int, Decimal))
            else value.astimezone(timezone.utc)
            if isinstance(value, datetime)
            else value
            for value in values
        )
        if any(isinstance(value, float) and not math.isfinite(value) for value in converted):
            reject(
                "finite promoted float64 values",
                repr(values),
                "Use values within the admitted numeric range.",
                "Session.values",
            )
        normalized.append(converted)
    operation = replace(
        operation,
        rows=tuple(
            (keys, tuple(normalized[index][position] for index in range(len(operation.columns))))
            for position, (keys, _) in enumerate(operation.rows)
        ),
    )

    def inferred(values: tuple[LiteralValue, ...]) -> DataType:
        try:
            present = tuple(value for value in values if value is not None)
            if present and all(isinstance(value, timedelta) for value in present):
                nonzero = tuple(value for value in present if value != timedelta(0))
                if not nonzero:
                    return DurationDataType("us")
                return data_type(dt.highest_precedence(dt.infer(value) for value in nonzero))
            decimals = tuple(value for value in values if isinstance(value, Decimal))
            if decimals:
                scales: list[int] = []
                for value in decimals:
                    exponent = value.as_tuple().exponent
                    assert isinstance(exponent, int)
                    scales.append(max(0, -exponent))
                scale = max(scales)
                precision = max(1, max(max(0, value.adjusted() + 1) for value in decimals) + scale)
                if precision > 38:
                    raise ValueError(f"Decimal literal precision {precision} exceeds 38")
                return DecimalDataType(precision, scale)
            return data_type(
                dt.highest_precedence(dt.infer(value) for value in values if value is not None)
            )
        except (TypeError, ValueError) as error:
            reject(
                "losslessly representable finite literal values",
                str(error),
                "Use compatible values within the admitted numeric precision.",
                "Session.values",
            )

    def check_key(value: LiteralValue, column: SourceField) -> None:
        from datetime import date, datetime, timedelta
        from decimal import Decimal, localcontext

        dtype = column.dtype
        valid = (
            column.nullable
            if value is None
            else (
                type(value) is int and -(2**63) <= value < 2**63
                if dtype == ScalarDataType("int64")
                else type(value) is float
                if dtype == ScalarDataType("float64")
                else type(value) is bool
                if dtype == ScalarDataType("boolean")
                else type(value) is str
                if dtype == ScalarDataType("string")
                else isinstance(value, date) and not isinstance(value, datetime)
                if dtype == ScalarDataType("date")
                else isinstance(value, datetime)
                and value.tzinfo is not None
                and value.utcoffset() is not None
                if isinstance(dtype, TimestampDataType)
                else isinstance(value, timedelta)
                if isinstance(dtype, DurationDataType)
                else isinstance(value, Decimal)
            )
        )
        if (
            valid
            and value is not None
            and isinstance(dtype, DecimalDataType)
            and isinstance(value, Decimal)
        ):
            with localcontext() as context:
                context.prec = 120
                valid = value == value.quantize(Decimal(1).scaleb(-dtype.scale)) and abs(
                    value
                ) < Decimal(10) ** (dtype.precision - dtype.scale)
        if not valid:
            reject(
                f"{column.field}: a lossless {dtype!r} coordinate",
                repr(value),
                "Use the declared Field kind or every complete identity key component.",
                "Session.values",
            )

    schema = next(
        (item for item in schemas if item.source_id == "literal:" + graph.fingerprint), None
    )
    groups: list[tuple[str, ...]] = []
    columns: list[DataColumn] = []
    for index, axis in enumerate(graph.signature.axes):
        assert isinstance(axis, AxisBinding)
        key = operation.rows[0][0][index]
        components = key if isinstance(key, tuple) else (key,)
        if schema is None or len(components) != len(schema.key_groups[index]):
            reject(
                "the complete declared coordinate key schema",
                repr(key),
                "Prepare the actual source schema and provide every identity component.",
                "Session.values",
            )
        group = []
        for component in range(len(components)):
            slot = f"key.{len(columns)}"
            samples_list: list[LiteralValue] = []
            for row, _ in operation.rows:
                value = row[index]
                samples_list.append(value[component] if isinstance(value, tuple) else value)
            samples = tuple(samples_list)
            for value in samples:
                check_key(value, schema.keys[len(columns)])
            columns.append(
                DataColumn(
                    slot,
                    axis.field.ref.path if axis.field is not None else axis.entity.ref.path,
                    schema.keys[len(columns)].dtype,
                    axis.field is not None,
                )
            )
            group.append(slot)
        groups.append(tuple(group))
    keys = tuple(column.slot_id for column in columns)
    columns.extend(
        DataColumn(
            f"value.{index}",
            name,
            inferred(tuple(values[index] for _, values in operation.rows)),
            False,
            quantity.unit,
        )
        for index, (name, quantity) in enumerate(
            zip(operation.columns, graph.signature.quantities, strict=True)
        )
    )
    stage = LocalCompute(
        "literal:" + graph.fingerprint,
        (),
        "query.result@v1",
        tuple(columns),
        keys,
        result=ResultProgram(operation, (), (), (), (), tuple(groups)),
    )
    return QueryPlan((stage,), (), (), stage.stage_id, tuple(groups))
