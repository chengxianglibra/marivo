"""Lower one captured predicate over prepared fields and membership markers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import ibis.expr.operations as ops
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
    result = lower_predicate(condition, fields, tuple(memberships))
    return NumericValue(
        native_predicate_equality(result.value),
        tuple(
            replace(check, violation=native_predicate_equality(check.violation))
            for check in result.checks
        ),
    )


def native_predicate_equality(predicate: ir.Value) -> ir.BooleanValue:
    """Preserve the source column's collation for MySQL business conditions."""

    def rewrite(node: ops.Node, kwargs: dict[str, object] | None) -> ops.Node:
        comparison = node.__recreate__(kwargs) if kwargs else node
        assert isinstance(comparison, ops.Node)
        if (
            isinstance(comparison, ops.Equals)
            and comparison.left.dtype.is_string()
            and comparison.right.dtype.is_string()
            and any(table.source.name == "mysql" for table in comparison.find(ops.DatabaseTable))
        ):
            # Ibis inserts a binary cast for MySQL string '='. Singleton IN
            # uses native equality and keeps SQL UNKNOWN for NULL operands.
            return ops.InValues(comparison.left, (comparison.right,))
        return comparison

    # A Field's owning relation can contain separately authored filters/joins.
    # Only lower comparisons in this predicate, never in those relations.
    value = predicate.op().replace(rewrite, filter=ops.Value).to_expr()
    assert isinstance(value, ir.BooleanValue)
    return value
