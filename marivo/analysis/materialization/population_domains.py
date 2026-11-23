"""Prepared Population domains and the checks carried across operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

import ibis
import ibis.expr.types as ir

from marivo._definition.relations import PopulationDependency
from marivo.analysis.compiler.lowered_query import PopulationCheck
from marivo.analysis.model.unique_keys import UniqueKeys


@dataclass(frozen=True, slots=True)
class PreparedPopulation:
    table: ir.Table
    key_groups: tuple[tuple[str, ...], ...]
    checks: tuple[PopulationCheck, ...] = ()
    presence: str | None = None
    unique_keys: UniqueKeys = field(default_factory=UniqueKeys)
    member_source: ir.Table | None = None

    @property
    def key_columns(self) -> tuple[str, ...]:
        return tuple(column for group in self.key_groups for column in group)


def _domain_table(prepared: PreparedPopulation) -> ir.Table:
    """Select the key set, retaining the unit tuple for a nonempty zero-axis domain."""
    table = prepared.table
    if prepared.presence is not None:
        table = table.filter(table[prepared.presence].fill_null(False))
    if prepared.key_columns:
        return table.select(*prepared.key_columns)
    return table.select(__population_unit=ibis.literal(True)).distinct()


def _retain_checks(result: PreparedPopulation, *inputs: PreparedPopulation) -> PreparedPopulation:
    """Carry required input checks even when a set operation removes all keys."""
    checks = dict.fromkeys((*result.checks, *(check for item in inputs for check in item.checks)))
    return replace(result, checks=tuple(checks))


def scoped_checks(
    table: ir.Table,
    flags: tuple[tuple[str, str, str], ...],
    keys: tuple[str, ...],
) -> tuple[PopulationCheck, ...]:
    return tuple(
        PopulationCheck(table, name, expected, received, keys) for name, expected, received in flags
    )


PrepareDomain = Callable[[PopulationDependency], PreparedPopulation]
