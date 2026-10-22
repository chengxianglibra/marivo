"""Qualify single-owner field selection as a native Query-key subquery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from math import isfinite
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.correspondence import coordinate_position
from marivo._definition.expressions import (
    EntityCount,
    MemberCount,
    MetricExpression,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    SourceAggregate,
)
from marivo._definition.graph import computation_fingerprint, walk
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    KeyImage,
    LiteralValues,
    PairStatistic,
    PopulationValues,
    ReadFields,
    ReduceValues,
    SelectValues,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo._definition.relations import (
    AttributeKeys,
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    sample_population,
)
from marivo._definition.types import AxisBinding
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import (
    FrameKeysInput,
    LocalCompute,
    NativeRead,
    QueryPlan,
    QueryStage,
    SourceRead,
    common_program,
    local_result,
    local_steps,
    read_components,
    read_finish,
    read_members,
    read_native_reductions,
    read_reduction,
    require_reduction,
)
from marivo.analysis.compiler.native_query import NativeQueryFinish
from marivo.analysis.compiler.observation import FormulaProgram, PopulationInput, ReductionInput
from marivo.analysis.compiler.optimization import RuleDecision, required_slots
from marivo.analysis.compiler.query_program import ResultProgram, SourceParameter
from marivo.analysis.compiler.source_predicates import (
    source_parameter_comparisons,
    string_without_collation,
)
from marivo.analysis.compiler.source_predicates import string_bytes as _string_bytes
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.value_types import expression_value, ibis_type
from marivo.analysis.materialization.query_parameters import _replace_parameter
from marivo.analysis.materialization.source_query_policy import (
    qualified_query_provider,
    qualify_query_reduce,
    qualify_query_source,
    qualify_query_top,
)
from marivo.semantic._numeric_lowering import NumericCheck, NumericValue, numeric_binary

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from marivo.datasource.adapters import SourceSession


def _decimal_shape(value: Decimal) -> tuple[int, int] | None:
    if not value.is_finite():
        return None
    parts = value.as_tuple()
    exponent = parts.exponent
    assert isinstance(exponent, int)
    scale = max(-exponent, 0)
    precision = max(len(parts.digits) + max(exponent, 0), scale, 1)
    return (precision, scale) if precision <= 38 else None


def _predicate_value(
    value: ValueExpression,
    values: tuple[ir.Value, ...],
    coordinates: tuple[ir.Value, ...],
    provider: str,
) -> ir.Value:
    if isinstance(value, ValueLiteral) and isinstance(value.value, Decimal):
        shape = _decimal_shape(value.value)
        assert shape is not None
        return ibis.literal(value.value, type=f"decimal({shape[0]},{shape[1]})")
    if isinstance(value, ValueBinary):
        left = _predicate_value(value.left, values, coordinates, provider)
        right = _predicate_value(value.right, values, coordinates, provider)
        if (
            value.operation in ("eq", "ne", "lt", "le", "gt", "ge")
            and left.type().is_string()
            and right.type().is_string()
        ):
            left, right = _string_bytes(left, provider), _string_bytes(right, provider)
        return expression_value(
            ValueBinary(value.operation, ValueSlot(0), ValueSlot(1)), (left, right), ()
        )
    if isinstance(value, ValueUnary):
        operand = _predicate_value(value.operand, values, coordinates, provider)
        return expression_value(ValueUnary(value.operation, ValueSlot(0)), (operand,), ())
    return expression_value(value, values, coordinates)


def _checked_value(
    value: ValueExpression,
    values: tuple[ir.Value, ...],
    coordinates: tuple[ir.Value, ...],
    provider: str,
) -> NumericValue | str:
    """Lower a finite Column expression without discarding demanded checks."""
    if isinstance(value, ValueBinary):
        left = _checked_value(value.left, values, coordinates, provider)
        right = _checked_value(value.right, values, coordinates, provider)
        if isinstance(left, str):
            return left
        if isinstance(right, str):
            return right
        if value.operation in ("add", "sub", "mul", "div"):
            if not (left.value.type().is_numeric() and right.value.type().is_numeric()):
                return "Column arithmetic retains a nonnumeric or Duration finish"
            result = numeric_binary(
                {"add": "add", "sub": "subtract", "mul": "multiply", "div": "divide"}[
                    value.operation
                ],
                left,
                right,
            )
            return result
        expression = _predicate_value(
            replace(value, left=ValueSlot(0), right=ValueSlot(1)),
            (left.value, right.value),
            (),
            provider,
        )
        return NumericValue(expression, (*left.checks, *right.checks))
    if isinstance(value, ValueUnary):
        operand = _checked_value(value.operand, values, coordinates, provider)
        if isinstance(operand, str):
            return operand
        if value.operation in ("neg", "abs") and (
            not operand.value.type().is_numeric() or operand.value.type().is_integer()
        ):
            return "integer unary Columns retain their existing local normalization"
        return NumericValue(
            expression_value(replace(value, operand=ValueSlot(0)), (operand.value,), ()),
            operand.checks,
        )
    return NumericValue(_predicate_value(value, values, coordinates, provider))


def _distinct(expression: ir.Table, provider: str) -> ir.Table:
    """Deduplicate returned string tuples independently of source collations."""
    if not any(expression[name].type().is_string() for name in expression.columns):
        return expression.distinct()
    groups: dict[str, ir.Value] = {}
    strings: dict[str, ir.Value] = {}
    for index, name in enumerate(expression.columns):
        value = expression[name]
        if value.type().is_string():
            groups[f"__query_distinct_{index}"] = _string_bytes(value, provider)
            strings[name] = value.min()
        else:
            groups[name] = value
    return expression.aggregate(**strings, by=groups).select(*expression.columns)


@dataclass(frozen=True, slots=True)
class NativeQueryRelation:
    expression: ir.Table
    stages: frozenset[str]
    sources: tuple[LoweredRead, ...]
    checks: tuple[NumericCheck, ...] = ()
    parameters: tuple[SourceParameter, ...] = ()

    def reductions(self, plan: QueryPlan) -> tuple[ReductionInput, ...]:
        return tuple(
            reduction
            for source in self.sources
            for reduction in (
                *(
                    require_reduction(member)
                    for member in read_members(plan.source_read(source.stage_id))
                    or (plan.source_read(source.stage_id),)
                    if read_reduction(member) is not None
                ),
                *read_native_reductions(plan.source_read(source.stage_id)),
            )
        )


def _logical_reads(reads: tuple[SourceRead, ...]) -> tuple[SourceRead, ...]:
    logical: dict[str, SourceRead] = {}
    for read in reads:
        for item in (
            _logical_reads(read.native_reads or read_members(read))
            if (read.native_reads or read_members(read))
            else (read,)
        ):
            logical.setdefault(item.stage_id, item)
    return tuple(logical.values())


@dataclass(frozen=True, slots=True)
class _NativePopulation:
    expression: ir.Table
    groups: tuple[tuple[str, ...], ...]


def _population_relation(
    program: ResultProgram, inputs: tuple[NativeQueryRelation, ...], provider: str
) -> ir.Table | str:
    operation = program.operation
    assert isinstance(operation, PopulationValues)
    available = {
        computation_fingerprint(atom): _NativePopulation(value.expression, groups)
        for atom, value, groups in zip(
            program.population_inputs, inputs, program.population_layouts, strict=True
        )
    }

    def project(value: _NativePopulation, positions: tuple[int, ...]) -> _NativePopulation | str:
        selected = tuple(key for position in positions for key in value.groups[position])
        if not selected:
            return "zero-axis Population algebra retains checked unit presence"
        keys = tuple(f"key.{index}" for index in range(len(selected)))
        expression = _distinct(
            value.expression.select(
                **dict(zip(keys, (value.expression[key] for key in selected), strict=True))
            ),
            provider,
        )
        groups: list[tuple[str, ...]] = []
        offset = 0
        for position in positions:
            width = len(value.groups[position])
            groups.append(keys[offset : offset + width])
            offset += width
        return _NativePopulation(expression, tuple(groups))

    def matches(
        condition: PredicateExpression, operand: PopulationDependency, value: _NativePopulation
    ) -> _NativePopulation | str:
        table = value.expression.view()
        original = tuple(table.columns)
        values: dict[str, object] = {}
        for index, leaf in enumerate(condition.inputs):
            assert isinstance(leaf, PopulationContains)
            other = rows(leaf.population)
            if isinstance(other, str):
                return other
            marker = f"__population_match_{index}"
            renamed = {
                name: f"__population_match_{index}_{position}"
                for position, name in enumerate(other.expression.columns)
            }
            right = _distinct(
                other.expression.select(
                    **{name: other.expression[old] for old, name in renamed.items()}
                ),
                provider,
            ).mutate(**{marker: ibis.literal(True)})
            predicates = tuple(
                _same_key(table[a], right[renamed[b]], provider)
                for axis, group in zip(leaf.population.axes, other.groups, strict=True)
                for a, b in zip(
                    value.groups[coordinate_position(operand.axes, axis)], group, strict=True
                )
            )
            table = table.left_join(right, predicates or ibis.literal(True)).select(
                table, right[marker]
            )
        for index, leaf in enumerate(condition.inputs):
            assert isinstance(leaf, PopulationContains)
            values[f"slot_{index}"] = table[f"__population_match_{index}"].fill_null(False)
        selected = condition.recipe.resolve(values)
        assert isinstance(selected, ir.BooleanValue)
        return _NativePopulation(table.filter(selected).select(*original), value.groups)

    def rows(domain: PopulationDependency) -> _NativePopulation | str:
        found = available.get(computation_fingerprint(domain))
        if found is not None:
            return project(found, tuple(range(len(found.groups))))
        if not isinstance(domain, PopulationProgram):
            return "Population relation requires a planned checked atom"
        node = domain.root
        if isinstance(node, PopulationProject):
            value = rows(node.operand)
            return value if isinstance(value, str) else project(value, node.positions)
        if isinstance(node, PopulationSet):
            left, right = rows(node.left), rows(node.right)
            if isinstance(left, str):
                return left
            if isinstance(right, str):
                return right
            if node.operation == "union":
                return _NativePopulation(
                    _distinct(left.expression.union(right.expression, distinct=False), provider),
                    left.groups,
                )
            lhs, rhs = left.expression.view(), right.expression.view()
            conditions = [_same_key(lhs[key], rhs[key], provider) for key in lhs.columns]
            joined = (
                lhs.semi_join(rhs, conditions)
                if node.operation == "intersection"
                else lhs.anti_join(rhs, conditions)
            )
            return _NativePopulation(joined, left.groups)
        if isinstance(node, PopulationProduct):
            combined: ir.Table | None = None
            groups: list[tuple[str, ...]] = []
            offset = 0
            for factor in node.factors:
                value = rows(factor)
                if isinstance(value, str):
                    return value
                aliases = {
                    key: f"key.{offset + index}"
                    for index, key in enumerate(value.expression.columns)
                }
                expression = value.expression.select(
                    **{alias: value.expression[key] for key, alias in aliases.items()}
                )
                groups.extend(tuple(aliases[key] for key in group) for group in value.groups)
                combined = expression if combined is None else combined.cross_join(expression)
                offset += len(aliases)
            if combined is None:
                return "empty Population product retains checked unit presence"
            return _NativePopulation(_distinct(combined, provider), tuple(groups))
        if isinstance(node, PopulationFilter):
            value = rows(node.operand)
            return value if isinstance(value, str) else matches(node.predicate, node.operand, value)
        return "Population source lookup retains its prepared attribute or relationship route"

    result = rows(operation.definition)
    if isinstance(result, str):
        return result
    source_keys = tuple(key for group in result.groups for key in group)
    output_keys = tuple(key for group in program.output_groups for key in group)
    return result.expression.select(
        **dict(zip(output_keys, (result.expression[key] for key in source_keys), strict=True))
    )


def _consumers(stages: tuple[QueryStage, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (stage.stage_id, tuple(item.stage_id for item in stages if stage.stage_id in item.inputs))
        for stage in stages
    )


def _prune_transport(plan: QueryPlan, producer: str) -> tuple[QueryPlan, tuple[str, ...]]:
    """Drop formula Population inputs that supply no output, member or sample domain."""
    removed: list[str] = []
    for original in plan.stages:
        if not isinstance(original, LocalCompute) or not isinstance(
            original.program, FormulaProgram
        ):
            continue
        program = original.program
        if program.output_population is not None or (bool(program.counts) and original.keys):
            continue
        closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
        if any(isinstance(node, MemberCount) for node in closure.order):
            continue
        sample_domains = tuple(
            domain
            for node in closure.order
            if isinstance(node, (PerReduce, PerWeightedMean))
            and isinstance(domain := sample_population(node.samples), PopulationProgram)
        )
        by_id = {stage.stage_id: stage for stage in plan.stages}
        redundant = tuple(
            binding.stage_id
            for binding in program.populations
            if binding.definition not in sample_domains
            and isinstance((stage := by_id[binding.stage_id]), LocalCompute)
            and isinstance(stage.program, ResultProgram)
            and isinstance(stage.program.operation, PopulationValues)
            and producer in stage.inputs
            and plan.consumers_of(stage.stage_id) == (original.stage_id,)
        )
        if not redundant:
            continue
        compute = replace(
            original,
            inputs=tuple(value for value in original.inputs if value not in redundant),
            program=replace(
                program,
                counts=() if not original.keys else program.counts,
                populations=tuple(
                    value for value in program.populations if value.stage_id not in redundant
                ),
            ),
        )
        stages = tuple(
            compute if stage.stage_id == original.stage_id else stage
            for stage in plan.stages
            if stage.stage_id not in redundant
        )
        plan = replace(plan, stages=stages, consumers=_consumers(stages))
        removed.extend(redundant)
    return plan, tuple(removed)


def _host_domain_selections(plan: QueryPlan) -> frozenset[str]:
    """Keep selections whose completed keys supply a real host Population domain."""
    by_id = {stage.stage_id: stage for stage in plan.stages}
    visited: set[str] = set()
    selected: set[str] = set()

    def include(identifier: str) -> None:
        if identifier in visited:
            return
        visited.add(identifier)
        stage = by_id[identifier]
        if (
            isinstance(stage, LocalCompute)
            and isinstance(stage.program, ResultProgram)
            and isinstance(stage.program.operation, (FilterValues, TopValues))
        ):
            selected.add(identifier)
        for dependency in stage.inputs:
            include(dependency)

    for stage in plan.stages:
        if not isinstance(stage, LocalCompute) or not isinstance(stage.program, FormulaProgram):
            continue
        program = stage.program
        closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
        domains: list[PopulationDependency] = [
            domain
            for node in closure.order
            if isinstance(node, (PerReduce, PerWeightedMean))
            and isinstance(domain := sample_population(node.samples), PopulationProgram)
        ]
        if program.output_population is not None:
            domains.append(program.output_population)
        if bool(program.counts):
            domains.extend(count.population for count in program.counts)
        for binding in program.populations:
            if binding.definition in domains:
                include(binding.stage_id)
    return frozenset(selected)


def _safe_expression(
    value: ValueExpression, *, native_float: bool = False, native_arithmetic: bool = False
) -> bool:
    if isinstance(value, (ValueSlot, CoordinateSlot, ValueLiteral)):
        return not isinstance(value, ValueLiteral) or not (
            isinstance(value.value, timedelta)
            or (isinstance(value.value, Decimal) and _decimal_shape(value.value) is None)
            or (isinstance(value.value, float) and not (native_float and isfinite(value.value)))
            or (isinstance(value.value, str) and "\x00" in value.value)
            or (isinstance(value.value, pd.Timestamp) and value.value.nanosecond != 0)
        )
    if isinstance(value, ValueUnary):
        return (value.operation in ("not", "is_null") or native_arithmetic) and _safe_expression(
            value.operand, native_float=native_float, native_arithmetic=native_arithmetic
        )
    if isinstance(value, ValueBinary):
        return (
            (
                value.operation in ("eq", "ne", "lt", "le", "gt", "ge", "and", "or")
                or native_arithmetic
            )
            and _safe_expression(
                value.left, native_float=native_float, native_arithmetic=native_arithmetic
            )
            and _safe_expression(
                value.right, native_float=native_float, native_arithmetic=native_arithmetic
            )
        )
    return False


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
    attribute_parent: PopulationInput | None = None
    attribute_leaf: FrameKeysInput | None = None
    endpoint = by_id[producer]
    if (
        terminal
        and isinstance(endpoint, LocalCompute)
        and isinstance(endpoint.program, FormulaProgram)
    ):
        formula = endpoint.program
        scope = formula.parent_scope
        if (
            formula.values
            and formula.calendar is None
            and not formula.counts
            and formula.output_population is None
            and isinstance(scope, PopulationProgram)
            and isinstance(scope.root, AttributeKeys)
            and scope.root.calendar_token is None
            and len(formula.populations) == 1
            and formula.populations[0].definition == scope
        ):
            binding = formula.populations[0]
            projected = by_id[binding.stage_id]
            if (
                isinstance(projected, LocalCompute)
                and isinstance(projected.program, ResultProgram)
                and len(projected.inputs) == 1
                and isinstance(leaf := by_id[projected.inputs[0]], FrameKeysInput)
                and leaf.parameter.definition == scope
                and projected.keys == binding.keys
                and tuple(column.slot_id for column in projected.columns) == binding.keys
            ):
                result = projected.program
                direct_image = (
                    isinstance(result.operation, KeyImage)
                    and result.keys == leaf.keys
                    and result.columns == leaf.columns
                )
                direct_population = (
                    isinstance(result.operation, PopulationValues)
                    and result.operation.definition == scope
                    and result.population_inputs == (scope,)
                    and len(result.population_layouts) == 1
                    and tuple(key for group in result.population_layouts[0] for key in group)
                    == leaf.keys
                    and tuple(key for group in result.output_groups for key in group)
                    == binding.keys
                )
                if direct_image or direct_population:
                    attribute_parent, attribute_leaf = binding, leaf
    field_inputs: set[str] = set()
    for item in plan.stages:
        if (
            item.stage_id not in internal
            or not isinstance(item, LocalCompute)
            or not isinstance(item.program, ResultProgram)
            or not isinstance(item.program.operation, ReadFields)
        ):
            continue
        pending = list(item.inputs)
        while pending:
            identifier = pending.pop()
            if identifier not in field_inputs:
                field_inputs.add(identifier)
                pending.extend(by_id[identifier].inputs)
    integer_identities: dict[str, set[str]] = {}
    if owner.provider.name != "duckdb":
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

    def lower(identifier: str, parent: str | None) -> NativeQueryRelation | str:
        stage = by_id[identifier]
        if (
            parent is not None
            and plan.consumers_of(identifier) != (parent,)
            and not set(plan.consumers_of(identifier)) <= allowed_consumers
        ) or (identifier == plan.publish and not terminal):
            return "producer has shared consumers or a publication boundary"
        if (
            attribute_parent is not None
            and attribute_leaf is not None
            and identifier == attribute_parent.stage_id
        ):
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
        if not stage.columns:
            return "zero-column unit domains retain their checked presence/cardinality boundary"
        if isinstance(stage, SourceRead):
            read = lowered.read(identifier)
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
            if prepared.sources.get(
                stage.source_id, prepared.source
            ) is not owner or not _physical_owner(
                read.expression, owner, prepared, allowed_parameters=allowed_parameters
            ):
                return "producer physical inputs do not belong to the consumer SourceSession"
            if read_finish(stage) is not None:
                return "producer retains a governed host finish"
            if read_members(stage):
                if owner.provider.name not in ("duckdb", "trino", "clickhouse") or any(
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
                if owner.provider.name != "duckdb":
                    for member in read_members(stage):
                        issue = qualify_query_source(owner, member, read, frozenset())
                        if issue is not None:
                            return issue
            if owner.provider.name not in ("duckdb", "postgres"):
                issue = qualify_query_source(
                    owner,
                    stage,
                    read,
                    frozenset(integer_identities.get(identifier, set())),
                    frozenset(
                        column.slot_id
                        for column in stage.columns
                        if column.slot_id not in integer_identities.get(identifier, set())
                    )
                    if identifier in field_inputs
                    else frozenset(),
                )
                if issue is not None:
                    return issue
            admitted = {"__identity_null", "__population_classify_invalid"}
            reduction = read_reduction(stage)
            if reduction is None:
                if owner.provider.name == "postgres" and any(
                    (
                        column.slot_id not in integer_identities.get(identifier, set())
                        and column.dtype != ScalarDataType("string")
                    )
                    or isinstance(column.dtype, DecimalDataType)
                    for column in stage.columns
                ):
                    return "PostgreSQL field relations qualify decoded strings and complete identity keys; other values retain their pandas boundary"
                if owner.provider.name not in ("duckdb", "trino", "clickhouse") and any(
                    column.dtype == ScalarDataType("float64") for column in stage.columns
                ):
                    return "floating producer slots retain pandas missing-value semantics"
                if any(
                    name not in admitted and not name.startswith("__field_nonfinite_")
                    for name, _ in read.numeric_checks
                ) or {
                    name
                    for name, _ in read.numeric_checks
                    if not name.startswith("__field_nonfinite_")
                } != {name for name, _, _ in stage.preparation_checks}:
                    return "field producer retains an unsupported check"
            elif (
                not qualified_query_provider(owner)
                or (
                    owner.provider.name == "postgres"
                    and not (
                        isinstance(reduction.node, EntityCount)
                        or (
                            isinstance(reduction.node, SourceAggregate)
                            and reduction.node.reducer in ("count", "count_distinct")
                        )
                    )
                )
                or (
                    owner.provider.name == "postgres"
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
                    and (
                        reduction.node.reducer.startswith("approx_")
                        or (
                            reduction.node.reducer in ("median", "quantile")
                            and any(
                                isinstance(column.dtype, DecimalDataType)
                                for column in stage.columns
                            )
                        )
                    )
                )
            ):
                return "aggregate producer retains an unqualified source finish"
            if (
                set(stage.inputs) != {parameter.stage_id for parameter in stage.parameters}
                or read_components(stage) is not None
                or any(name not in admitted for name, _, _ in stage.preparation_checks)
            ):
                return "producer retains dynamic inputs, components or unsupported checks"
            expression = read.expression
            if "__numeric_present" in expression.columns:
                expression = expression.filter(expression["__numeric_present"].fill_null(False))
            expression = expression.select(
                **{
                    column.slot_id: string_without_collation(
                        expression[column.slot_id], owner.provider.name
                    )
                    if expression[column.slot_id].type().is_string()
                    else expression[column.slot_id]
                    for column in stage.columns
                }
            )
            return NativeQueryRelation(
                expression, frozenset({identifier}), (read,), parameters=stage.parameters
            )
        if not isinstance(stage, LocalCompute):
            return "fixed Frame data is outside source fusion"
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
            result = lower(dependency, identifier)
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
            formula = stage.program
            if formula.values:
                arithmetic = owner.provider.name in ("duckdb", "trino", "clickhouse")
                if len(formula.values) != 1 and not arithmetic:
                    return "this provider retains multiple completed input grids"
                closure = walk(
                    (node for _, node in formula.outputs), resolve=dict(formula.declared).get
                )
                if not arithmetic and any(
                    isinstance(node, MetricExpression) for node in closure.order
                ):
                    return "aggregate producer retains local Metric arithmetic finishing"
                from marivo.analysis.compiler.native_formula import lower_native_formula

                relations = dict(
                    zip(stage.inputs, (value.expression for value in inputs), strict=True)
                )
                # Common source blocks keep logical binding ids while their
                # positional inputs point at the shared physical head.
                relations.update(
                    (binding.stage_id, value.expression)
                    for binding, value in zip(
                        formula.values, inputs[: len(formula.values)], strict=True
                    )
                )
                native_formula = lower_native_formula(
                    formula,
                    relations,
                    stage.keys,
                    alignment="domain"
                    if owner.provider.name in ("trino", "clickhouse")
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
                (
                    item
                    for item in formula.populations
                    if item.definition == formula.output_population
                ),
                None,
            )
            if selected is None:
                return "producer formula has no exact complete population input"
            expression = inputs[stage.inputs.index(selected.stage_id)].expression
            expression = _distinct(
                expression.select(
                    **dict(zip(stage.keys, (expression[key] for key in selected.keys), strict=True))
                ),
                owner.provider.name,
            )
            return NativeQueryRelation(expression, members, sources, checks, parameters)
        program = local_result(stage)
        if program is not None and isinstance(program.operation, PopulationValues):
            expression = _population_relation(program, tuple(inputs), owner.provider.name)
            if isinstance(expression, str):
                return expression
            return NativeQueryRelation(expression, members, sources, checks, parameters)
        if program is None or len(inputs) != 1 or program.duration_operations:
            return "producer is outside the admitted field relation grammar"
        expression = inputs[0].expression
        operation = program.operation
        if isinstance(operation, ReadFields):
            columns = (
                *program.key_groups[0],
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
        elif isinstance(operation, (FilterValues, SelectValues, TopValues, ReduceValues)):
            arithmetic = owner.provider.name in ("duckdb", "trino", "clickhouse")
            # Demand pruning retains the original positional expression layout.
            # Typed placeholders occupy only slots unused by this operation.
            values = tuple(
                expression[column.slot_id]
                if column.slot_id in expression.columns
                else ibis.null().cast(ibis_type(column.dtype))
                for column in (
                    program.input_layout.values
                    if program.input_layout is not None
                    else tuple(
                        column for column in program.columns if column.slot_id not in program.keys
                    )
                )
            )
            if any(len(group) != 1 for group in program.key_groups):
                coordinates = ()
            else:
                coordinates = tuple(expression[group[0]] for group in program.key_groups)
            if isinstance(operation, FilterValues):
                if not isinstance(operation.predicate, ValueExpression) or not _safe_expression(
                    operation.predicate,
                    native_float=owner.provider.name in ("duckdb", "trino", "clickhouse"),
                    native_arithmetic=arithmetic,
                ):
                    return "producer filter has nonlocal or numeric expression obligations"
                evaluated = _checked_value(
                    operation.predicate, values, coordinates, owner.provider.name
                )
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
                        native_float=owner.provider.name in ("duckdb", "trino", "clickhouse"),
                        native_arithmetic=arithmetic,
                    )
                    for _, value in operation.values
                ) or (
                    owner.provider.name not in ("duckdb", "trino", "clickhouse")
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
                    if evaluated.value.type().is_floating():
                        checks = (
                            *checks,
                            NumericCheck(
                                evaluated.value.notnull()
                                & (evaluated.value.isnan() | evaluated.value.isinf()),
                                "a finite float64 output",
                                "Column.select: output overflow",
                            ),
                        )
                expression = expression.select(**projection)
            elif isinstance(operation, TopValues):
                if (
                    (
                        not arithmetic
                        and not isinstance(operation.score, (ValueSlot, CoordinateSlot))
                    )
                    or not qualified_query_provider(owner)
                    or not _safe_expression(
                        operation.score,
                        native_float=owner.provider.name in ("duckdb", "trino", "clickhouse"),
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
                evaluated = _checked_value(
                    operation.score, values, coordinates, owner.provider.name
                )
                if isinstance(evaluated, str):
                    return evaluated
                score = evaluated.value
                checks = (*checks, *evaluated.checks)
                if owner.provider.name not in ("duckdb", "postgres"):
                    issue = qualify_query_top(owner, program, score)
                    if issue is not None:
                        return issue
                if score.type().is_floating() and owner.provider.name not in (
                    "duckdb",
                    "trino",
                    "clickhouse",
                ):
                    return "Top-N retains pandas floating missing-score semantics"
                score = (
                    _string_bytes(score, owner.provider.name) if score.type().is_string() else score
                )
                order = (
                    score.desc(nulls_first=False)
                    if operation.descending
                    else score.asc(nulls_first=False),
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
            else:
                if any(isinstance(statistic, PairStatistic) for _, statistic in operation.values):
                    return "paired statistics retain their local Frame numeric method"
                if owner.provider.name != "duckdb":
                    issue = qualify_query_reduce(owner, program)
                    if issue is not None:
                        return issue
                aggregates: dict[str, ir.Value] = {}
                floating_sums: list[str] = []
                for index, (_, statistic) in enumerate(operation.values):
                    slot = f"value.{index}"
                    if isinstance(statistic, PairStatistic):
                        return "paired statistics retain their local Frame numeric method"
                    if statistic.method == "row_count":
                        aggregates[slot] = expression.count()
                    elif statistic.method == "sum" and isinstance(statistic.value, ValueSlot):
                        value = values[statistic.value.index]
                        if value.type().is_integer():
                            from marivo.semantic._numeric_lowering import _source_sum

                            total = _source_sum(value)
                            aggregates[slot] = (expression.count() == 0).ifelse(
                                ibis.literal(0).cast(total.type()), total
                            )
                        elif value.type().is_floating() or value.type().is_decimal():
                            if value.type().is_floating():
                                floating_sums.append(slot)
                                checks = (
                                    *checks,
                                    NumericCheck(
                                        value.notnull() & (value.isnan() | value.isinf()),
                                        "a qualified finite value statistic",
                                        "float64 state has a nonfinite component",
                                    ),
                                )
                            from marivo.semantic._numeric_lowering import _source_sum

                            total = _source_sum(value)
                            aggregates[slot] = (expression.count() == 0).ifelse(
                                ibis.literal(0).cast(total.type()), total
                            )
                        else:
                            return "cell sum retains its exact Duration finisher"
                    else:
                        return "cell reduction retains an unsupported statistic or expression"
                selected_keys = tuple(
                    key for position in operation.positions for key in program.key_groups[position]
                )
                value_slots = tuple(aggregates)
                groups: dict[str, ir.Value] = {}
                for index, (target, key) in enumerate(zip(stage.keys, selected_keys, strict=True)):
                    value = expression[key]
                    if value.type().is_string():
                        groups[f"__query_group_{index}"] = _string_bytes(value, owner.provider.name)
                        aggregates[target] = value.min()
                    else:
                        groups[target] = value
                expression = expression.aggregate(**aggregates, by=groups).select(
                    *stage.keys, *value_slots
                )
                for slot in floating_sums:
                    total = expression[slot]
                    checks = (
                        *checks,
                        NumericCheck(
                            total.notnull() & (total.isnan() | total.isinf()),
                            "a qualified finite value statistic",
                            "float64 state overflow",
                        ),
                    )
        else:
            return "producer operation is outside the admitted field relation grammar"
        return NativeQueryRelation(expression, members, sources, checks, parameters)

    consumers = plan.consumers_of(producer)
    if not (terminal or shared) and len(consumers) != 1:
        return "producer has shared consumers or a publication boundary"
    return lower(producer, None if terminal or shared else consumers[0])


def _attach_relation_checks(
    expression: ir.Table,
    native: NativeQueryRelation,
    plan: QueryPlan,
    preparation: tuple[tuple[str, str, str], ...] = (),
    numeric: tuple[tuple[str, NumericCheck], ...] = (),
) -> tuple[ir.Table, tuple[tuple[str, str, str], ...], tuple[tuple[str, NumericCheck], ...]]:
    """Keep full parent checks visible even when the selected relation is empty."""
    prepared_checks = list(preparation)
    checks = list(numeric)
    for source in native.sources:
        for name, check in source.numeric_checks:
            alias = f"__query_preparation_{len(checks)}"
            summary = source.expression.aggregate(
                **{alias: source.expression[name].any().fill_null(False)}
            )
            if "__numeric_present" not in expression.columns:
                expression = expression.mutate(__numeric_present=ibis.literal(True))
            expression = summary.left_join(expression, ibis.literal(True))
            checks.append((alias, NumericCheck(summary[alias], check.expected, check.received)))
            if any(
                name == item[0] for item in plan.source_read(source.stage_id).preparation_checks
            ):
                prepared_checks.append((alias, check.expected, check.received))
    for check in native.checks:
        alias = f"__query_numeric_{len(checks)}"
        summary = check.violation.any().fill_null(False).name(alias).as_table()
        if "__numeric_present" not in expression.columns:
            expression = expression.mutate(__numeric_present=ibis.literal(True))
        expression = summary.left_join(expression, ibis.literal(True))
        checks.append((alias, NumericCheck(summary[alias], check.expected, check.received)))
    return expression, tuple(prepared_checks), tuple(checks)


def finish_native_query(finish: NativeQueryFinish, frame: pd.DataFrame) -> pd.DataFrame:
    """Restore deterministic union order after the qualified source read."""
    return (
        frame.sort_values(list(finish.order), kind="stable", na_position="last")
        .loc[:, [column.slot_id for column in finish.columns]]
        .reset_index(drop=True)
    )


def _same_key(left: ir.Value, right: ir.Value, provider: str) -> ir.BooleanValue:
    if left.type().is_string() and right.type().is_string():
        left, right = _string_bytes(left, provider), _string_bytes(right, provider)
    result = left.identical_to(right)
    assert isinstance(result, ir.BooleanValue)
    return result


def _outer_join_value(value: ir.Value, provider: str) -> ir.Value:
    """Make absent ClickHouse endpoint cells null even with join_use_nulls=0."""
    if provider == "clickhouse":
        result = ops.Cast(value, to=value.type().copy(nullable=True)).to_expr()
        assert isinstance(result, ir.Value)
        return result
    return value


def fuse_query_terminal(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Replace a qualified terminal field relation with one governed source read."""
    by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
    final = by_id[lowered.plan.publish]
    if not isinstance(final, LocalCompute):
        return lowered
    # Source frames have canonical key order. Keep Top and its following local
    # presentation operations so display order is restored over the bounded rows.
    endpoint = final
    cursor = final
    while len(cursor.inputs) == 1 and isinstance(cursor.program, ResultProgram):
        if isinstance(cursor.program.operation, TopValues):
            endpoint = cursor
            break
        if not isinstance(cursor.program.operation, (FilterValues, SelectValues)):
            break
        parent = by_id[cursor.inputs[0]]
        if not isinstance(parent, LocalCompute):
            break
        cursor = parent
    for owner in dict.fromkeys(prepared.sources.values()):
        if not qualified_query_provider(owner):
            continue
        native = native_query_relation(prepared, lowered, endpoint.stage_id, owner, terminal=True)
        if isinstance(native, str) or not native.sources:
            continue
        finish: NativeQueryFinish | None = None
        if isinstance(endpoint.program, ResultProgram) and isinstance(
            endpoint.program.operation, PopulationValues
        ):
            if any(
                isinstance(item, LocalCompute)
                and isinstance(item.program, ResultProgram)
                and isinstance(item.program.operation, (TopValues, ReduceValues, PopulationValues))
                for item in lowered.plan.stages
                if item.stage_id in native.stages and item.stage_id != endpoint.stage_id
            ):
                continue
            domain = endpoint.program.operation.definition
            if (
                isinstance(domain, PopulationProgram)
                and isinstance(domain.root, PopulationSet)
                and domain.root.operation == "union"
            ):
                left_position = next(
                    (
                        index
                        for index, atom in enumerate(endpoint.program.population_inputs)
                        if computation_fingerprint(atom)
                        == computation_fingerprint(domain.root.left)
                    ),
                    None,
                )
                if left_position is None or any(
                    isinstance(item, LocalCompute)
                    and isinstance(item.program, ResultProgram)
                    and isinstance(
                        item.program.operation,
                        (TopValues, ReduceValues, PopulationValues),
                    )
                    for item in lowered.plan.stages
                    if item.stage_id in native.stages and item.stage_id != endpoint.stage_id
                ):
                    continue
                left = native_query_relation(
                    prepared,
                    lowered,
                    endpoint.inputs[left_position],
                    owner,
                    shared=True,
                    allowed_consumers=native.stages,
                )
                if isinstance(left, str):
                    continue
                source_keys = tuple(
                    key
                    for group in endpoint.program.population_layouts[left_position]
                    for key in group
                )
                key_relation = _distinct(
                    left.expression.select(
                        **dict(
                            zip(
                                endpoint.keys,
                                (left.expression[key] for key in source_keys),
                                strict=True,
                            )
                        )
                    ),
                    owner.provider.name,
                ).mutate(__query_left_union=ibis.literal(True))
                expression = native.expression.left_join(
                    key_relation,
                    [
                        _same_key(native.expression[key], key_relation[key], owner.provider.name)
                        for key in endpoint.keys
                    ],
                ).select(
                    *native.expression.columns,
                    __query_union_order=key_relation["__query_left_union"]
                    .fill_null(False)
                    .ifelse(0, 1),
                )
                native = replace(native, expression=expression)
                finish = NativeQueryFinish(
                    endpoint.columns,
                    ("__query_union_order", *endpoint.keys),
                    endpoint.program.output_layout,
                )
        expression, preparation, checks = _attach_relation_checks(
            native.expression, native, lowered.plan
        )
        source_id = lowered.plan.source_read(native.sources[0].stage_id).source_id
        keep_top = isinstance(endpoint.program, ResultProgram) and isinstance(
            endpoint.program.operation, TopValues
        )
        identifier = "source:terminal:" + endpoint.stage_id if keep_top else endpoint.stage_id
        stage = SourceRead(
            identifier,
            source_id,
            (),
            endpoint.columns,
            endpoint.keys,
            NativeRead(native.reductions(lowered.plan), finish),
            (),
            (),
            None,
            preparation_checks=preparation,
            native_reads=_logical_reads(
                tuple(lowered.plan.source_read(read.stage_id) for read in native.sources)
            ),
            inputs=tuple(dict.fromkeys(parameter.stage_id for parameter in native.parameters)),
            parameters=native.parameters,
        )
        replacement = replace(endpoint, inputs=(identifier,)) if keep_top else stage
        removed = native.stages - {endpoint.stage_id}
        stages = tuple(
            replacement if item.stage_id == endpoint.stage_id else item
            for item in lowered.plan.stages
            if item.stage_id not in removed
        )
        if keep_top:
            position = next(
                index for index, item in enumerate(stages) if item.stage_id == endpoint.stage_id
            )
            stages = (*stages[:position], stage, *stages[position:])
        decisions = (
            *lowered.plan.optimization_decisions,
            RuleDecision(
                "source_terminal_fusion",
                "adopted",
                tuple(sorted(native.stages)),
                "Qualified same-owner relation and full-scope checks execute at the source; Top display order is restored locally.",
            ),
        )
        plan = replace(
            lowered.plan,
            stages=stages,
            consumers=_consumers(stages),
            fusion_groups=tuple(
                group for group in lowered.plan.fusion_groups if group.output not in native.stages
            ),
            optimization_decisions=decisions,
        )
        plan = replace(plan, required_slots=required_slots(plan))
        return LoweredQuery(
            plan,
            (
                *(read for read in lowered.reads if read.stage_id not in native.stages),
                LoweredRead(stage.stage_id, expression, checks),
            ),
        )
    return lowered


