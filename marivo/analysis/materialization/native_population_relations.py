"""Lower complete Population and count relations within native Query fusion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.correspondence import coordinate_position
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    DeclaredMetricRef,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
)
from marivo._definition.graph import children, computation_fingerprint, walk
from marivo._definition.query import (
    PopulationValues,
)
from marivo._definition.relations import (
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
)
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.source_predicates import string_bytes as _string_bytes
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
)
from marivo.analysis.materialization.query_key_values import _distinct, _same_key
from marivo.analysis.model.data_types import (
    DataColumn,
    ScalarDataType,
)
from marivo.analysis.model.programs import FormulaInput
from marivo.analysis.model.unique_keys import UniqueKeys


@dataclass(frozen=True, slots=True)
class _NativePopulation:
    expression: ir.Table
    groups: tuple[tuple[str, ...], ...]


def _project(
    value: _NativePopulation, positions: tuple[int, ...], provider: str
) -> _NativePopulation | str:
    selected = tuple(key for position in positions for key in value.groups[position])
    if not selected:
        return _NativePopulation(
            value.expression.select(__population_unit=ibis.literal(True)).distinct(), ()
        )
    keys = tuple(f"key.{index}" for index in range(len(selected)))
    expression = value.expression.select(
        **dict(zip(keys, (value.expression[key] for key in selected), strict=True))
    )
    if len(positions) != len(value.groups):
        expression = _distinct(expression, provider)
    groups: list[tuple[str, ...]] = []
    offset = 0
    for position in positions:
        width = len(value.groups[position])
        groups.append(keys[offset : offset + width])
        offset += width
    return _NativePopulation(expression, tuple(groups))


class _NativePopulationLowerer:
    def __init__(self, available: Mapping[str, _NativePopulation], provider: str) -> None:
        self.available = available
        self.provider = provider

    def matches(
        self,
        condition: PredicateExpression,
        operand: PopulationDependency,
        value: _NativePopulation,
    ) -> _NativePopulation | str:
        if any(not isinstance(leaf, PopulationContains) for leaf in condition.inputs):
            return "Field predicates retain the checked coordinate kernel."
        table = value.expression.view()
        original = tuple(table.columns)
        values: dict[str, object] = {}
        for index, leaf in enumerate(condition.inputs):
            assert isinstance(leaf, PopulationContains)
            other = self.rows(leaf.population)
            if isinstance(other, str):
                return other
            marker = f"__population_match_{index}"
            renamed = {
                name: f"__population_match_{index}_{position}"
                for position, name in enumerate(other.expression.columns)
            }
            right = other.expression.select(
                **{name: other.expression[old] for old, name in renamed.items()}
            ).mutate(**{marker: ibis.literal(True)})
            predicates = tuple(
                _same_key(table[a], right[renamed[b]], self.provider)
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

    def rows(self, domain: PopulationDependency) -> _NativePopulation | str:
        found = self.available.get(computation_fingerprint(domain))
        if found is not None:
            return _project(found, tuple(range(len(found.groups))), self.provider)
        if not isinstance(domain, PopulationProgram):
            return "Population relation requires a planned checked atom"
        node = domain.root
        if isinstance(node, PopulationProject):
            if node.interval_keys:
                return "Interval key remapping retains the checked coordinate kernel."
            value = self.rows(node.operand)
            return (
                value if isinstance(value, str) else _project(value, node.positions, self.provider)
            )
        if isinstance(node, PopulationSet):
            left, right = self.rows(node.left), self.rows(node.right)
            if isinstance(left, str):
                return left
            if isinstance(right, str):
                return right
            if node.operation == "union":
                return _NativePopulation(
                    _distinct(
                        left.expression.union(right.expression, distinct=False), self.provider
                    ),
                    left.groups,
                )
            lhs, rhs = left.expression.view(), right.expression.view()
            conditions = [_same_key(lhs[key], rhs[key], self.provider) for key in lhs.columns]
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
                value = self.rows(factor)
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
            # Complete factor tuples remain unique in their Cartesian product.
            return _NativePopulation(combined, tuple(groups))
        if isinstance(node, PopulationFilter):
            value = self.rows(node.operand)
            return (
                value
                if isinstance(value, str)
                else self.matches(node.predicate, node.operand, value)
            )
        return "Population source lookup retains its prepared attribute or relationship route"


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

    result = _NativePopulationLowerer(available, provider).rows(operation.definition)
    if isinstance(result, str):
        return result
    source_keys = tuple(key for group in result.groups for key in group)
    output_keys = tuple(key for group in program.output_groups for key in group)
    return (
        result.expression.select(
            **dict(zip(output_keys, (result.expression[key] for key in source_keys), strict=True))
        )
        if output_keys
        else result.expression.select(__population_unit=ibis.literal(True)).distinct()
    )


def _exact_pop_distinct(table: ir.Table, provider: str) -> ir.Table:
    def exact_distinct(
        node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object
    ) -> ops.Node:
        rewritten = node.__recreate__(kwargs) if kwargs else node
        if isinstance(rewritten, ops.Distinct):
            parent = rewritten.parent.to_expr()
            assert isinstance(parent, ir.Table)
            return _distinct(parent, provider).op()
        return rewritten

    exact = table.op().map(exact_distinct)[table.op()].to_expr()
    assert isinstance(exact, ir.Table)
    return exact


def _native_member_counts(
    program: FormulaProgram,
    relations: dict[str, ir.Table],
    keys: tuple[str, ...],
    providers: Mapping[str, str],
    unique_keys: Mapping[str, UniqueKeys],
) -> tuple[FormulaProgram, dict[str, ir.Table]] | str:
    """Complete distinct member tuples before composing ordinary count formulas."""
    if (
        program.calendar is not None
        or program.output_population is not None
        or program.parent_scope
    ):
        return "member count retains a calendar, explicit output or parent domain"
    closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
    if any(isinstance(node, (PerReduce, PerWeightedMean)) for node in closure.order):
        return "member count within samples retains its independent sample domain"
    axes = tuple(axis for axis in program.axes if not isinstance(axis, BuiltinSampleCoordinate))
    if len(axes) != len(program.axes):
        return "member count broadcasts only across represented internal calendar samples"
    values = list(program.values)
    declarations = dict(program.declared)

    def include(node: MetricNode, occurrence: tuple[int, ...]) -> str | None:
        if isinstance(node, MemberCount):
            count = next(item for item in program.counts if item.definition == node.population)
            binding = next(
                (item for item in program.populations if item.definition == count.population), None
            )
            if binding is None or binding.stage_id not in relations:
                return "member count has no exact prepared Population relation"
            table = relations[binding.stage_id]
            provider = providers[binding.stage_id]

            table = _exact_pop_distinct(table, provider)
            mapping = dict(zip(count.keys, binding.keys, strict=True))
            selected = tuple(
                mapping[name]
                for axis in axes
                for name in count.axis_mapping[coordinate_position(count.population.axes, axis)][1]
            )
            if len(selected) != len(keys):
                return "member count output does not match its complete coordinates"
            table = (
                table.select(*binding.keys)
                if binding.keys
                else table.select(__population_unit=ibis.literal(True))
            )
            if not unique_keys.get(binding.stage_id, UniqueKeys()).proves(binding.keys):
                table = _distinct(table, provider)
            slot = "__member_count_" + "_".join(str(index) for index in occurrence)
            identifier = binding.stage_id + ":" + slot
            grouped = {
                f"__member_group_{index}" if table[name].type().is_string() else key: _string_bytes(
                    table[name], provider
                )
                if table[name].type().is_string()
                else table[name]
                for index, (key, name) in enumerate(zip(keys, selected, strict=True))
            }
            relations[identifier] = table.aggregate(
                **{slot: table.count()},
                **{
                    key: table[name].min()
                    for key, name in zip(keys, selected, strict=True)
                    if table[name].type().is_string()
                },
                by=grouped,
            ).select(*keys, slot)
            values.append(
                FormulaInput(
                    occurrence,
                    identifier,
                    DataColumn(slot, slot, ScalarDataType("int64"), False),
                    True,
                    keys=keys,
                )
            )
            return None
        if isinstance(node, DeclaredMetricRef):
            return include(declarations[node.ref], (*occurrence, 0))
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                issue = include(edge.child, (*occurrence, edge.index))
                if issue is not None:
                    return issue
        return None

    for index, (_, node) in enumerate(program.outputs):
        issue = include(node, (index,))
        if issue is not None:
            return issue
    return replace(program, values=tuple(values), counts=(), populations=()), relations
