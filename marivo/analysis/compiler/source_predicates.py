"""Source string identity and Query numeric missing-value predicates."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.datasource.spi import query_dialect, query_policy


def string_bytes(value: ir.Value, provider: str) -> ir.Value:
    return query_dialect(provider).string_bytes(value)


def string_without_collation(value: ir.Value, provider: str) -> ir.Value:
    """Remove inherited collations when a dialect needs returned byte identity."""
    return query_dialect(provider).string_without_collation(value)


def parameter_string_comparisons(expression: ir.Table, name: str, provider: str) -> ir.Table:
    """Match qualified membership keys using their exact returned string bytes."""
    if query_policy(provider).key_fusion == "none":
        return expression

    def rewrite(node: ops.Node, kwargs: dict[str, object] | None) -> ops.Node:
        # Rebuild operands first: a nested membership may already have changed
        # their relation parents, including JoinReferences in an enclosing join.
        comparison = node.__recreate__(kwargs) if kwargs else node
        assert isinstance(comparison, ops.Node)
        if (
            isinstance(comparison, ops.IdenticalTo)
            and comparison.left.dtype.is_string()
            and comparison.right.dtype.is_string()
            and any(relation.name == name for relation in comparison.find(ops.UnboundTable))
        ):
            return (
                string_bytes(comparison.left.to_expr(), provider)
                .identical_to(string_bytes(comparison.right.to_expr(), provider))
                .op()
            )
        return comparison

    result = expression.op().replace(rewrite).to_expr()
    assert isinstance(result, ir.Table)
    return result


def source_parameter_comparisons(expression: ir.Table, name: str, provider: str) -> ir.Table:
    """Retain native membership equality, including native Metric group collations."""
    return (
        parameter_string_comparisons(expression, name, provider)
        if query_dialect(provider).source_membership_uses_returned_strings
        else expression
    )


def same_returned_key(left: ir.Value, right: ir.Value, provider: str) -> ir.BooleanValue:
    """Match complete returned keys independently of source string collations."""
    if left.type().is_string() and right.type().is_string():
        left, right = string_bytes(left, provider), string_bytes(right, provider)
    result = left.identical_to(right)
    assert isinstance(result, ir.BooleanValue)
    return result


def aggregate_returned_keys(
    table: ir.Table,
    keys: tuple[str, ...],
    metrics: Mapping[str, ir.Value],
    provider: str,
) -> ir.Table:
    """Aggregate completed values using the exact returned coordinate identities."""
    groups: dict[str, ir.Value] = {}
    aggregates = dict(metrics)
    for index, name in enumerate(keys):
        value = table[name]
        if value.type().is_string():
            groups[f"__returned_key_{index}"] = string_bytes(value, provider)
            aggregates[name] = value.min()
        else:
            groups[name] = value
    result = table.aggregate(**aggregates, by=groups)
    return result.select(*keys, *metrics)


def distinct_returned_keys(table: ir.Table, provider: str) -> ir.Table:
    """Deduplicate returned tuples without inherited or session string collations."""
    if not any(table[name].type().is_string() for name in table.columns):
        return table.distinct()
    return aggregate_returned_keys(table, tuple(table.columns), {}, provider)
