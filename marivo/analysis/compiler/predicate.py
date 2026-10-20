"""Lower one captured predicate over prepared fields and membership markers."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.types as ir

from marivo._definition.expressions import PredicateExpression
from marivo._definition.graph import computation_fingerprint
from marivo._definition.relations import PopulationContains, PopulationDependency
from marivo.analysis.compiler.errors import compilation_error
from marivo.semantic._numeric_lowering import NumericValue, lower_predicate


def population_key(population: PopulationDependency) -> str:
    """Identify the complete inert membership input in a prepared field map."""
    return "population:" + computation_fingerprint(population)


def lower_condition(condition: PredicateExpression, fields: Mapping[str, ir.Value]) -> NumericValue:
    memberships: list[ir.BooleanValue] = []
    for leaf in condition.inputs:
        if isinstance(leaf, PopulationContains):
            marker = fields[population_key(leaf.population)]
            if not isinstance(marker, ir.BooleanValue):
                raise compilation_error("a boolean membership marker", str(marker.type()))
            memberships.append(marker)
    return lower_predicate(condition, fields, tuple(memberships))