def _qualified_parameter_consumer(
    prepared: PreparedSources, stage: SourceRead, read: LoweredRead, owner: SourceSession
) -> bool:
    if prepared.sources.get(stage.source_id, prepared.source) is not owner:
        return False
    placeholders = {parameter.placeholder for parameter in stage.parameters}
    allowed = frozenset(
        node for node in read.expression.op().find(ops.UnboundTable) if node.name in placeholders
    )
    if not _physical_owner(read.expression, owner, prepared, allowed_parameters=allowed):
        return False
    return owner.provider.name == "duckdb" or (
        owner.provider.name in ("trino", "clickhouse")
        and qualify_query_source(owner, stage, read, frozenset()) is None
    )


def _fuse_shared_query_keys(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Keep shared keys inside one statement for qualified compatible consumer grids."""
    from marivo.analysis.compiler.observation import FormulaProgram, common_slot

    producers = tuple(
        dict.fromkeys(
            parameter.stage_id
            for read in lowered.reads
            for parameter in lowered.plan.source_read(read.stage_id).parameters
        )
    )
    for producer in producers:
        candidate, pruned = _prune_transport(lowered.plan, producer)
        final = next(stage for stage in candidate.stages if stage.stage_id == candidate.publish)
        if not isinstance(final, LocalCompute) or not isinstance(final.program, FormulaProgram):
            continue
        reads = tuple(
            read
            for read in lowered.reads
            if any(
                parameter.stage_id == producer
                for parameter in lowered.plan.source_read(read.stage_id).parameters
            )
        )
        if len(reads) < 2 or set(candidate.consumers_of(producer)) != {
            read.stage_id for read in reads
        }:
            continue
        first = reads[0]
        first_reduction = read_reduction(lowered.plan.source_read(first.stage_id))
        if first_reduction is None:
            continue
        owner = prepared.sources.get(
            lowered.plan.source_read(first.stage_id).source_id, prepared.source
        )
        first_keys = tuple(
            column
            for column in lowered.plan.source_read(first.stage_id).columns
            if column.slot_id in lowered.plan.source_read(first.stage_id).keys
        )
        if any(
            not _qualified_parameter_consumer(
                prepared, lowered.plan.source_read(read.stage_id), read, owner
            )
            or lowered.plan.source_read(read.stage_id).keys
            != lowered.plan.source_read(first.stage_id).keys
            or tuple(
                column
                for column in lowered.plan.source_read(read.stage_id).columns
                if column.slot_id in lowered.plan.source_read(read.stage_id).keys
            )
            != first_keys
            or read_reduction(lowered.plan.source_read(read.stage_id)) is None
            or require_reduction(lowered.plan.source_read(read.stage_id)).axes
            != first_reduction.axes
            or read_members(lowered.plan.source_read(read.stage_id))
            or read_finish(lowered.plan.source_read(read.stage_id)) is not None
            or read_components(lowered.plan.source_read(read.stage_id)) is not None
            or require_reduction(lowered.plan.source_read(read.stage_id)).duration is not None
            or require_reduction(lowered.plan.source_read(read.stage_id)).calendar is not None
            or require_reduction(lowered.plan.source_read(read.stage_id)).calendars
            or require_reduction(lowered.plan.source_read(read.stage_id)).window_key is not None
            or require_reduction(lowered.plan.source_read(read.stage_id)).preserve_sample_presence
            or (
                isinstance(
                    (node := require_reduction(lowered.plan.source_read(read.stage_id)).node),
                    SourceAggregate,
                )
                and (
                    node.reducer in ("median", "quantile")
                    and any(
                        isinstance(column.dtype, DecimalDataType)
                        for column in lowered.plan.source_read(read.stage_id).columns
                    )
                )
            )
            or lowered.plan.source_read(read.stage_id).inputs != (producer,)
            or len(lowered.plan.source_read(read.stage_id).parameters) != 1
            or candidate.consumers_of(read.stage_id) != (final.stage_id,)
            for read in reads
        ):
            continue
        native = native_query_relation(
            prepared, replace(lowered, plan=candidate), producer, owner, shared=True
        )
        if isinstance(native, str):
            continue
        producer_stage = next(stage for stage in candidate.stages if stage.stage_id == producer)
        expressions: list[ir.Table] = []
        members: list[SourceRead] = []
        checks: list[tuple[str, NumericCheck]] = []
        columns: list[DataColumn] = []
        for index, read in enumerate(reads):
            parameter = lowered.plan.source_read(read.stage_id).parameters[0]
            keys = _distinct(
                native.expression.select(
                    **dict(
                        zip(
                            (column.slot_id for column in parameter.columns),
                            (native.expression[key] for key in producer_stage.keys),
                            strict=True,
                        )
                    )
                ),
                owner.provider.name,
            )
            expression = _replace_parameter(
                source_parameter_comparisons(
                    read.expression, parameter.placeholder, owner.provider.name
                ),
                parameter.placeholder,
                keys,
            )
            preparation = lowered.plan.source_read(read.stage_id).preparation_checks
            numeric = read.numeric_checks
            if index == 0:
                expression, preparation, numeric = _attach_relation_checks(
                    expression, native, lowered.plan, preparation, numeric
                )
            if "__numeric_present" not in expression.columns:
                expression = expression.mutate(__numeric_present=ibis.literal(True))
            member = replace(
                lowered.plan.source_read(read.stage_id),
                inputs=(),
                parameters=(),
                preparation_checks=preparation,
                columns=lowered.plan.source_read(read.stage_id).columns
                if any(
                    column.slot_id == "__numeric_present"
                    for column in lowered.plan.source_read(read.stage_id).columns
                )
                else (
                    *lowered.plan.source_read(read.stage_id).columns,
                    DataColumn(
                        "__numeric_present", "__numeric_present", ScalarDataType("boolean"), False
                    ),
                ),
            )
            members.append(member)
            assert read_reduction(member) is not None
            value_slot = require_reduction(member).value_slot
            names = {
                name: name
                if name in member.keys or name == value_slot
                else common_slot(index, name)
                for name in expression.columns
            }
            expressions.append(
                expression.select(
                    **{
                        alias: _outer_join_value(expression[name], owner.provider.name)
                        for name, alias in names.items()
                    }
                )
            )
            columns.extend(
                replace(column, slot_id=names[column.slot_id])
                for column in member.columns
                if index == 0 or column.slot_id not in member.keys
            )
            checks.extend((names[name], check) for name, check in numeric)
        expression = expressions[0]
        for other in expressions[1:]:
            if lowered.plan.source_read(first.stage_id).keys:
                left, right = expression.view(), other.view()
                joined = left.join(
                    right,
                    [
                        _same_key(left[key], right[key], owner.provider.name)
                        for key in lowered.plan.source_read(first.stage_id).keys
                    ],
                    how="outer",
                )
                expression = joined.select(
                    **{
                        **{
                            key: ibis.coalesce(left[key], right[key])
                            for key in lowered.plan.source_read(first.stage_id).keys
                        },
                        **{
                            name: left[name]
                            for name in left.columns
                            if name not in lowered.plan.source_read(first.stage_id).keys
                        },
                        **{
                            name: right[name]
                            for name in right.columns
                            if name not in lowered.plan.source_read(first.stage_id).keys
                        },
                    }
                )
            else:
                expression = expression.cross_join(other)
        head = replace(
            members[0],
            columns=tuple(columns),
            preparation_checks=(),
            native_reductions=(*members[0].native_reductions, *native.reductions(lowered.plan)),
            native_reads=_logical_reads(
                (*members, *(lowered.plan.source_read(read.stage_id) for read in native.sources))
            ),
            program=common_program(members[0], tuple(members)),
        )
        indices = {member.stage_id: index for index, member in enumerate(members)}

        def remap(program: FormulaProgram, indexes: dict[str, int] = indices) -> FormulaProgram:
            return replace(
                program,
                values=tuple(
                    replace(
                        binding,
                        presence=common_slot(indexes[binding.stage_id], "__numeric_present"),
                        checks=tuple(
                            (common_slot(indexes[binding.stage_id], name), expected, received)
                            for name, expected, received in binding.checks
                        ),
                    )
                    if binding.stage_id in indexes
                    else binding
                    for binding in program.values
                ),
            )

        final = replace(
            final,
            inputs=tuple(head.stage_id if value in indices else value for value in final.inputs),
            program=remap(final.program),
        )
        removed = native.stages | frozenset(pruned) | (frozenset(indices) - {head.stage_id})
        stages = tuple(
            head
            if stage.stage_id == head.stage_id
            else final
            if stage.stage_id == final.stage_id
            else stage
            for stage in candidate.stages
            if stage.stage_id not in removed
        )
        plan = replace(
            candidate,
            stages=stages,
            consumers=_consumers(stages),
            original=remap(candidate.original) if candidate.original is not None else None,
            fusion_groups=tuple(
                group for group in candidate.fusion_groups if group.output not in removed
            ),
            optimization_decisions=(
                *candidate.optimization_decisions,
                RuleDecision(
                    "source_shared_key_fusion",
                    "adopted",
                    (producer, *indices),
                    "One same-owner source statement shares the native key relation across qualified compatible scalar or grouped consumers; independent grids, presence and checks are retained.",
                ),
            ),
        )
        plan = replace(plan, required_slots=required_slots(plan))
        lowered = LoweredQuery(
            plan,
            tuple(
                LoweredRead(head.stage_id, expression, tuple(checks))
                if read.stage_id == head.stage_id
                else read
                for read in lowered.reads
                if read.stage_id not in removed
            ),
        )
    return lowered


def _fuse_parameter_blocks(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Replace mutually shared parameter producers atomically in one consumer."""
    for original in tuple(lowered.reads):
        if (
            len(lowered.plan.source_read(original.stage_id).parameters) < 2
            or read_components(lowered.plan.source_read(original.stage_id)) is not None
        ):
            continue
        owner = prepared.sources.get(
            lowered.plan.source_read(original.stage_id).source_id, prepared.source
        )
        if not _qualified_parameter_consumer(
            prepared, lowered.plan.source_read(original.stage_id), original, owner
        ):
            continue
        candidate = lowered.plan
        pruned: set[str] = set()
        for parameter in lowered.plan.source_read(original.stage_id).parameters:
            candidate, transport = _prune_transport(candidate, parameter.stage_id)
            pruned.update(transport)
        by_id = {stage.stage_id: stage for stage in candidate.stages}
        closure = {original.stage_id}

        def include(
            identifier: str, members: set[str] = closure, stages: dict[str, QueryStage] = by_id
        ) -> None:
            if identifier not in members:
                members.add(identifier)
                for dependency in stages[identifier].inputs:
                    include(dependency, members, stages)

        for parameter in lowered.plan.source_read(original.stage_id).parameters:
            include(parameter.stage_id)
        natives = tuple(
            native_query_relation(
                prepared,
                replace(lowered, plan=candidate),
                parameter.stage_id,
                owner,
                shared=True,
                allowed_consumers=frozenset(closure),
            )
            for parameter in lowered.plan.source_read(original.stage_id).parameters
        )
        if any(isinstance(native, str) for native in natives):
            continue
        native_inputs = tuple(native for native in natives if not isinstance(native, str))
        if any(
            not set(candidate.consumers_of(identifier)) <= closure
            for native in native_inputs
            for identifier in native.stages
        ):
            continue
        expression = original.expression
        compatible = True
        for parameter, native in zip(
            lowered.plan.source_read(original.stage_id).parameters, native_inputs, strict=True
        ):
            producer = by_id[parameter.stage_id]
            if len(producer.keys) != len(parameter.columns):
                compatible = False
                break
            keys = _distinct(
                native.expression.select(
                    **dict(
                        zip(
                            (column.slot_id for column in parameter.columns),
                            (native.expression[key] for key in producer.keys),
                            strict=True,
                        )
                    )
                ),
                owner.provider.name,
            )
            expression = _replace_parameter(
                source_parameter_comparisons(
                    expression, parameter.placeholder, owner.provider.name
                ),
                parameter.placeholder,
                keys,
            )
        if not compatible:
            continue
        native = NativeQueryRelation(
            expression,
            frozenset().union(*(value.stages for value in native_inputs)),
            tuple(
                {read.stage_id: read for value in native_inputs for read in value.sources}.values()
            ),
            tuple(check for value in native_inputs for check in value.checks),
        )
        expression, preparation, checks = _attach_relation_checks(
            expression,
            native,
            lowered.plan,
            lowered.plan.source_read(original.stage_id).preparation_checks,
            original.numeric_checks,
        )
        producers = {
            parameter.stage_id
            for parameter in lowered.plan.source_read(original.stage_id).parameters
        }
        stage = replace(
            lowered.plan.source_read(original.stage_id),
            inputs=tuple(
                value
                for value in lowered.plan.source_read(original.stage_id).inputs
                if value not in producers
            ),
            parameters=(),
            preparation_checks=preparation,
            native_reductions=(
                *lowered.plan.source_read(original.stage_id).native_reductions,
                *native.reductions(lowered.plan),
            ),
            native_reads=_logical_reads(
                (
                    lowered.plan.source_read(original.stage_id),
                    *(lowered.plan.source_read(read.stage_id) for read in native.sources),
                )
            ),
        )
        removed = native.stages | frozenset(pruned)
        stages = tuple(
            stage if item.stage_id == stage.stage_id else item
            for item in candidate.stages
            if item.stage_id not in removed
        )
        plan = replace(
            candidate,
            stages=stages,
            consumers=_consumers(stages),
            fusion_groups=tuple(
                group for group in candidate.fusion_groups if group.output not in removed
            ),
            optimization_decisions=(
                *candidate.optimization_decisions,
                RuleDecision(
                    "source_parameter_block",
                    "adopted",
                    tuple(sorted(removed)),
                    "All same-owner parameter producers share one source statement; complete key tuples and parent checks stay inside the consumer.",
                ),
            ),
        )
        plan = replace(plan, required_slots=required_slots(plan))
        lowered = LoweredQuery(
            plan,
            tuple(
                LoweredRead(stage.stage_id, expression, checks)
                if read.stage_id == stage.stage_id
                else read
                for read in lowered.reads
                if read.stage_id not in removed
            ),
        )
    return lowered


def _fold_population_checks(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Retain relationship-domain checks without transporting unused identity sets."""
    for candidate in tuple(lowered.plan.stages):
        if (
            not isinstance(candidate, LocalCompute)
            or not isinstance(candidate.program, FormulaProgram)
            or local_steps(candidate)
        ):
            continue
        program = candidate.program
        if program.calendar is not None or not program.populations:
            continue
        if any(
            isinstance(node, (MemberCount, PerReduce, PerWeightedMean))
            for node in walk(
                (node for _, node in program.outputs), resolve=dict(program.declared).get
            ).order
        ):
            continue
        if not any(
            isinstance(node, (PopulationImage, PopulationPreimage))
            for node in walk(
                (binding.definition for binding in program.populations), resolve=lambda _: None
            ).order
        ):
            continue
        if bool(program.counts) and candidate.keys:
            continue
        by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
        if candidate.stage_id not in by_id:
            continue
        kept = tuple(
            binding
            for binding in program.populations
            if binding.definition == program.output_population
        )
        redundant = tuple(binding for binding in program.populations if binding not in kept)
        if not redundant:
            continue
        if program.output_population is not None:
            if program.values or program.outputs or len(kept) != 1:
                continue
            carrier_id = kept[0].stage_id
        elif len(program.values) == 1:
            carrier_id = program.values[0].stage_id
        else:
            continue
        carrier_stage = by_id.get(carrier_id)
        if not isinstance(carrier_stage, SourceRead) or read_components(carrier_stage) is not None:
            continue
        carrier = lowered.read(carrier_id)
        owner = prepared.sources.get(
            lowered.plan.source_read(carrier.stage_id).source_id, prepared.source
        )
        if not qualified_query_provider(owner):
            continue
        removed = {binding.stage_id for binding in redundant}
        if any(
            lowered.plan.consumers_of(identifier) != (candidate.stage_id,) for identifier in removed
        ):
            continue
        sources: list[LoweredRead] = []
        compatible = True
        for identifier in removed:
            stage = by_id[identifier]
            if isinstance(stage, SourceRead):
                request = lowered.read(identifier)
                placeholders = {parameter.placeholder for parameter in stage.parameters}
                allowed = frozenset(
                    node
                    for node in request.expression.op().find(ops.UnboundTable)
                    if node.name in placeholders
                )
                if prepared.sources.get(
                    stage.source_id, prepared.source
                ) is not owner or not _physical_owner(
                    request.expression, owner, prepared, allowed_parameters=allowed
                ):
                    compatible = False
                    break
                sources.append(request)
            elif (
                not isinstance(stage, LocalCompute)
                or not isinstance(stage.program, ResultProgram)
                or not isinstance(stage.program.operation, PopulationValues)
            ):
                compatible = False
                break
        if not compatible:
            continue
        # Preserve the original source-check order rather than set iteration order.
        sources.sort(key=lambda request: tuple(by_id).index(request.stage_id))
        parameters = {
            parameter.placeholder: parameter
            for parameter in lowered.plan.source_read(carrier.stage_id).parameters
        }
        for request in sources:
            if set(lowered.plan.source_read(request.stage_id).inputs) != {
                parameter.stage_id
                for parameter in lowered.plan.source_read(request.stage_id).parameters
            }:
                compatible = False
                break
            for parameter in lowered.plan.source_read(request.stage_id).parameters:
                existing = parameters.get(parameter.placeholder)
                if existing is not None and existing != parameter:
                    compatible = False
                    break
                parameters[parameter.placeholder] = parameter
        if not compatible:
            continue
        inputs = tuple(dict.fromkeys(parameter.stage_id for parameter in parameters.values()))
        order = {identifier: index for index, identifier in enumerate(by_id)}
        if any(order[identifier] >= order[carrier_id] for identifier in inputs):
            continue
        native = NativeQueryRelation(carrier.expression, frozenset(removed), tuple(sources))
        expression, preparation, checks = _attach_relation_checks(
            carrier.expression,
            native,
            lowered.plan,
            lowered.plan.source_read(carrier.stage_id).preparation_checks,
            carrier.numeric_checks,
        )
        stage = replace(
            lowered.plan.source_read(carrier.stage_id),
            inputs=inputs,
            parameters=tuple(parameters.values()),
            preparation_checks=preparation,
            native_reads=_logical_reads(
                (
                    lowered.plan.source_read(carrier.stage_id),
                    *(lowered.plan.source_read(source.stage_id) for source in sources),
                )
            ),
        )
        updated = replace(program, populations=kept, counts=())
        compute = replace(
            candidate,
            inputs=tuple(
                identifier for identifier in candidate.inputs if identifier not in removed
            ),
            program=updated,
        )
        rewritten = {**by_id, carrier_id: stage, candidate.stage_id: compute}
        retained: set[str] = set()

        def keep(
            identifier: str,
            retained: set[str] = retained,
            rewritten: dict[str, QueryStage] = rewritten,
        ) -> None:
            if identifier not in retained:
                retained.add(identifier)
                for dependency in rewritten[identifier].inputs:
                    keep(dependency)

        keep(lowered.plan.publish)
        stages = tuple(
            rewritten[item.stage_id] for item in lowered.plan.stages if item.stage_id in retained
        )
        plan = replace(
            lowered.plan,
            stages=stages,
            consumers=_consumers(stages),
            original=updated if lowered.plan.original == program else lowered.plan.original,
            fusion_groups=tuple(
                group for group in lowered.plan.fusion_groups if group.output in retained
            ),
            optimization_decisions=(
                *lowered.plan.optimization_decisions,
                RuleDecision(
                    "source_population_check_fusion",
                    "adopted",
                    (carrier_id, *sorted(removed)),
                    "Unused relationship Population identities remain source check summaries; only the consumed output domain or reduced values are transported.",
                ),
            ),
        )
        plan = replace(plan, required_slots=required_slots(plan))
        lowered = LoweredQuery(
            plan,
            tuple(
                replace(
                    request, stage_id=stage.stage_id, expression=expression, numeric_checks=checks
                )
                if request.stage_id == carrier_id
                else request
                for request in lowered.reads
                if request.stage_id in retained
            ),
        )
    return lowered


def fuse_query_keys(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Inline qualified source-key uses while retaining independently consumed producers."""
    if any(isinstance(stage, FrameKeysInput) for stage in lowered.plan.stages):
        return replace(
            lowered,
            plan=replace(
                lowered.plan,
                optimization_decisions=(
                    *lowered.plan.optimization_decisions,
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        (),
                        "Explicit Frame selection completes before dependent source reads.",
                    ),
                ),
            ),
        )
    lowered = _fold_population_checks(prepared, lowered)
    lowered = _fuse_parameter_blocks(prepared, lowered)
    lowered = _fuse_shared_query_keys(prepared, lowered)
    decisions = list(lowered.plan.optimization_decisions)
    host_selections = _host_domain_selections(lowered.plan)
    for original in lowered.reads:
        for parameter in lowered.plan.source_read(original.stage_id).parameters:
            if original.stage_id not in {read.stage_id for read in lowered.reads}:
                break
            current = lowered.read(original.stage_id)
            owner = prepared.sources.get(
                lowered.plan.source_read(current.stage_id).source_id, prepared.source
            )
            pair = (parameter.stage_id, current.stage_id)
            if (
                sum(
                    value.stage_id == parameter.stage_id
                    for value in lowered.plan.source_read(current.stage_id).parameters
                )
                != 1
            ):
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "producer supplies multiple parameter uses in the same consumer",
                    )
                )
                continue
            candidate, pruned = _prune_transport(lowered.plan, parameter.stage_id)
            native: NativeQueryRelation | str = (
                "selected provider has no qualified source-key fusion route"
            )
            if qualified_query_provider(owner):
                native = native_query_relation(
                    prepared,
                    replace(lowered, plan=candidate),
                    parameter.stage_id,
                    owner,
                    shared=True,
                    allowed_consumers=frozenset(stage.stage_id for stage in candidate.stages),
                )
            if isinstance(native, str):
                decisions.append(RuleDecision("source_key_fusion", "refused", pair, native))
                continue
            if native.stages & host_selections:
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "The host Population domain consumes completed parent where/Top keys; this source edge retains those fixed complete keys.",
                    )
                )
                continue
            producer = next(
                stage for stage in candidate.stages if stage.stage_id == parameter.stage_id
            )
            if len(producer.keys) != len(parameter.columns):
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "complete key width differs from the planned parameter",
                    )
                )
                continue
            keys = _distinct(
                native.expression.select(
                    **dict(
                        zip(
                            (column.slot_id for column in parameter.columns),
                            (native.expression[key] for key in producer.keys),
                            strict=True,
                        )
                    )
                ),
                owner.provider.name,
            )
            expression = _replace_parameter(
                source_parameter_comparisons(
                    current.expression, parameter.placeholder, owner.provider.name
                ),
                parameter.placeholder,
                keys,
            )
            expression, preparation, checks = _attach_relation_checks(
                expression,
                native,
                lowered.plan,
                lowered.plan.source_read(current.stage_id).preparation_checks,
                current.numeric_checks,
            )
            parameters = tuple(
                value
                for value in lowered.plan.source_read(current.stage_id).parameters
                if value != parameter
            )
            stage = replace(
                lowered.plan.source_read(current.stage_id),
                inputs=tuple(
                    value
                    for value in lowered.plan.source_read(current.stage_id).inputs
                    if value != parameter.stage_id
                ),
                parameters=parameters,
                preparation_checks=tuple(preparation),
                native_reductions=(
                    *lowered.plan.source_read(current.stage_id).native_reductions,
                    *native.reductions(lowered.plan),
                ),
                native_reads=_logical_reads(
                    (
                        lowered.plan.source_read(current.stage_id),
                        *(lowered.plan.source_read(read.stage_id) for read in native.sources),
                    )
                ),
            )
            rewritten = {
                item.stage_id: stage if item.stage_id == stage.stage_id else item
                for item in candidate.stages
            }
            retained: set[str] = set()

            def keep(
                identifier: str,
                retained: set[str] = retained,
                rewritten: dict[str, QueryStage] = rewritten,
            ) -> None:
                if identifier not in retained:
                    retained.add(identifier)
                    for dependency in rewritten[identifier].inputs:
                        keep(dependency)

            keep(candidate.publish)
            removed = frozenset(rewritten) - retained
            stages = tuple(
                rewritten[item.stage_id] for item in candidate.stages if item.stage_id in retained
            )
            decisions.append(
                RuleDecision(
                    "source_key_fusion",
                    "adopted",
                    (*sorted(removed), stage.stage_id),
                    "Same SourceSession field selection stays in the null-safe membership subquery; full-scope identity/classification checks are retained.",
                )
            )
            if pruned:
                decisions.append(
                    RuleDecision(
                        "dead_population_transport",
                        "adopted",
                        pruned,
                        "Published formula consumes no output/member/sample domain and retains no downstream original state.",
                    )
                )
            plan = replace(
                candidate,
                stages=stages,
                consumers=_consumers(stages),
                fusion_groups=tuple(
                    group for group in candidate.fusion_groups if group.output not in removed
                ),
            )
            plan = replace(plan, required_slots=required_slots(plan))
            read = replace(
                current,
                stage_id=stage.stage_id,
                expression=expression,
                numeric_checks=tuple(checks),
            )
            lowered = LoweredQuery(
                plan,
                tuple(
                    read if item.stage_id == stage.stage_id else item
                    for item in lowered.reads
                    if item.stage_id not in removed
                ),
            )
    return replace(lowered, plan=replace(lowered.plan, optimization_decisions=tuple(decisions)))
