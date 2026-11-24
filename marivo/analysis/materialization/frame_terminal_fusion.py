"""Fold checked terminal Frame operations into a source-owned query."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.query import ReduceValues, SelectValues, TopValues, ValueSlot
from marivo._definition.relations import ObserveQuery, ReadQuery
from marivo._kernel.validation import has_exact_type
from marivo.analysis.compiler.frame_top import native_frame_key_types, native_top_order
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.native_query import (
    NativeCheckOrigin,
    NativeFrameTopFinish,
    NativeQueryFinish,
)
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_program import FrameCombineProgram, FrameProgram
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    NativeRead,
    QueryStage,
    RuleDecision,
    SourceReadStage,
    read_finish,
)
from marivo.analysis.compiler.source_predicates import same_returned_key
from marivo.analysis.core.frame_operations import (
    FixFrameValues,
    FrameExpression,
    FrameOperation,
    SourceFrameInput,
)
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.materialization.frame_terminal_aggregates import (
    has_partition,
    lower_over,
    lower_reduce,
)
from marivo.analysis.materialization.frame_terminal_align import (
    ALIGN_ORDER,
    AlignmentInput,
    lower_align,
)
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
    _attach_relation_checks,
)
from marivo.analysis.materialization.native_query_relation import native_query_relation
from marivo.analysis.materialization.query_plan_transport import _consumers, _logical_reads
from marivo.analysis.materialization.source_reads import qualify_query_sources
from marivo.analysis.model.data_schema import DataAxis, DataSchema
from marivo.analysis.model.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.spi import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession


FrameTerminalRuleId: TypeAlias = Literal[
    "frame_top_float_decimal",
    "frame_terminal_fix",
    "frame_terminal_reduce",
    "frame_terminal_over",
    "frame_terminal_align",
]


@dataclass(frozen=True, slots=True)
class _CheckScope:
    relation: NativeQueryRelation
    source_id: str
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Operand:
    native: NativeQueryRelation
    schema: DataSchema
    owner: SourceSession
    prepared: PreparedSources
    order: tuple[str, ...]
    check_scopes: tuple[_CheckScope, ...]
    nulls_first: bool = False

    def aligned(self) -> AlignmentInput:
        return AlignmentInput(self.native.expression, self.schema, self.order, self.nulls_first)


@dataclass(frozen=True, slots=True)
class _Projection:
    expression: ir.Table
    schema: DataSchema
    order: tuple[str, ...]
    nulls_first: bool = False
    top_marker: str | None = None


def _extremum_argument(value: ops.IfElse) -> ops.Value | None:
    condition, missing, reduced = value.bool_expr, value.true_expr, value.false_null_expr
    if (
        isinstance(condition, ops.Equals)
        and isinstance(known := condition.left, ops.Count)
        and isinstance(zero := condition.right, ops.Literal)
        and zero.value == 0
        and isinstance(missing, ops.Cast)
        and isinstance(empty := missing.arg, ops.Literal)
        and empty.value is None
        and isinstance(reduced, (ops.Min, ops.Max))
        and known.arg == reduced.arg
        and known.where is None
        and reduced.where is None
    ):
        return reduced.arg
    return None


def _physical_value_carrier(
    value: ops.Value, owner: SourceSession, prepared: PreparedSources
) -> bool:
    # Follow aliases back to stored fields or a qualified physical finisher.
    # A statement-dependent computed projection still needs its original read.
    if isinstance(value, ops.Field):
        definition = value.rel.values.get(value.name)
        if definition is not None:
            return _physical_value_carrier(definition, owner, prepared)
        if isinstance(value.rel, ops.Reference):
            return _physical_value_carrier(ops.Field(value.rel.parent, value.name), owner, prepared)
        return any(
            owner.stored_projection.preserves_reduction_carrier(binding, value)
            for binding in prepared.bound.values()
            if binding.relation.op() == value.rel
        )
    if isinstance(value, ops.Cast):
        source, target = value.arg.dtype, value.dtype
        if (
            source.is_signed_integer()
            and target.is_decimal()
            and target.precision is not None
            and target.scale is not None
            and target.precision - target.scale >= 19
        ):
            return _physical_value_carrier(value.arg, owner, prepared)
    if isinstance(value, (ops.Min, ops.Max)) and value.where is None:
        return _physical_value_carrier(value.arg, owner, prepared)
    if isinstance(value, ops.IfElse) and (operand := _extremum_argument(value)) is not None:
        return _physical_value_carrier(operand, owner, prepared)
    if value.dtype.is_string():
        dialect = owner.provider.dialect
        returned = dialect.returned_string
        normalizers = (
            (dialect.string_without_collation,)
            if returned is None
            else (dialect.string_without_collation, returned)
        )
        return any(
            field.dtype.is_string()
            and any(normalized(field.to_expr()).op() == value for normalized in normalizers)
            and _physical_value_carrier(field, owner, prepared)
            for field in value.find(ops.Field)
        )
    return False


def _exact_values(
    expression: ir.Table, schema: DataSchema, owner: SourceSession, prepared: PreparedSources
) -> bool:
    try:
        for column in schema.columns:
            if column.slot_id not in expression.columns:
                return False
            value = expression[column.slot_id]
            dtype = value.type()
            if data_type(dtype) != column.dtype or dtype.is_uint64():
                return False
            if not (dtype.is_numeric() or dtype.is_boolean() or dtype.is_string()):
                # A later SQL filter or count cannot omit a temporal transport
                # error from the complete original input.
                return False
            if not _physical_value_carrier(value.op(), owner, prepared):
                return False
    except SemanticExpressionError:
        return False
    return True


def _checked_operand(
    lowered: LoweredQuery,
    source_stage: QueryStage,
    source: SourceFrameInput,
    prepared: PreparedSources,
    schema: DataSchema,
) -> _Operand | str:
    if not isinstance(source.graph.root, (ReadQuery, ObserveQuery)):
        return "producer is outside the direct source input grammar"

    if not all(has_exact_type(axis, DataAxis) for axis in schema.axes) or schema.projection_columns:
        return "producer requires a frozen coordinate attachment or another key carrier"
    if isinstance(source_stage, SourceReadStage) and read_finish(source_stage) is not None:
        return "producer retains a governed host finish"
    for owner in dict.fromkeys(prepared.sources.values()):
        native = native_query_relation(
            prepared, lowered, source_stage.stage_id, owner, terminal=True, frame_endpoint=True
        )
        if isinstance(native, str):
            continue
        if not native.sources or native.parameters:
            return "producer would require completing or uploading an input"
        if not prepared.bound or not all(
            owner.stored_projection.proves(binding) for binding in prepared.bound.values()
        ):
            return "producer does not have a verified stored relation for every input"
        if not native_frame_key_types(
            schema, exact_strings=owner.provider.dialect.native_frame_string_keys_match_transport
        ):
            return "producer keys require their complete source transport"
        if not _exact_values(native.expression, schema, owner, prepared):
            return "producer retains a physical carrier conversion boundary"
        if not source_stage.unique_keys.proves(schema.key_fields):
            return "producer has no complete key proof"
        checks = tuple(
            NumericCheck(
                native.expression[column.slot_id].isnull(),
                f"{column.slot_id}: a nonnullable column without NA",
                "NA values in the consumed Frame input",
            )
            for column in schema.columns
            if not column.nullable
            and not (
                column.slot_id in schema.key_fields
                and native.expression[column.slot_id].type().nullable is False
            )
        )
        physical = tuple(lowered.plan.source_read_stage(item.stage_id) for item in native.sources)
        return _Operand(
            native,
            schema,
            owner,
            prepared,
            schema.key_fields,
            (
                *(
                    _CheckScope(
                        replace(native, sources=(read,), checks=()), stage.source_id, stage.keys
                    )
                    for read, stage in zip(native.sources, physical, strict=True)
                ),
                _CheckScope(
                    replace(native, sources=(), checks=(*native.checks, *checks)),
                    physical[0].source_id,
                    schema.key_fields,
                ),
            ),
        )
    return "producer retains a checked non-native relation boundary"


def _fix(operand: _Operand, operation: FixFrameValues, output_schema: DataSchema) -> _Projection:
    axis = operand.schema.axes[operation.position]
    expression = operand.native.expression
    predicates = [
        same_returned_key(
            expression[column.slot_id],
            ibis.literal(item, type=ibis_type(column.dtype)),
            operand.owner.provider.name,
        )
        for column, item in zip(axis.columns, operation.key, strict=True)
    ]
    removed_keys = {column.slot_id for column in axis.columns}
    order = tuple(key for key in operand.order if key not in removed_keys)
    return _Projection(
        expression.filter(*predicates).select(
            *dict.fromkeys((*(column.slot_id for column in output_schema.columns), *order))
        ),
        output_schema,
        order,
        operand.nulls_first,
    )


def _top(operand: _Operand, operation: TopValues, score: str) -> _Projection | str:
    if operation.n >= 2**63:
        return "limit exceeds the native signed int64 row-count parameter"
    expression = operand.native.expression
    order = native_top_order(
        expression,
        expression[score],
        operand.schema.key_fields,
        operand.owner.provider.name,
        descending=operation.descending,
    )
    if order is None:
        return "score carrier has no qualified native order"
    return _Projection(
        expression.order_by(*order)
        .limit(operation.n)
        .mutate(__frame_top_present=ibis.literal(True)),
        operand.schema,
        (),
        top_marker="__frame_top_present",
    )


def _replace_plan(
    compiled: CompiledFramePlan[CheckedStageInputT],
    anchor: str,
    stage: SourceReadStage,
    read: LoweredRead,
    removed: frozenset[str],
    rule: FrameTerminalRuleId,
) -> CompiledFramePlan[CheckedStageInputT]:
    lowered = compiled.lowered
    stages = tuple(
        stage if item.stage_id == anchor else item
        for item in lowered.plan.stages
        if item.stage_id not in removed or item.stage_id == anchor
    )
    plan = replace(
        lowered.plan,
        stages=stages,
        consumers=_consumers(stages),
        fusion_groups=tuple(g for g in lowered.plan.fusion_groups if g.output not in removed),
        optimization_decisions=(
            *lowered.plan.optimization_decisions,
            RuleDecision(
                rule,
                "adopted",
                (*sorted(removed), anchor),
                "One source statement completes the Frame operation and its full input checks.",
            ),
        ),
    )
    plan = replace(plan, required_slots=required_slots(plan))
    return replace(
        compiled,
        lowered=LoweredQuery(
            plan,
            (*tuple(item for item in lowered.reads if item.stage_id not in removed), read),
        ),
        source_stages=(
            *(item for item in compiled.source_stages if item[0] not in removed),
            (stage.stage_id, stage.stage_id),
        ),
        source_outputs=tuple(item for item in compiled.source_outputs if item[0] not in removed),
    )


def _install(
    compiled: CompiledFramePlan[CheckedStageInputT],
    anchor: QueryStage,
    operand: _Operand,
    projection: _Projection,
    rule: FrameTerminalRuleId,
) -> CompiledFramePlan[CheckedStageInputT] | str:
    plan = compiled.lowered.plan
    native, owner = operand.native, operand.owner
    expression = projection.expression
    preparation: tuple[tuple[str, str, str], ...] = ()
    checks: tuple[tuple[str, NumericCheck], ...] = ()
    origins: list[NativeCheckOrigin] = []
    for scope in operand.check_scopes:
        previous = len(checks)
        expression, preparation, checks = _attach_relation_checks(
            expression, scope.relation, plan, preparation, checks, provider=owner.provider.name
        )
        origins.extend(
            NativeCheckOrigin(name, scope.source_id, scope.keys) for name, _ in checks[previous:]
        )
    finish = NativeQueryFinish(
        projection.schema.columns,
        projection.order,
        None,
        frame_top=NativeFrameTopFinish(projection.top_marker, ())
        if projection.top_marker is not None
        else None,
        frame_schema=projection.schema,
        nulls_first=projection.nulls_first,
        check_origins=tuple(origins),
    )
    physical = tuple(plan.source_read_stage(read.stage_id) for read in native.sources)
    stage = SourceReadStage(
        anchor.stage_id,
        physical[0].source_id,
        (),
        projection.schema.columns,
        projection.schema.key_fields,
        NativeRead(native.reductions(plan), finish),
        (),
        (),
        None,
        preparation_checks=preparation,
        native_reads=_logical_reads(physical),
        unique_keys=anchor.unique_keys,
    )
    read = LoweredRead(stage.stage_id, expression, checks)
    candidate = _replace_plan(compiled, anchor.stage_id, stage, read, native.stages, rule)
    try:
        qualify_query_sources(operand.prepared, LoweredQuery(candidate.lowered.plan, (read,)))
    except MaterializationError as error:
        return error.received or "native source did not qualify the terminal statement"
    return candidate


def _merge_prepared(left: _Operand, right: _Operand) -> PreparedSources | str:
    a, b = left.prepared, right.prepared
    if any(
        binding.relation.op() != b.bound[name].relation.op()
        or a.sources.get(name, a.source) is not b.sources.get(name, b.source)
        for name, binding in a.bound.items()
        if name in b.bound
    ):
        return "same-source operands retain incompatible physical bindings"
    return replace(
        a,
        bound={**b.bound, **a.bound},
        sources={**b.sources, **a.sources},
    )


def _align(
    left: _Operand, right: _Operand, stage: FrameCompute, program: FrameCombineProgram
) -> _Operand | str:
    if left.owner is not right.owner:
        return "alignment operands belong to separate SourceSessions"
    owner = left.owner
    prepared = _merge_prepared(left, right)
    if isinstance(prepared, str):
        return prepared
    expression = lower_align(left.aligned(), right.aligned(), program, owner)
    if isinstance(expression, str):
        return expression
    a, b = left.native, right.native
    native = NativeQueryRelation(
        expression,
        a.stages | b.stages | {stage.stage_id},
        (*a.sources, *b.sources),
        (*a.checks, *b.checks),
    )
    return _Operand(
        native,
        program.output_schema,
        owner,
        prepared,
        ALIGN_ORDER,
        (*left.check_scopes, *right.check_scopes),
    )


def _project_unary(operand: _Operand, program: FrameProgram) -> _Projection | str:
    operation = program.operation
    output = program.output_schema
    native, owner = operand.native.expression, operand.owner
    if isinstance(operation, FixFrameValues):
        return _fix(operand, operation, output)
    if isinstance(operation, ReduceValues):
        relation = lower_reduce(native, program, owner)
        if isinstance(relation, str):
            return relation
        return _Projection(relation, output, output.key_fields, nulls_first=True)
    assert isinstance(operation, SelectValues)
    relation = lower_over(native, program, owner, order=operand.order)
    if isinstance(relation, str):
        return relation
    return _Projection(relation, output, operand.order, operand.nulls_first)


@dataclass(frozen=True, slots=True)
class _TerminalLowerer(Generic[CheckedStageInputT]):
    compiled: CompiledFramePlan[CheckedStageInputT]
    sources: Mapping[str, PreparedSources]
    protected: frozenset[int]
    stages: Mapping[str, QueryStage]
    operations: Mapping[str, FrameOperation]
    checked: frozenset[str]

    def _input(
        self, parent: FrameCompute, position: int, node: FrameExpression, schema: DataSchema
    ) -> _Operand | str:
        identifier = parent.inputs[position]
        if (
            id(node) in self.protected
            or identifier in self.checked
            or self.compiled.lowered.plan.consumers_of(identifier) != (parent.stage_id,)
            or parent.inputs.count(identifier) != 1
        ):
            return "producer is protected, completed, or has another consumer"
        endpoint = self.stages[identifier]
        if isinstance(node, SourceFrameInput) and node.input_id in self.sources:
            return _checked_operand(
                self.compiled.lowered, endpoint, node, self.sources[node.input_id], schema
            )
        if isinstance(node, FrameOperation) and isinstance(endpoint, FrameCompute):
            return self.operation(endpoint, node)
        return "producer is fixed, source-free, or has another Frame boundary"

    def operation(self, stage: FrameCompute, node: FrameOperation) -> _Operand | str:
        rule = _terminal_rule(stage)
        if (
            rule is None
            or rule == "frame_top_float_decimal"
            or id(node) in self.protected
            or not self.compiled.lowered.plan.optimization_policy.allows(rule)
        ):
            return "producer retains another checked Frame kernel"
        program = stage.program
        if isinstance(program, FrameCombineProgram):
            left = self._input(stage, 0, node.inputs[0], program.left_schema)
            if isinstance(left, str):
                return left
            right = self._input(stage, 1, node.inputs[1], program.right_schema)
            if isinstance(right, str):
                return right
            return _align(left, right, stage, program)
        assert isinstance(program, FrameProgram)
        operand = self._input(stage, 0, node.inputs[0], program.input_schema)
        if isinstance(operand, str):
            return operand
        projection = _project_unary(operand, program)
        if isinstance(projection, str):
            return projection
        return replace(
            operand,
            native=replace(
                operand.native,
                expression=projection.expression,
                stages=operand.native.stages | {stage.stage_id},
            ),
            schema=projection.schema,
            order=projection.order,
            nulls_first=projection.nulls_first,
        )

    def terminal(
        self, stage: FrameCompute, node: FrameOperation, rule: FrameTerminalRuleId
    ) -> tuple[CompiledFramePlan[CheckedStageInputT], PreparedSources, str] | str:
        if rule == "frame_top_float_decimal":
            return self._terminal_top(stage, node, rule)
        operand = self.operation(stage, node)
        if isinstance(operand, str):
            return operand
        projection = _Projection(
            operand.native.expression, operand.schema, operand.order, operand.nulls_first
        )
        result = _install(self.compiled, stage, operand, projection, rule)
        return result if isinstance(result, str) else (result, operand.prepared, stage.stage_id)

    def _terminal_top(
        self, stage: FrameCompute, node: FrameOperation, rule: FrameTerminalRuleId
    ) -> tuple[CompiledFramePlan[CheckedStageInputT], PreparedSources, str] | str:
        assert isinstance(stage.program, FrameProgram)
        operation = stage.program.operation
        assert isinstance(operation, TopValues) and isinstance(operation.score, ValueSlot)
        operand = self._input(stage, 0, node.inputs[0], stage.program.input_schema)
        if isinstance(operand, str):
            return operand
        anchor = self.stages[stage.inputs[0]]
        projection = _top(operand, operation, operand.schema.values[operation.score.index].slot_id)
        if isinstance(projection, str):
            return projection
        result = _install(self.compiled, anchor, operand, projection, rule)
        return result if isinstance(result, str) else (result, operand.prepared, anchor.stage_id)


def _terminal_rule(stage: FrameCompute) -> FrameTerminalRuleId | None:
    if isinstance(stage.program, FrameCombineProgram):
        return "frame_terminal_align"
    if not isinstance(stage.program, FrameProgram):
        return None
    operation = stage.program.operation
    if isinstance(operation, FixFrameValues):
        return "frame_terminal_fix"
    if isinstance(operation, ReduceValues):
        return "frame_terminal_reduce"
    if isinstance(operation, SelectValues) and any(
        has_partition(item) for _, item in operation.values
    ):
        return "frame_terminal_over"
    if isinstance(operation, TopValues) and isinstance(operation.score, ValueSlot):
        dtype = stage.program.input_schema.values[operation.score.index].dtype
        if dtype == ScalarDataType("float64") or isinstance(dtype, DecimalDataType):
            return "frame_top_float_decimal"
    return None


def fuse_frame_terminal(
    compiled: CompiledFramePlan[CheckedStageInputT],
    source_prepared: Mapping[str, PreparedSources],
    protected: frozenset[int],
) -> tuple[CompiledFramePlan[CheckedStageInputT], dict[str, PreparedSources]]:
    """Admit ordinary same-invocation operations with private unshared inputs."""
    owners: dict[str, PreparedSources] = {}
    operations = dict(compiled.frame_operations)
    checked = frozenset(identifier for identifier, _ in compiled.checked_inputs)
    removed: set[str] = set()
    for stage in reversed(compiled.lowered.plan.stages):
        if not isinstance(stage, FrameCompute) or stage.stage_id in removed:
            continue
        rule = _terminal_rule(stage)
        if rule is None or not compiled.lowered.plan.optimization_policy.allows(rule):
            continue
        node = operations[stage.stage_id]
        if id(node) in protected:
            continue
        stages = {item.stage_id: item for item in compiled.lowered.plan.stages}
        lowerer = _TerminalLowerer(
            compiled, source_prepared, protected, stages, operations, checked
        )
        result = lowerer.terminal(stage, node, rule)
        if isinstance(result, str):
            lowered = compiled.lowered
            compiled = replace(
                compiled,
                lowered=replace(
                    lowered,
                    plan=replace(
                        lowered.plan,
                        optimization_decisions=(
                            *lowered.plan.optimization_decisions,
                            RuleDecision(rule, "refused", (stage.stage_id,), result),
                        ),
                    ),
                ),
            )
            continue
        compiled, prepared, stage_id = result
        owners[stage_id] = prepared
        removed.update(stages.keys() - {item.stage_id for item in compiled.lowered.plan.stages})
        removed.add(stage_id)
    return compiled, owners
