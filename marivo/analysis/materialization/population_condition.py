"""Apply closed Population membership to source contributions without fanout."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.observations import ContributionBinding
from marivo._definition.relations import (
    PopulationAnd,
    PopulationCondition,
    PopulationContains,
    PopulationDependency,
    PopulationNot,
)
from marivo._definition.types import AxisBinding, coordinate_read_keys
from marivo.analysis.dsl.population import condition_populations
from marivo.analysis.materialization.population_preparation import prepare_population
from marivo.semantic._compiled_state import CompiledSemanticState


def filter_contribution(
    table: ir.Table,
    contribution: ContributionBinding,
    columns: Mapping[str, str],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
) -> ir.Table:
    """Classify each fact once, then test membership in the captured key sets."""
    original = tuple(table.columns)
    reads = dict(contribution.population_reads)
    marks: dict[PopulationDependency, str] = {}
    for condition in contribution.population_conditions:
        for domain in condition_populations(condition):
            if domain in marks:
                continue
            selected = prepare_population(domain, entities, state)
            index = len(marks)
            marker = f"__predicate_present_{index}"
            renamed = {
                key: f"__predicate_{index}_{i}" for i, key in enumerate(selected.key_columns)
            }
            allowed = (
                selected.table.select(**{new: selected.table[old] for old, new in renamed.items()})
                .distinct()
                .mutate(**{marker: ibis.literal(True)})
            )
            predicates: list[ir.BooleanValue] = []
            for axis, group in zip(domain.axes, selected.key_groups, strict=True):
                assert isinstance(axis, AxisBinding)
                for left, right in zip(coordinate_read_keys(reads[axis]), group, strict=True):
                    predicates.append(table[columns[left]].identical_to(allowed[renamed[right]]))
            table = table.left_join(allowed, predicates).select(table, allowed[marker])
            marks[domain] = marker

    def value(condition: PopulationCondition) -> ir.BooleanValue:
        if isinstance(condition, PopulationContains):
            result = table[marks[condition.population]].fill_null(False)
            assert isinstance(result, ir.BooleanValue)
            return result
        if isinstance(condition, PopulationNot):
            return ~value(condition.operand)
        if isinstance(condition, PopulationAnd):
            return value(condition.left) & value(condition.right)
        return value(condition.left) | value(condition.right)

    return table.filter(
        *(value(condition) for condition in contribution.population_conditions)
    ).select(*original)
