"""Prepare non-null complete-tuple membership markers without filtering facts."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.observations import ContributionBinding
from marivo._definition.relations import PopulationDependency, condition_populations
from marivo._definition.types import AxisBinding, coordinate_read_keys
from marivo.analysis.compiler.predicate import population_key
from marivo.analysis.materialization.population_preparation import _domain_table, prepare_population
from marivo.semantic._compiled_state import CompiledSemanticState


def prepare_condition_memberships(
    table: ir.Table,
    contribution: ContributionBinding,
    columns: Mapping[str, str],
    entities: Mapping[str, ir.Table],
    state: CompiledSemanticState,
) -> tuple[ir.Table, tuple[tuple[str, str], ...]]:
    """Classify every fact once while retaining the condition's original input domain."""
    reads = dict(contribution.population_reads)
    marks: dict[PopulationDependency, str] = {}
    conditions = (
        *contribution.intrinsic_conditions,
        *((contribution.common_condition,) if contribution.common_condition is not None else ()),
    )
    for condition in conditions:
        for domain in condition_populations(condition):
            if domain in marks:
                continue
            selected = prepare_population(domain, entities, state)
            index = len(marks)
            marker = f"__predicate_present_{index}"
            while marker in table.columns:
                marker += "_"
            renamed = {
                key: f"__predicate_{index}_{i}" for i, key in enumerate(selected.key_columns)
            }
            allowed = _domain_table(selected)
            allowed = (
                (
                    allowed.select(**{new: allowed[old] for old, new in renamed.items()})
                    if renamed
                    else allowed
                )
                .distinct()
                .mutate(**{marker: ibis.literal(True)})
            )
            predicates: list[ir.BooleanValue] = []
            for axis, group in zip(domain.axes, selected.key_groups, strict=True):
                assert isinstance(axis, AxisBinding)
                for left, right in zip(coordinate_read_keys(reads[axis]), group, strict=True):
                    predicates.append(table[columns[left]].identical_to(allowed[renamed[right]]))
            table = table.left_join(allowed, predicates or ibis.literal(True)).select(
                table, allowed[marker]
            )
            table = table.mutate(**{marker: table[marker].fill_null(False)})
            marks[domain] = marker
    return table, tuple((population_key(domain), marker) for domain, marker in marks.items())
