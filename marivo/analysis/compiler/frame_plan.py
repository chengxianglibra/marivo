"""Compose explicit source and fixed inputs into the existing stage schedule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Generic

from marivo._definition.query import (
    PairStatistic,
    ReduceValues,
    Statistic,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValuePartition,
    ValueUnary,
)
from marivo._definition.relations import AttributeKeys, FrameKeys
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.attribute_inputs import attribute_input_program, exact_integer_closure
from marivo.analysis.compiler.attribute_native_queries import native_game_stage
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan, CompiledSourceFrame
from marivo.analysis.compiler.graph_plan import with_unique_keys
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.populations import plan_frame_keys
from marivo.analysis.compiler.query_namespace import _name, _prefix, _prefix_read
from marivo.analysis.compiler.query_program import (
    DurationBinary,
    FrameCombineProgram,
    FrameCompareProgram,
    FrameProgram,
)
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    FrameInput,
    FrameKeysInput,
    FusionGroup,
    LocalCompute,
    QueryPlan,
    QueryStage,
    RuleDecision,
    SourceReadStage,
    read_attribute,
    require_attribute,
)
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeFormulaBinding,
    AttributeInputProgram,
    AttributeReadBinding,
    AttributeSourceParentBinding,
    contribution_schema,
    pending_contribution_schema,
)
from marivo.analysis.core.attribute_game import AttributeGameProgram, game_schema
from marivo.analysis.core.frame_expression import (
    infer_frame_combine_schema,
    infer_frame_compare_schema,
    transport_logical_facts,
)
from marivo.analysis.core.frame_operations import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeGameValues,
    AttributeInputsValues,
    AttributeProjectValues,
    CompareFrameValues,
    FixedFrameInput,
    FrameExpression,
    FrameOperation,
    FrameUnaryOperationKind,
    SourceFrameInput,
)
from marivo.analysis.core.frame_value_analysis import (
    expression_type,
    expressions,
    infer_frame_schema,
    select_attribute_source_schema,
    statistic_expressions,
)
from marivo.analysis.core.value_normalization import physical_operation
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.data_types import DataType, DurationDataType, ScalarDataType
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_attribute_merge import (
    AttributeMergeProgram,
    AttributeMergeValues,
    infer_attribute_merge_schema,
)
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.analysis.model.frame_structure import PendingDataSchema, require_complete_structure


def _durations(schema: DataSchema, expression: ValueExpression) -> tuple[DurationBinary, ...]:
    if isinstance(expression, ValuePartition):
        return tuple(
            item
            for value in statistic_expressions(expression.statistic)
            for item in _durations(schema, value)
        )
    if isinstance(expression, ValueUnary):
        return _durations(schema, expression.operand)
    if not isinstance(expression, ValueBinary):
        return ()
    result = (*_durations(schema, expression.left), *_durations(schema, expression.right))
    left, right = (
        expression_type(schema, expression.left),
        expression_type(schema, expression.right),
    )
    if isinstance(left, DurationDataType) or isinstance(right, DurationDataType):
        output = expression_type(schema, expression)
        if expression.operation in ("add", "sub", "mul", "div"):
            if (
                isinstance(expression.left, ValueLiteral)
                and expression.left.value == timedelta(0)
                and isinstance(right, DurationDataType)
            ):
                left = right
            if (
                isinstance(expression.right, ValueLiteral)
                and expression.right.value == timedelta(0)
                and isinstance(left, DurationDataType)
            ):
                right = left
            assert isinstance(output, (DurationDataType, ScalarDataType))
            result = (*result, DurationBinary(expression, left, right, output))
    return result


def _statistic_types(
    schema: DataSchema, statistic: Statistic | PairStatistic
) -> tuple[DataType, ...]:
    if isinstance(statistic, PairStatistic):
        return (
            expression_type(schema, statistic.left),
            expression_type(schema, statistic.right),
        )
    return () if statistic.value is None else (expression_type(schema, statistic.value),)


def frame_program(
    schema: DataSchema,
    operation: FrameUnaryOperationKind,
    logical_schema: DataSchema | PendingDataSchema | None = None,
) -> FrameProgram:
    """Infer one unary program from the concrete types consumed by its kernel."""
    logical_output = (
        infer_frame_schema(schema, operation) if logical_schema is None else logical_schema
    )
    operation = physical_operation(operation, len(schema.axes))
    return FrameProgram(
        operation,
        schema,
        transport_logical_facts(infer_frame_schema(schema, operation), logical_output),
        tuple(_statistic_types(schema, stat) for _, stat in operation.values)
        if isinstance(operation, ReduceValues)
        else (),
        tuple(item for expr in expressions(operation) for item in _durations(schema, expr)),
    )


@dataclass(slots=True)
class _FramePlanner(Generic[CheckedStageInputT]):
    source_plans: Mapping[str, CompiledSourceFrame[CheckedStageInputT]]
    frame_inputs: Mapping[str, FrameExpression]
    stages: list[QueryStage] = field(default_factory=list)
    reads: list[LoweredRead] = field(default_factory=list)
    groups: list[FusionGroup] = field(default_factory=list)
    decisions: list[RuleDecision] = field(default_factory=list)
    owners: list[tuple[str, str]] = field(default_factory=list)
    boundaries: list[tuple[str, str]] = field(default_factory=list)
    leaves: dict[str, tuple[str, DataSchema]] = field(default_factory=dict)
    identities: dict[str, SourceFrameInput | FixedFrameInput] = field(default_factory=dict)
    projections: dict[str, str] = field(default_factory=dict)
    native_sources: dict[str, CompiledSourceFrame[CheckedStageInputT]] = field(default_factory=dict)
    operations: dict[int, tuple[str, DataSchema]] = field(default_factory=dict)
    operation_nodes: dict[int, FrameOperation] = field(default_factory=dict)
    attribute_preparations: list[FrameCompute] = field(default_factory=list)
    checked_inputs: list[tuple[str, CheckedStageInputT]] = field(default_factory=list)

    def visit(self, node: FrameExpression) -> tuple[str, DataSchema]:
        if isinstance(node, (SourceFrameInput, FixedFrameInput)) and node.input_id in self.leaves:
            saved = self.leaves[node.input_id]
            previous = self.identities[node.input_id]
            if (
                type(previous) is not type(node)
                or (isinstance(node, FixedFrameInput) and saved[1] != node.schema)
                or (
                    isinstance(node, SourceFrameInput)
                    and isinstance(previous, SourceFrameInput)
                    and previous.graph is not node.graph
                )
            ):
                reject(
                    "one exact schema per fixed input",
                    node.input_id,
                    "Use the original input binding.",
                    "frame.plan",
                )
            return saved
        if isinstance(node, FixedFrameInput):
            return self._fixed_input(node)
        if isinstance(node, SourceFrameInput):
            return self._source_input(node)
        assert isinstance(node, FrameOperation)
        self.operation_nodes[id(node)] = node
        existing = self.operations.get(id(node))
        if existing is not None:
            return existing

        if isinstance(node.operation, AttributeMergeValues):
            return self._attribute_merge(node, node.operation)
        if isinstance(node.operation, AttributeGameValues):
            return self._attribute_game(node, node.operation)
        if isinstance(node.operation, AttributeContributionValues):
            return self._attribute_contribution(node, node.operation)
        if isinstance(node.operation, AttributeInputsValues):
            return self._attribute_inputs(node, node.operation)
        if isinstance(node.operation, CompareFrameValues):
            return self._compare(node, node.operation)
        if isinstance(node.operation, AlignFrameValues):
            return self._align(node, node.operation)
        if len(node.inputs) != 1:
            reject(
                "one explicit input for an ordinary Frame operation",
                str(len(node.inputs)),
                "Use ordinary operations on their receiver.",
                "frame.plan",
            )
        source, schema = self.visit(node.inputs[0])
        assert not isinstance(node.operation, AttributeProjectValues)
        program = frame_program(schema, node.operation, node.logical_schema)
        output_schema = program.output_schema
        compute = FrameCompute(f"frame:{len(self.stages)}", (source,), program)
        self.stages.append(compute)
        self.operations[id(node)] = (compute.stage_id, output_schema)
        return compute.stage_id, output_schema

    def _fixed_input(self, node: FixedFrameInput) -> tuple[str, DataSchema]:
        require_complete_structure(node.schema)
        stage = FrameInput(f"fixed:{len(self.leaves)}", node.input_id, node.schema)
        self.stages.append(stage)
        result = (stage.stage_id, node.schema)
        self.leaves[node.input_id] = result
        self.identities[node.input_id] = node
        return result

    def _source_input(self, node: SourceFrameInput) -> tuple[str, DataSchema]:
        if node.input_id not in self.source_plans:
            reject(
                "a prepared plan for each explicit graph input",
                node.input_id,
                "Prepare only the explicit source leaves.",
                "frame.plan",
            )
        child = self.source_plans[node.input_id]
        require_complete_structure(child.schema)
        external: dict[str, str] = {}
        for key_stage in child.lowered.plan.stages:
            if not isinstance(key_stage, FrameKeysInput):
                continue
            definition = key_stage.parameter.definition.root
            assert isinstance(definition, (FrameKeys, AttributeKeys))
            source_expression = self.frame_inputs.get(definition.input_id)
            if source_expression is None:
                reject(
                    "a retained invocation Frame input",
                    definition.input_id,
                    "Retain Frame-key sidecars before source capture.",
                    "frame.population",
                )
            source, schema = self.visit(source_expression)

            projection = plan_frame_keys(key_stage.parameter, source, schema)
            if projection.stage_id not in self.projections:
                self.stages.append(projection)
                self.projections[projection.stage_id] = projection.stage_id
            external[key_stage.stage_id] = projection.stage_id
        namespace = f"source:{len(self.leaves)}"
        names = {
            s.stage_id: namespace + "/" + s.stage_id
            for s in child.lowered.plan.stages
            if not isinstance(s, FrameKeysInput)
        }
        names.update(external)
        self.checked_inputs.extend((names[name], value) for name, value in child.checked_inputs)
        prefixed = tuple(
            _prefix(s, names, namespace)
            for s in child.lowered.plan.stages
            if not isinstance(s, FrameKeysInput)
        )
        self.native_sources[node.input_id] = replace(
            child,
            native_reads=tuple(_prefix_read(read, names, namespace) for read in child.native_reads),
        )
        self.stages.extend(prefixed)
        for item in child.lowered.reads:
            self.reads.append(replace(item, stage_id=names[item.stage_id]))
        self.groups.extend(
            replace(
                group,
                members=tuple(namespace + "/" + member for member in group.members),
                output=_name(names, group.output, namespace),
            )
            for group in child.lowered.plan.fusion_groups
        )
        self.decisions.extend(
            replace(
                decision, members=tuple(namespace + "/" + member for member in decision.members)
            )
            for decision in child.lowered.plan.optimization_decisions
        )
        self.owners.extend((s.stage_id, node.input_id) for s in prefixed)
        output = names[child.lowered.plan.publish]
        self.boundaries.append((output, node.input_id))
        result = (output, child.schema)
        self.leaves[node.input_id] = result
        self.identities[node.input_id] = node
        return result

    def _attribute_merge(
        self, node: FrameOperation, operation: AttributeMergeValues
    ) -> tuple[str, DataSchema]:
        children = tuple(self.visit(item) for item in node.inputs)
        merged_schema = infer_attribute_merge_schema(tuple(schema for _, schema in children))
        assert isinstance(merged_schema, DataSchema)
        merge_program = AttributeMergeProgram(
            operation, tuple(schema for _, schema in children), merged_schema
        )
        merged = FrameCompute(
            f"frame:{len(self.stages)}",
            tuple(identifier for identifier, _ in children),
            merge_program,
        )
        self.stages.append(merged)
        self.operations[id(node)] = (merged.stage_id, merged_schema)
        return merged.stage_id, merged_schema

    def _attribute_game(
        self, node: FrameOperation, operation: AttributeGameValues
    ) -> tuple[str, DataSchema]:
        parent, parent_schema = self.visit(node.inputs[0])
        self._prepare_attribute_projections(node.inputs[1:], parent, parent_schema)
        left, left_schema = self.visit(node.inputs[1])
        right, right_schema = self.visit(node.inputs[2])
        game_output = game_schema(left_schema, right_schema, parent_schema, operation)
        assert isinstance(game_output, DataSchema)
        original_slots = []
        state_slots = []
        for endpoint, game in zip(node.inputs[1:], operation.games, strict=True):
            assert isinstance(endpoint, SourceFrameInput)
            formula = next(
                stage.program
                for stage in reversed(self.source_plans[endpoint.input_id].lowered.plan.stages)
                if isinstance(stage, LocalCompute) and isinstance(stage.program, FormulaProgram)
            )
            original_slots.append(
                tuple(
                    (
                        leaf.occurrence,
                        next(
                            value.column.slot_id
                            for value in formula.values
                            if value.occurrence == (game.target_slot, *leaf.occurrence[1:])
                        ),
                    )
                    for leaf in game.leaves
                )
            )
            state_slots.append(
                tuple(
                    (value.occurrence[0], value.column.slot_id)
                    for value in formula.values
                    if len(value.occurrence) >= 1
                )
            )
        game_program = AttributeGameProgram(
            operation,
            parent_schema,
            left_schema,
            right_schema,
            game_output,
            (original_slots[0], original_slots[1]),
            (state_slots[0], state_slots[1]),
        )
        bridges = tuple(
            "attribute-bridge:" + identifier
            for identifier in (
                operation.mapping.left_projection,
                operation.mapping.right_projection,
            )
        )
        endpoint_ids = tuple(
            endpoint.input_id
            for endpoint in node.inputs[1:]
            if isinstance(endpoint, SourceFrameInput)
        )
        if any(not leaf.state_eligible for game in operation.games for leaf in game.leaves) or any(
            {slot for _, slot in original_slots[side]}.intersection(
                self.source_plans[input_id].native_value_slots
            )
            for side, input_id in enumerate(endpoint_ids)
        ):
            assert len(endpoint_ids) == 2
            compute: FrameCompute | SourceReadStage = native_game_stage(
                f"frame:{len(self.stages)}",
                (parent, left, right, *bridges),
                game_program,
                (endpoint_ids[0], endpoint_ids[1]),
                self.native_sources,
            )
        else:
            compute = FrameCompute(
                f"frame:{len(self.stages)}", (parent, left, right, *bridges), game_program
            )
        self.stages.append(compute)
        self.operations[id(node)] = (compute.stage_id, game_output)
        return compute.stage_id, game_output

    def _attribute_contribution(
        self, node: FrameOperation, operation: AttributeContributionValues
    ) -> tuple[str, DataSchema]:
        preparation = FrameOperation(
            node.inputs,
            AttributeInputsValues(operation.mapping, operation.source_indices),
        )
        prepared, _ = self.visit(preparation)
        input_stage = next(s for s in self.stages if s.stage_id == prepared)
        assert isinstance(input_stage, FrameCompute)

        assert isinstance(input_stage.program, AttributeInputProgram)

        source_owners = dict(self.owners)
        for side_index, endpoint_node in enumerate(node.inputs[1:]):
            assert isinstance(endpoint_node, SourceFrameInput)
            source_schema = (
                input_stage.program.left_schema
                if side_index == 0
                else input_stage.program.right_schema
            )
            positions = (
                operation.mapping.left_parents
                if side_index == 0
                else operation.mapping.right_parents
            )
            parent_columns = tuple(
                c for position in positions for c in source_schema.axes[position].columns
            )
            use_records = tuple(
                dict.fromkeys(
                    source.use
                    for item in input_stage.program.matrix
                    if item.side == ("left" if side_index == 0 else "right")
                    for source in item.sources
                )
            )
            for stage_index, local_stage in enumerate(self.stages):
                if (
                    isinstance(local_stage, LocalCompute)
                    and isinstance(local_stage.program, FormulaProgram)
                    and source_owners.get(local_stage.stage_id) == endpoint_node.input_id
                ):
                    self.stages[stage_index] = replace(
                        local_stage,
                        inputs=(*local_stage.inputs, input_stage.inputs[3 + side_index]),
                        attribute=AttributeFormulaBinding(
                            use_records,
                            input_stage.program.parent_schema,
                            parent_columns,
                            len(local_stage.inputs),
                        ),
                    )
        formulas: list[FormulaProgram] = []
        for endpoint_node in node.inputs[1:]:
            assert isinstance(endpoint_node, SourceFrameInput)
            formula = next(
                s.program
                for s in reversed(self.source_plans[endpoint_node.input_id].lowered.plan.stages)
                if isinstance(s, LocalCompute) and isinstance(s.program, FormulaProgram)
            )
            formulas.append(formula)

        contribution_program = AttributeContributionProgram(
            operation,
            input_stage.program,
            (formulas[0], formulas[-1]),
            contribution_schema(input_stage.program, operation),
            exact_integer_closure(
                operation,
                tuple(
                    require_attribute(s)
                    for s in self.stages
                    if isinstance(s, SourceReadStage)
                    and read_attribute(s) is not None
                    and s.stage_id in input_stage.inputs[5:]
                ),
            ),
        )
        compute = FrameCompute(
            f"frame:{len(self.stages)}",
            (prepared, input_stage.inputs[0], *input_stage.inputs[3:5]),
            contribution_program,
        )
        self.stages.append(compute)
        self.operations[id(node)] = (compute.stage_id, contribution_program.output_schema)
        return compute.stage_id, contribution_program.output_schema

    def _attribute_inputs(
        self, node: FrameOperation, operation: AttributeInputsValues
    ) -> tuple[str, DataSchema]:
        parent, parent_schema = self.visit(node.inputs[0])
        self._prepare_attribute_projections(node.inputs[1:], parent, parent_schema)
        left, left_schema = self.visit(node.inputs[1])
        right, right_schema = self.visit(node.inputs[-1])
        layouts = tuple(
            tuple(
                require_attribute(stage)
                for stage in self.source_plans[endpoint.input_id].lowered.plan.stages
                if isinstance(stage, SourceReadStage) and read_attribute(stage) is not None
            )
            for endpoint in node.inputs[1:]
            if isinstance(endpoint, SourceFrameInput)
        )
        if operation.source_indices is not None:
            left_schema = select_attribute_source_schema(left_schema, operation.source_indices[0])
            right_schema = select_attribute_source_schema(right_schema, operation.source_indices[1])
        input_program = attribute_input_program(
            operation,
            parent_schema,
            left_schema,
            right_schema,
            layouts[0],
            layouts[-1] if len(node.inputs) > 2 else (),
        )
        bridges = tuple(
            "attribute-bridge:" + identifier
            for identifier in (
                operation.mapping.left_projection,
                operation.mapping.right_projection,
            )
        )

        read_bindings: list[AttributeReadBinding] = []
        direct_reads: list[str] = []
        source_owners = dict(self.owners)
        for side_index, (endpoint_node, projection_id) in enumerate(
            zip(node.inputs[1:], bridges[: len(node.inputs) - 1], strict=True)
        ):
            assert isinstance(endpoint_node, SourceFrameInput)
            for stage_index, source_stage in enumerate(self.stages):
                if (
                    isinstance(source_stage, SourceReadStage)
                    and read_attribute(source_stage) is not None
                    and source_owners.get(source_stage.stage_id) == endpoint_node.input_id
                ):
                    dependencies = source_stage.inputs
                    if projection_id not in dependencies:
                        dependencies = (*dependencies, projection_id)
                    updated_source = replace(
                        source_stage,
                        inputs=dependencies,
                        attribute_parent=AttributeSourceParentBinding(
                            parent_schema, dependencies.index(projection_id)
                        ),
                    )
                    self.stages[stage_index] = updated_source
                    layout = require_attribute(updated_source)
                    read_bindings.append(
                        AttributeReadBinding(
                            5 + len(direct_reads),
                            "left" if side_index == 0 else "right",
                            layout.scope,
                            tuple(leaf.use for leaf in layout.leaves),
                        )
                    )
                    direct_reads.append(updated_source.stage_id)
        input_program = replace(input_program, reads=tuple(read_bindings))
        inputs = (parent, left, right, *bridges, *direct_reads)
        # Absolute and relative targets can consume the same checked preparation.
        # Keep every input identity and the complete program, including schemas,
        # source indices and role bindings, in the equivalence check.
        for prepared_stage in self.attribute_preparations:
            if prepared_stage.inputs == inputs and prepared_stage.program == input_program:
                result = (prepared_stage.stage_id, input_program.output_schema)
                self.operations[id(node)] = result
                return result
        compute = FrameCompute(f"frame:{len(self.stages)}", inputs, input_program)
        self.attribute_preparations.append(compute)
        self.stages.append(compute)
        self.operations[id(node)] = (compute.stage_id, input_program.output_schema)
        return compute.stage_id, input_program.output_schema

    def _compare(
        self, node: FrameOperation, operation: CompareFrameValues
    ) -> tuple[str, DataSchema]:
        resolved = tuple(self.visit(child) for child in node.inputs)
        schemas = tuple(schema for _, schema in resolved)
        output_schema = transport_logical_facts(
            infer_frame_compare_schema(schemas, operation), node.logical_schema
        )
        program_compare = FrameCompareProgram(operation, schemas, output_schema)
        compute = FrameCompute(
            f"frame:{len(self.stages)}",
            tuple(identifier for identifier, _ in resolved),
            program_compare,
        )
        self.stages.append(compute)
        self.operations[id(node)] = (compute.stage_id, output_schema)
        return compute.stage_id, output_schema

    def _align(self, node: FrameOperation, operation: AlignFrameValues) -> tuple[str, DataSchema]:
        if len(node.inputs) != 2:
            reject(
                "two explicit binary inputs",
                str(len(node.inputs)),
                "Bind both Frame inputs.",
                "frame.plan",
            )
        left, left_schema = self.visit(node.inputs[0])
        right, right_schema = self.visit(node.inputs[1])
        output_schema = transport_logical_facts(
            infer_frame_combine_schema(left_schema, right_schema, operation),
            node.logical_schema,
        )
        binary = FrameCombineProgram(operation, left_schema, right_schema, output_schema)
        compute = FrameCompute(f"frame:{len(self.stages)}", (left, right), binary)
        self.stages.append(compute)
        self.operations[id(node)] = (compute.stage_id, output_schema)
        return compute.stage_id, output_schema

    def _prepare_attribute_projections(
        self, endpoints: tuple[FrameExpression, ...], parent: str, parent_schema: DataSchema
    ) -> None:
        for endpoint_node in endpoints:
            assert isinstance(endpoint_node, SourceFrameInput)
            for key_stage in self.source_plans[endpoint_node.input_id].lowered.plan.stages:
                if isinstance(key_stage, FrameKeysInput) and isinstance(
                    key_stage.parameter.definition.root, AttributeKeys
                ):
                    projection = plan_frame_keys(key_stage.parameter, parent, parent_schema)
                    if projection.stage_id not in self.projections:
                        self.stages.append(projection)
                        self.projections[projection.stage_id] = projection.stage_id


def plan_frame(
    root: FrameExpression,
    source_plans: Mapping[str, CompiledSourceFrame[CheckedStageInputT]],
    *,
    frame_inputs: Mapping[str, FrameExpression] | None = None,
) -> CompiledFramePlan[CheckedStageInputT]:
    planner = _FramePlanner(source_plans, {} if frame_inputs is None else frame_inputs)
    output, schema = planner.visit(root)
    consumers = tuple(
        (
            s.stage_id,
            tuple(
                c.stage_id
                for c in planner.stages
                for dependency in c.inputs
                if dependency == s.stage_id
            ),
        )
        for s in planner.stages
    )
    plan = QueryPlan(
        tuple(planner.stages),
        consumers,
        output,
        tuple(tuple(c.slot_id for c in axis.columns) for axis in schema.axes),
        fusion_groups=tuple(planner.groups),
        optimization_decisions=tuple(planner.decisions),
    )
    return CompiledFramePlan(
        LoweredQuery(with_unique_keys(plan), tuple(planner.reads)),
        schema,
        tuple(planner.owners),
        tuple(planner.boundaries),
        tuple(
            (stage_id, planner.operation_nodes[identifier])
            for identifier, (stage_id, _) in planner.operations.items()
        ),
        tuple(planner.checked_inputs),
    )


def frame_expression_schema(
    expression: FrameExpression,
    source_schemas: Mapping[str, DataSchema],
    *,
    memo: dict[int, DataSchema] | None = None,
) -> DataSchema:
    """Infer complete intermediate structure without running a partial schedule."""
    memo = {} if memo is None else memo
    existing = memo.get(id(expression))
    if existing is not None:
        return existing
    if isinstance(expression, FixedFrameInput):
        result = expression.schema
    elif isinstance(expression, SourceFrameInput):
        result = source_schemas[expression.input_id]
    else:
        inputs = tuple(
            frame_expression_schema(item, source_schemas, memo=memo) for item in expression.inputs
        )

        if isinstance(expression.operation, AttributeMergeValues):
            merged_schema = infer_attribute_merge_schema(inputs)
            assert isinstance(merged_schema, DataSchema)
            memo[id(expression)] = merged_schema
            return merged_schema
        if isinstance(expression.operation, AttributeGameValues):
            game_result = game_schema(inputs[1], inputs[2], inputs[0], expression.operation)
            assert isinstance(game_result, DataSchema)
            memo[id(expression)] = game_result
            return game_result
        if isinstance(expression.operation, AttributeContributionValues):
            left_schema, right_schema = inputs[1], inputs[-1]
            if expression.operation.source_indices is not None:
                left_schema = select_attribute_source_schema(
                    left_schema, expression.operation.source_indices[0]
                )
                right_schema = select_attribute_source_schema(
                    right_schema, expression.operation.source_indices[1]
                )
            contribution_result = pending_contribution_schema(
                left_schema, right_schema, inputs[0], expression.operation
            )
            assert isinstance(contribution_result, DataSchema)
            memo[id(expression)] = contribution_result
            return contribution_result
        if isinstance(expression.operation, AttributeInputsValues):
            reject(
                "a prepared Attribute input program",
                "uncompiled private input request",
                "Compile its explicit source layouts before requesting the matrix schema.",
                "frame.plan",
            )
        assert not isinstance(expression.operation, AttributeProjectValues)
        result = (
            infer_frame_combine_schema(inputs[0], inputs[1], expression.operation)
            if isinstance(expression.operation, AlignFrameValues)
            else infer_frame_compare_schema(inputs, expression.operation)
            if isinstance(expression.operation, CompareFrameValues)
            else infer_frame_schema(inputs[0], expression.operation)
        )
    if isinstance(expression, FrameOperation):
        result = transport_logical_facts(result, expression.logical_schema)
    memo[id(expression)] = result
    return result
