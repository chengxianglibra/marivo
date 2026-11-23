"""Lower a checked finite Query producer over one native source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    EntityCount,
    MetricExpression,
    SourceAggregate,
)
from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    KeyImage,
    LiteralValues,
    PopulationValues,
    ReadFields,
    SelectValues,
    TopValues,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
)
from marivo._definition.types import AxisBinding
from marivo._kernel.validation import is_instance
from marivo.analysis.binding.programs import FormulaProgram, PopulationInput
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.native_formula import lower_native_formula
from marivo.analysis.compiler.query_program import ResultProgram, SourceParameter
from marivo.analysis.compiler.query_stages import (
    FrameKeysInput,
    LocalCompute,
    QueryStage,
    SourceReadStage,
    local_result,
    local_steps,
    read_components,
    read_finish,
    read_members,
    read_presence,
    read_reduction,
    require_reduction,
)
from marivo.analysis.compiler.source_predicates import string_bytes as _string_bytes
from marivo.analysis.compiler.source_predicates import (
    string_without_collation,
)
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
)
from marivo.analysis.materialization.native_query_inputs import (
    field_inputs as _field_inputs,
)
from marivo.analysis.materialization.native_query_inputs import (
    terminal_attribute_inputs as _terminal_attribute_inputs,
)
from marivo.analysis.materialization.source_query_policy import (
    qualified_query_provider,
    qualify_query_source,
    qualify_query_top,
)
from marivo.analysis.model.data_types import (
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.semantic.spi import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession


from marivo.analysis.materialization.native_population_relations import (
    _native_member_counts,
    _population_relation,
)
from marivo.analysis.materialization.native_query_values import _checked_value, _safe_expression


def _physical_owner(
    expression: ir.Table,
    owner: SourceSession,
    prepared: PreparedSources,
    *,
    allowed_parameters: frozenset[ops.Node] = frozenset(),
) -> bool:
    physical = (ops.DatabaseTable, ops.UnboundTable, ops.InMemoryTable, ops.SQLQueryResult)
    allowed = {
        leaf
        for path, binding in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is owner
        for leaf in binding.relation.op().find(physical)
    }
    leaves = set(expression.op().find(physical))
    return bool(leaves) and leaves <= allowed | allowed_parameters


def _read_scopes(read: LoweredRead) -> tuple[ir.Table, ...]:
    return (read.expression, *(check.relation for check in read.population_checks))


def _project_native_read(
    stage: SourceReadStage, read: LoweredRead, provider: str
) -> NativeQueryRelation:
    expression = read.expression
    if "__numeric_present" in expression.columns:
        expression = expression.filter(expression["__numeric_present"].fill_null(False))
    expression = (
        expression.select(
            **{
                column.slot_id: string_without_collation(expression[column.slot_id], provider)
                if expression[column.slot_id].type().is_string()
                else expression[column.slot_id]
                for column in stage.columns
            }
        )
        if stage.columns
        else expression.select(__population_unit=ibis.literal(True)).distinct()
    )
    return NativeQueryRelation(
        expression, frozenset({stage.stage_id}), (read,), parameters=stage.parameters
    )


def _native_field_issue(
    stage: SourceReadStage,
    read: LoweredRead,
    owner: SourceSession,
    identities: set[str],
    field_input: bool,
    admitted: set[str],
) -> str | None:
    if owner.provider.query_policy.key_fusion == "key_counts" and any(
        (
            not (
                column.dtype == ScalarDataType("int64")
                and (column.slot_id in identities or field_input)
            )
            and column.dtype != ScalarDataType("string")
        )
        or isinstance(column.dtype, DecimalDataType)
        for column in stage.columns
    ):
        return "PostgreSQL field relations qualify decoded strings and complete identity keys; other values retain their pandas boundary"
    if not owner.provider.query_policy.supports_fused_arithmetic and any(
        column.dtype == ScalarDataType("float64") for column in stage.columns
    ):
        return "floating producer slots retain pandas missing-value semantics"
    if any(name not in admitted for name, _ in read.numeric_checks) or {
        name for name, _ in read.numeric_checks
    } != {name for name, _, _ in stage.preparation_checks}:
        return "field producer retains an unsupported check"
    if any(
        check.expected
        not in (
            "non-null complete identity keys",
            "a non-null complete identity for every input tuple",
        )
        for check in read.population_checks
    ):
        return "field producer retains an unsupported Population check"
    return None


def _lower_native_top(
    expression: ir.Table,
    program: ResultProgram,
    operation: TopValues,
    values: tuple[ir.Value, ...],
    coordinates: tuple[ir.Value, ...],
    owner: SourceSession,
) -> tuple[ir.Table, tuple[NumericCheck, ...]] | str:
    arithmetic = owner.provider.query_policy.supports_fused_arithmetic
    if (
        (not arithmetic and not isinstance(operation.score, (ValueSlot, CoordinateSlot)))
        or not qualified_query_provider(owner)
        or not _safe_expression(
            operation.score,
            native_float=owner.provider.query_policy.supports_fused_arithmetic,
            native_arithmetic=arithmetic,
        )
    ):
        return "Top-N score requires a qualified finite expression"
    if any(
        isinstance(column.dtype, DurationDataType)
        or (isinstance(column.dtype, TimestampDataType) and column.dtype.unit == "ns")
        for column in program.columns
    ):
        return "Top-N retains Duration or submicrosecond timestamp ordering"
    evaluated = _checked_value(operation.score, values, coordinates, owner.provider.name)
    if isinstance(evaluated, str):
        return evaluated
    score = evaluated.value
    if owner.provider.query_policy.needs_remote_carrier_qualification:
        issue = qualify_query_top(owner, program, score)
        if issue is not None:
            return issue
    if score.type().is_floating() and not owner.provider.query_policy.supports_fused_arithmetic:
        return "Top-N retains pandas floating missing-score semantics"
    score = _string_bytes(score, owner.provider.name) if score.type().is_string() else score
    order = (
        score.desc(nulls_first=False) if operation.descending else score.asc(nulls_first=False),
        *(
            (
                _string_bytes(expression[key], owner.provider.name)
                if expression[key].type().is_string()
                else expression[key]
            ).asc(nulls_first=True)
            for key in program.keys
        ),
    )
    expression = expression.order_by(*order).limit(operation.n)
    return expression, evaluated.checks


def _lower_native_operation(
    expression: ir.Table,
    stage: LocalCompute,
    program: ResultProgram,
    operation: FilterValues | SelectValues | TopValues,
    owner: SourceSession,
    checks: tuple[NumericCheck, ...],
) -> tuple[ir.Table, tuple[NumericCheck, ...]] | str:
    arithmetic = owner.provider.query_policy.supports_fused_arithmetic
    # Demand pruning retains the original positional expression layout.
    # Typed placeholders occupy only slots unused by this operation.
    values = tuple(
        expression[column.slot_id]
        if column.slot_id in expression.columns
        else ibis.null().cast(ibis_type(column.dtype))
        for column in (
            program.input_layout.values
            if program.input_layout is not None
            else tuple(column for column in program.columns if column.slot_id not in program.keys)
        )
    )
    if any(len(group) != 1 for group in program.key_groups):
        coordinates = ()
    else:
        coordinates = tuple(expression[group[0]] for group in program.key_groups)
    if isinstance(operation, FilterValues):
        if not is_instance(operation.predicate, ValueExpression) or not _safe_expression(
            operation.predicate,
            native_float=owner.provider.query_policy.supports_fused_arithmetic,
            native_arithmetic=arithmetic,
        ):
            return "producer filter has nonlocal or numeric expression obligations"
        evaluated = _checked_value(operation.predicate, values, coordinates, owner.provider.name)
        if isinstance(evaluated, str):
            return evaluated
        condition = evaluated.value
        checks = (*checks, *evaluated.checks)
        if not isinstance(condition, ir.BooleanValue):
            return "producer filter is not a boolean value"
        expression = expression.filter(condition.fill_null(False)).select(
            *(column.slot_id for column in stage.columns)
        )
    elif isinstance(operation, SelectValues):
        if any(
            (not arithmetic and not isinstance(value, (ValueSlot, ValueLiteral)))
            or not _safe_expression(
                value,
                native_float=owner.provider.query_policy.supports_fused_arithmetic,
                native_arithmetic=arithmetic,
            )
            for _, value in operation.values
        ) or (
            not owner.provider.query_policy.supports_fused_arithmetic
            and any(column.dtype == ScalarDataType("float64") for column in stage.columns)
        ):
            return "producer select retains a numeric finish or computed value"
        projection = {key: expression[key] for key in program.keys}
        for index, (_, value) in enumerate(operation.values):
            evaluated = _checked_value(value, values, coordinates, owner.provider.name)
            if isinstance(evaluated, str):
                return evaluated
            projection[f"value.{index}"] = evaluated.value
            checks = (*checks, *evaluated.checks)
        expression = expression.select(**projection)
    else:
        qualified = _lower_native_top(expression, program, operation, values, coordinates, owner)
        if isinstance(qualified, str):
            return qualified
        expression, top_checks = qualified
        checks = (*checks, *top_checks)
    return expression, checks


def _native_local_projection(
    expression: ir.Table,
    stage: LocalCompute,
    program: ResultProgram,
    owner: SourceSession,
    checks: tuple[NumericCheck, ...],
) -> tuple[ir.Table, tuple[NumericCheck, ...]] | str:
    operation = program.operation
    if isinstance(operation, ReadFields):
        columns = (
            *(slot for group in program.key_groups[: len(program.output_groups)] for slot in group),
            *(program.key_groups[i][0] for i in operation.positions),
        )
        expression = expression.select(
            **dict(
                zip(
                    (column.slot_id for column in stage.columns),
                    (expression[name] for name in columns),
                    strict=True,
                )
            )
        )
    elif isinstance(operation, KeyImage):
        expression = expression.select(
            **dict(
                zip(
                    (column.slot_id for column in stage.columns),
                    (expression[name] for name in program.keys),
                    strict=True,
                )
            )
        )
    elif isinstance(operation, (FilterValues, SelectValues, TopValues)):
        selected = _lower_native_operation(expression, stage, program, operation, owner, checks)
        if isinstance(selected, str):
            return selected
        expression, checks = selected
    else:
        return "producer operation is outside the admitted field relation grammar"
    return expression, checks


def _common_read_issue(
    owner: SourceSession, stage: SourceReadStage, read: LoweredRead
) -> str | None:
    if read_members(stage):
        if not owner.provider.query_policy.supports_fused_arithmetic or any(
            read_reduction(member) is None
            or read_components(member) is not None
            or read_finish(member) is not None
            or require_reduction(member).duration is not None
            or require_reduction(member).calendar is not None
            or require_reduction(member).calendars
            or require_reduction(member).sampled
            or require_reduction(member).window_key is not None
            or require_reduction(member).preserve_sample_presence
            for member in read_members(stage)
        ):
            return "common producer retains its original component or sample finish"
        if owner.provider.query_policy.key_fusion != "local":
            for member in read_members(stage):
                issue = qualify_query_source(owner, member, read, frozenset())
                if issue is not None:
                    return issue
    return None


@dataclass(frozen=True, slots=True)
class _NativeRelationLowerer:
    prepared: PreparedSources
    lowered: LoweredQuery
    owner: SourceSession
    by_id: Mapping[str, QueryStage]
    allowed_consumers: frozenset[str]
    terminal: bool
    attribute_parent: PopulationInput | None
    attribute_leaf: FrameKeysInput | None
    field_inputs: set[str]
    integer_identities: dict[str, set[str]]

    def lower(self, identifier: str, parent: str | None) -> NativeQueryRelation | str:
        stage = self.by_id[identifier]
        if (
            parent is not None
            and self.lowered.plan.consumers_of(identifier) != (parent,)
            and not set(self.lowered.plan.consumers_of(identifier)) <= self.allowed_consumers
        ) or (identifier == self.lowered.plan.publish and not self.terminal):
            return "producer has shared consumers or a publication boundary"
        if (
            self.attribute_parent is not None
            and self.attribute_leaf is not None
            and identifier == self.attribute_parent.stage_id
        ):
            return self._attribute_relation(identifier, self.attribute_parent, self.attribute_leaf)
        if not stage.columns and not (isinstance(stage, SourceReadStage) and read_presence(stage)):
            return "zero-column unit domains retain their checked presence/cardinality boundary"
        if isinstance(stage, SourceReadStage):
            return self._read(stage)
        if not isinstance(stage, LocalCompute):
            return "fixed Frame data is outside source fusion"
        return self._local(stage)

    def _attribute_relation(
        self, identifier: str, attribute_parent: PopulationInput, attribute_leaf: FrameKeysInput
    ) -> NativeQueryRelation | str:
        prepared = self.prepared
        frame_parameter = attribute_leaf.parameter
        relation = prepared.entities.get(
            "population-parameter:" + computation_fingerprint(frame_parameter.definition)
        )
        if (
            relation is None
            or tuple(relation.columns)
            != (
                tuple(column.slot_id for column in frame_parameter.columns)
                if frame_parameter.columns
                else ("__population_unit",)
            )
            or any(
                relation[column.slot_id].type() != ibis_type(column.dtype)
                for column in frame_parameter.columns
            )
        ):
            return "Attribute parent has no exact prepared parameter relation"
        expression = (
            relation.select(
                **dict(
                    zip(
                        attribute_parent.keys,
                        (relation[key] for key in attribute_leaf.keys),
                        strict=True,
                    )
                )
            )
            if attribute_parent.keys
            else relation.select("__population_unit")
        )
        return NativeQueryRelation(
            expression,
            frozenset({identifier}),
            (),
            parameters=(
                SourceParameter(
                    attribute_leaf.stage_id,
                    frame_parameter.placeholder,
                    frame_parameter.columns,
                ),
            ),
        )

    def _read(self, stage: SourceReadStage) -> NativeQueryRelation | str:
        identifier = stage.stage_id
        owner, prepared, attribute_leaf = self.owner, self.prepared, self.attribute_leaf
        identities = self.integer_identities.get(identifier, set())
        field_input = identifier in self.field_inputs
        read = self.lowered.read(identifier)
        allowed_parameters: frozenset[ops.Node] = frozenset()
        if stage.parameters:
            if attribute_leaf is None or any(
                parameter
                != SourceParameter(
                    attribute_leaf.stage_id,
                    attribute_leaf.parameter.placeholder,
                    attribute_leaf.parameter.columns,
                )
                for parameter in stage.parameters
            ):
                return "source retains a parameter outside its exact Attribute parent"
            relation = prepared.entities.get(
                "population-parameter:"
                + computation_fingerprint(attribute_leaf.parameter.definition)
            )
            if relation is None:
                return "Attribute parent parameter is unavailable"
            allowed_parameters = frozenset(relation.op().find(ops.UnboundTable))
        if prepared.sources.get(stage.source_id, prepared.source) is not owner or any(
            not _physical_owner(scope, owner, prepared, allowed_parameters=allowed_parameters)
            for scope in _read_scopes(read)
        ):
            return "producer physical inputs do not belong to the consumer SourceSession"
        if read_finish(stage) is not None:
            return "producer retains a governed host finish"
        common_issue = _common_read_issue(owner, stage, read)
        if common_issue is not None:
            return common_issue
        if owner.provider.query_policy.needs_remote_carrier_qualification:
            issue = qualify_query_source(
                owner,
                stage,
                read,
                frozenset(identities),
                frozenset(
                    column.slot_id for column in stage.columns if column.slot_id not in identities
                )
                if field_input
                else frozenset(),
            )
            if issue is not None:
                return issue
        admitted = {
            name
            for name, expected, _ in stage.preparation_checks
            if expected
            in (
                "non-null complete identity keys",
                "a non-null complete identity for every input tuple",
            )
        }
        reduction = read_reduction(stage)
        if reduction is None:
            issue = _native_field_issue(stage, read, owner, identities, field_input, admitted)
            if issue is not None:
                return issue
        elif (
            not qualified_query_provider(owner)
            or (
                owner.provider.query_policy.key_fusion == "key_counts"
                and not (
                    isinstance(reduction.node, EntityCount)
                    or (
                        isinstance(reduction.node, SourceAggregate)
                        and reduction.node.reducer in ("count", "count_distinct")
                    )
                )
            )
            or (
                owner.provider.query_policy.key_fusion == "key_counts"
                and any(
                    column.slot_id in stage.keys and column.dtype != ScalarDataType("string")
                    for column in stage.columns
                )
            )
            or reduction.calendar
            or reduction.calendars
            or reduction.duration is not None
            or reduction.sampled
            or reduction.preserve_sample_presence
            or reduction.window_key is not None
            or (
                isinstance(reduction.node, SourceAggregate)
                and reduction.node.reducer.startswith("approx_")
            )
        ):
            return "aggregate producer retains an unqualified source finish"
        if (
            set(stage.inputs) != {parameter.stage_id for parameter in stage.parameters}
            or read_components(stage) is not None
            or any(name not in admitted for name, _, _ in stage.preparation_checks)
        ):
            return "producer retains dynamic inputs, components or unsupported checks"
        return _project_native_read(stage, read, owner.provider.name)

    def _local(self, stage: LocalCompute) -> NativeQueryRelation | str:
        identifier, owner = stage.stage_id, self.owner
        if local_steps(stage):
            return "producer retains a registered local fusion boundary"
        if isinstance(stage.program, ResultProgram) and isinstance(
            stage.program.operation, LiteralValues
        ):
            return (
                "literal inputs retain their typed bulk key transport without per-row SQL expansion"
            )
        inputs: list[NativeQueryRelation] = []
        for dependency in stage.inputs:
            result = self.lower(dependency, identifier)
            if isinstance(result, str):
                return result
            inputs.append(result)
        sources = tuple(
            {item.stage_id: item for value in inputs for item in value.sources}.values()
        )
        members = frozenset({identifier}).union(*(value.stages for value in inputs))
        checks = tuple(check for value in inputs for check in value.checks)
        parameter_map: dict[str, SourceParameter] = {}
        for native_input in inputs:
            for parameter in native_input.parameters:
                previous = parameter_map.get(parameter.placeholder)
                if previous is not None and previous != parameter:
                    return "native inputs retain conflicting Attribute parent parameters"
                parameter_map[parameter.placeholder] = parameter
        parameters = tuple(parameter_map.values())
        if isinstance(stage.program, FormulaProgram):
            return self._formula(stage, stage.program, inputs, members, sources, checks, parameters)
        program = local_result(stage)
        if program is not None and isinstance(program.operation, PopulationValues):
            expression = _population_relation(program, tuple(inputs), owner.provider.name)
            if isinstance(expression, str):
                return expression
            return NativeQueryRelation(expression, members, sources, checks, parameters)
        if program is None or len(inputs) != 1 or program.duration_operations:
            return "producer is outside the admitted field relation grammar"
        selected = _native_local_projection(inputs[0].expression, stage, program, owner, checks)
        if isinstance(selected, str):
            return selected
        expression, checks = selected
        return NativeQueryRelation(expression, members, sources, checks, parameters)

    def _formula(
        self,
        stage: LocalCompute,
        program: FormulaProgram,
        inputs: list[NativeQueryRelation],
        members: frozenset[str],
        sources: tuple[LoweredRead, ...],
        checks: tuple[NumericCheck, ...],
        parameters: tuple[SourceParameter, ...],
    ) -> NativeQueryRelation | str:
        owner = self.owner
        formula = program
        relations = dict(zip(stage.inputs, (value.expression for value in inputs), strict=True))
        if formula.counts:
            folded = _native_member_counts(
                formula,
                relations,
                stage.keys,
                {binding.stage_id: owner.provider.name for binding in formula.populations},
                {
                    stage.stage_id: stage.unique_keys
                    for stage in self.lowered.plan.stages
                    if isinstance(stage, SourceReadStage)
                },
            )
            if isinstance(folded, str):
                return folded
            formula, relations = folded
        if formula.values:
            arithmetic = owner.provider.query_policy.supports_fused_arithmetic
            if len(formula.values) != 1 and not arithmetic:
                return "this provider retains multiple completed input grids"
            closure = walk(
                (node for _, node in formula.outputs), resolve=dict(formula.declared).get
            )
            if not arithmetic and any(isinstance(node, MetricExpression) for node in closure.order):
                return "aggregate producer retains local Metric arithmetic finishing"
            # Common source blocks keep logical binding ids while their
            # positional inputs point at the shared physical head.
            relations.update(
                (binding.stage_id, value.expression)
                for binding, value in zip(
                    program.values, inputs[: len(program.values)], strict=True
                )
            )
            native_formula = lower_native_formula(
                formula,
                relations,
                stage.keys,
                provider=owner.provider.name,
                alignment="domain"
                if owner.provider.query_policy.uses_identity_alignment_domain
                else "outer",
            )
            if isinstance(native_formula, str):
                return native_formula
            expression, formula_checks = native_formula
            checks = (
                *checks,
                *(
                    NumericCheck(expression[name], check.expected, check.received)
                    for name, check in formula_checks
                ),
            )
            expression = expression.select(*(column.slot_id for column in stage.columns))
            return NativeQueryRelation(expression, members, sources, checks, parameters)
        if (
            formula.values
            or formula.outputs
            or formula.calendar is not None
            or bool(formula.counts)
            or formula.output_population is None
            or tuple(column.slot_id for column in stage.columns) != stage.keys
        ):
            return "producer formula is not a field-only complete population"
        selected = next(
            (item for item in formula.populations if item.definition == formula.output_population),
            None,
        )
        if selected is None:
            return "producer formula has no exact complete population input"
        expression = inputs[stage.inputs.index(selected.stage_id)].expression
        # The exact complete Population already has set semantics; this
        # projection only renames its slots and cannot collapse tuples.
        expression = expression.select(
            **dict(zip(stage.keys, (expression[key] for key in selected.keys), strict=True))
        )
        return NativeQueryRelation(expression, members, sources, checks, parameters)


def native_query_relation(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    producer: str,
    owner: SourceSession,
    *,
    terminal: bool = False,
    shared: bool = False,
    allowed_consumers: frozenset[str] = frozenset(),
) -> NativeQueryRelation | str:
    """Qualify one finite relation without weakening source ownership or checks."""
    plan = lowered.plan
    by_id = {stage.stage_id: stage for stage in plan.stages}
    internal: set[str] = set(allowed_consumers)

    def include(identifier: str) -> None:
        if identifier not in internal:
            internal.add(identifier)
            for dependency in by_id[identifier].inputs:
                include(dependency)

    include(producer)
    allowed_consumers = frozenset(internal)
    attribute_parent, attribute_leaf = _terminal_attribute_inputs(by_id[producer], by_id, terminal)
    field_inputs = _field_inputs(by_id, internal)
    integer_identities: dict[str, set[str]] = {}
    if owner.provider.query_policy.key_fusion != "local":
        for item in plan.stages:
            if not isinstance(item, LocalCompute) or not isinstance(item.program, FormulaProgram):
                continue
            for population in item.program.populations:
                if len(population.definition.axes) != len(population.key_groups):
                    continue
                slots = integer_identities.setdefault(population.stage_id, set())
                for axis, group in zip(
                    population.definition.axes, population.key_groups, strict=True
                ):
                    if isinstance(axis, AxisBinding) and axis.field is None:
                        slots.update(group)

    consumers = plan.consumers_of(producer)
    if not (terminal or shared) and len(consumers) != 1:
        return "producer has shared consumers or a publication boundary"
    return _NativeRelationLowerer(
        prepared,
        lowered,
        owner,
        by_id,
        allowed_consumers,
        terminal,
        attribute_parent,
        attribute_leaf,
        field_inputs,
        integer_identities,
    ).lower(producer, None if terminal or shared else consumers[0])
