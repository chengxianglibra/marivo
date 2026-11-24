"""Recognize complete ordinary-key group and Population inputs in the native graph."""

from __future__ import annotations

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.query_stages import SourceReadStage


def stored_key_field(value: ops.Value) -> ops.Field | None:
    """Prove a grouping key is a physical column reached through direct aliases."""
    while isinstance(value, ops.Field):
        if isinstance(value.rel, ops.DatabaseTable):
            return value
        if isinstance(value.rel, (ops.Reference, ops.Filter, ops.Distinct)):
            value = ops.Field(value.rel.parent, value.name)
        elif value.name in value.rel.values:
            value = value.rel.values[value.name]
        else:
            return None
    return None


def _stored_integer_predicate(value: ops.Value) -> bool:
    if isinstance(value, ops.And):
        return _stored_integer_predicate(value.left) and _stored_integer_predicate(value.right)
    if not isinstance(
        value, (ops.Equals, ops.NotEquals, ops.Less, ops.LessEqual, ops.Greater, ops.GreaterEqual)
    ):
        return False
    field, literal = (
        (value.right, value.left)
        if isinstance(value.left, ops.Literal)
        else (value.left, value.right)
    )
    return (
        isinstance(literal, ops.Literal)
        and literal.dtype.is_signed_integer()
        and (stored := stored_key_field(field)) is not None
        and stored.dtype.is_signed_integer()
    )


def stored_group_input(aggregate: ops.Aggregate) -> bool:
    """Admit only direct reads without unrelated fallible row demand."""
    for relation in aggregate.parent.find(ops.Relation):
        if isinstance(relation, (ops.Reference, ops.DatabaseTable)):
            continue
        if isinstance(relation, ops.Filter) and all(
            _stored_integer_predicate(term) for term in relation.predicates
        ):
            continue
        if not isinstance(relation, ops.Project) or any(
            not isinstance(value, (ops.Field, ops.Literal)) for value in relation.values.values()
        ):
            return False
    return True


def supported_reduction(
    relation: ops.Relation, stage: SourceReadStage
) -> tuple[ops.Aggregate, ops.Relation] | None:
    """Recognize a direct reduction with an independent total presence marker."""
    if isinstance(relation, ops.Aggregate):
        return relation, relation.parent
    if not (
        isinstance(relation, ops.JoinChain)
        and isinstance(support := relation.first.parent, ops.Aggregate)
        and len(relation.rest) == 1
    ):
        return None
    (link,) = relation.rest
    if not (
        link.how == "left"
        and isinstance(values := link.table.parent, ops.Project)
        and isinstance(reduction := values.parent, ops.Aggregate)
        and isinstance(filtered := reduction.parent, ops.Filter)
        and filtered.parent == support.parent
        and len(filtered.predicates) == 1
        and _stored_integer_predicate(filtered.predicates[0])
        and tuple(support.groups) == tuple(reduction.groups) == stage.keys
    ):
        return None
    for slot in stage.keys:
        inner = reduction.groups[slot]
        if not (
            isinstance(inner, ops.Field)
            and inner.rel == filtered
            and support.groups[slot] == ops.Field(support.parent, inner.name)
        ):
            return None
    expected_join = tuple(
        ops.IdenticalTo(ops.Field(relation.first, slot), ops.Field(link.table, slot))
        for slot in stage.keys
    )
    marker = support.metrics.get("__sample_present")
    if not (
        link.predicates == expected_join
        and len(support.metrics) == 1
        and isinstance(marker, ops.Coalesce)
        and len(marker.arg) == 2
        and isinstance(presence := marker.arg[0], ops.Any)
        and presence.where is None
        and presence.arg == filtered.predicates[0]
        and isinstance(empty := marker.arg[1], ops.Literal)
        and empty.value is False
        and all(isinstance(value, (ops.Field, ops.Literal)) for value in values.values.values())
        and all(isinstance(value, ops.Field) for value in relation.values.values())
    ):
        return None
    return reduction, support.parent


def stored_population_read(expression: ir.Table, leaf: ops.DatabaseTable) -> bool:
    """Recognize exact same-table projections with total native predicates."""
    if tuple(expression.op().find(ops.DatabaseTable)) != (leaf,):
        return False
    for relation in expression.op().find(ops.Relation):
        if isinstance(relation, (ops.Reference, ops.DatabaseTable, ops.Distinct)):
            continue
        if isinstance(relation, ops.Filter) and all(
            _stored_integer_predicate(term) for term in relation.predicates
        ):
            continue
        if not isinstance(relation, ops.Project) or any(
            not isinstance(value, (ops.Field, ops.Literal))
            and not (isinstance(value, ops.IsNull) and stored_key_field(value.arg) is not None)
            for value in relation.values.values()
        ):
            return False
    return True
