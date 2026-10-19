"""Compose explicit source and fixed inputs into the existing stage schedule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta

from marivo._definition.query import (
    ReduceValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueUnary,
)
from marivo._definition.relations import FrameKeys
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    FrameKeysInput,
    LocalCompute,
    PlannedCheck,
    QueryPlan,
    QueryStage,
    SourceRead,
)
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.compiler.query_program import DurationBinary, FrameCombineProgram, FrameProgram
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DurationDataType, ScalarDataType
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    CompareFrameValues,
    FixedFrameInput,
    FrameExpression,
    FrameOperation,
    SourceFrameInput,
    expression_type,
    expressions,
    infer_frame_combine_schema,
    infer_frame_schema,
)
from marivo.analysis.core.frame_structure import require_complete_structure
from marivo.analysis.core.model import reject


@dataclass(frozen=True, slots=True)
class CompiledSourceFrame:
    lowered: LoweredQuery
    schema: DataSchema


@dataclass(frozen=True, slots=True)
class CompiledFramePlan:
    lowered: LoweredQuery
    schema: DataSchema
    source_stages: tuple[tuple[str, str], ...]
    source_outputs: tuple[tuple[str, str], ...]


def _name(names: Mapping[str, str], identifier: str) -> str:
    prefix = next(iter(names.values())).split("/", 1)[0]
    return names.get(identifier, prefix + "/" + identifier)


def _required_formula(program: FormulaProgram, names: Mapping[str, str]) -> FormulaProgram:
    return replace(
        program,
        values=tuple(
            replace(value, stage_id=_name(names, value.stage_id)) for value in program.values
        ),
        populations=tuple(
            replace(value, stage_id=_name(names, value.stage_id)) for value in program.populations
        ),
    )


def _formula(program: FormulaProgram | None, names: Mapping[str, str]) -> FormulaProgram | None:
    if program is None:
        return None
    return _required_formula(program, names)


def _prefix(stage: QueryStage, names: Mapping[str, str]) -> QueryStage:
    inputs = tuple(_name(names, name) for name in stage.inputs)
    if isinstance(stage, SourceRead):
        return replace(
            stage,
            stage_id=_name(names, stage.stage_id),
            inputs=inputs,
            parameters=tuple(
                replace(p, stage_id=_name(names, p.stage_id)) for p in stage.parameters
            ),
            common_reads=tuple(_prefix_read(s, names) for s in stage.common_reads),
            native_reads=tuple(_prefix_read(s, names) for s in stage.native_reads),
        )
    if isinstance(stage, LocalCompute):
        return replace(
            stage,
            stage_id=_name(names, stage.stage_id),
            inputs=inputs,
            program=_formula(stage.program, names),
            fused_steps=tuple(_prefix_local(s, names) for s in stage.fused_steps),
        )
    return replace(stage, stage_id=_name(names, stage.stage_id), inputs=inputs)


def _prefix_read(stage: SourceRead, names: Mapping[str, str]) -> SourceRead:
    result = _prefix(stage, names)
    assert isinstance(result, SourceRead)
    return result


def _prefix_local(stage: LocalCompute, names: Mapping[str, str]) -> LocalCompute:
    result = _prefix(stage, names)
    assert isinstance(result, LocalCompute)
    return result


def _durations(schema: DataSchema, expression: ValueExpression) -> tuple[DurationBinary, ...]:
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


def plan_frame(
    root: FrameExpression,
    source_plans: Mapping[str, CompiledSourceFrame],
    *,
    frame_inputs: Mapping[str, FrameExpression] | None = None,
) -> CompiledFramePlan:
    stages: list[QueryStage] = []
    reads: list[LoweredRead] = []
    checks: list[PlannedCheck] = []
    owners: list[tuple[str, str]] = []
    boundaries: list[tuple[str, str]] = []
    leaves: dict[str, tuple[str, DataSchema]] = {}
    identities: dict[str, SourceFrameInput | FixedFrameInput] = {}
    projections: dict[str, str] = {}
    operations: dict[int, tuple[str, DataSchema]] = {}

    def visit(node: FrameExpression) -> tuple[str, DataSchema]:
        if isinstance(node, (SourceFrameInput, FixedFrameInput)) and node.input_id in leaves:
            saved = leaves[node.input_id]
            previous = identities[node.input_id]
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
            require_complete_structure(node.schema)
            stage = FrameInput(f"fixed:{len(leaves)}", node.input_id, node.schema)
            stages.append(stage)
            result = (stage.stage_id, node.schema)
            leaves[node.input_id] = result
            identities[node.input_id] = node
            return result
        if isinstance(node, SourceFrameInput):
            if node.input_id not in source_plans:
                reject(
                    "a prepared plan for each explicit graph input",
                    node.input_id,
                    "Prepare only the explicit source leaves.",
                    "frame.plan",
                )
            child = source_plans[node.input_id]
            require_complete_structure(child.schema)
            external: dict[str, str] = {}
            for key_stage in child.lowered.plan.stages:
                if not isinstance(key_stage, FrameKeysInput):
                    continue
                definition = key_stage.parameter.definition.root
                assert isinstance(definition, FrameKeys)
                source_expression = (frame_inputs or {}).get(definition.input_id)
                if source_expression is None:
                    reject(
                        "a retained invocation Frame input",
                        definition.input_id,
                        "Retain Frame-key sidecars before source capture.",
                        "frame.population",
                    )
                source, schema = visit(source_expression)
                from marivo.analysis.compiler.populations import plan_frame_keys

                projection = plan_frame_keys(key_stage.parameter, source, schema)
                if projection.stage_id not in projections:
                    stages.append(projection)
                    projections[projection.stage_id] = projection.stage_id
                external[key_stage.stage_id] = projection.stage_id
            names = {
                s.stage_id: f"source:{len(leaves)}/{s.stage_id}"
                for s in child.lowered.plan.stages
                if not isinstance(s, FrameKeysInput)
            }
            names.update(external)
            prefixed = tuple(
                _prefix(s, names)
                for s in child.lowered.plan.stages
                if not isinstance(s, FrameKeysInput)
            )
            by_id = {s.stage_id: s for s in prefixed}
            stages.extend(prefixed)
            for item in child.lowered.reads:
                read_stage = by_id[names[item.stage.stage_id]]
                assert isinstance(read_stage, SourceRead)
                reads.append(replace(item, stage=read_stage))
            checks.extend(replace(c, stage_id=names[c.stage_id]) for c in child.lowered.plan.checks)
            owners.extend((s.stage_id, node.input_id) for s in prefixed)
            output = names[child.lowered.plan.publish]
            boundaries.append((output, node.input_id))
            result = (output, child.schema)
            leaves[node.input_id] = result
            identities[node.input_id] = node
            return result
        assert isinstance(node, FrameOperation)
        existing = operations.get(id(node))
        if existing is not None:
            return existing
        if isinstance(node.operation, (AlignFrameValues, CompareFrameValues)):
            if len(node.inputs) != 2:
                reject(
                    "two explicit binary inputs",
                    str(len(node.inputs)),
                    "Bind both Frame inputs.",
                    "frame.plan",
                )
            left, left_schema = visit(node.inputs[0])
            right, right_schema = visit(node.inputs[1])
            output_schema = infer_frame_combine_schema(left_schema, right_schema, node.operation)
            binary = FrameCombineProgram(node.operation, left_schema, right_schema, output_schema)
            compute = FrameCompute(f"frame:{len(stages)}", (left, right), binary)
            stages.append(compute)
            operations[id(node)] = (compute.stage_id, output_schema)
            return compute.stage_id, output_schema
        if len(node.inputs) != 1:
            reject(
                "one explicit input for an ordinary Frame operation",
                str(len(node.inputs)),
                "Use ordinary operations on their receiver.",
                "frame.plan",
            )
        source, schema = visit(node.inputs[0])
        output_schema = infer_frame_schema(schema, node.operation)
        statistic_types = (
            tuple(
                ScalarDataType("int64")
                if stat.value is None
                else expression_type(schema, stat.value)
                for _, stat in node.operation.values
            )
            if isinstance(node.operation, ReduceValues)
            else ()
        )
        program = FrameProgram(
            node.operation,
            schema,
            output_schema,
            statistic_types,
            tuple(
                item for expr in expressions(node.operation) for item in _durations(schema, expr)
            ),
        )
        compute = FrameCompute(f"frame:{len(stages)}", (source,), program)
        stages.append(compute)
        operations[id(node)] = (compute.stage_id, output_schema)
        return compute.stage_id, output_schema

    output, schema = visit(root)
    consumers = tuple(
        (
            s.stage_id,
            tuple(
                c.stage_id for c in stages for dependency in c.inputs if dependency == s.stage_id
            ),
        )
        for s in stages
    )
    plan = QueryPlan(
        tuple(stages),
        tuple(checks),
        consumers,
        output,
        tuple(tuple(c.slot_id for c in axis.columns) for axis in schema.axes),
    )
    return CompiledFramePlan(
        LoweredQuery(plan, tuple(reads)), schema, tuple(owners), tuple(boundaries)
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
        result = (
            infer_frame_combine_schema(inputs[0], inputs[1], expression.operation)
            if isinstance(expression.operation, (AlignFrameValues, CompareFrameValues))
            else infer_frame_schema(inputs[0], expression.operation)
        )
    memo[id(expression)] = result
    return result
