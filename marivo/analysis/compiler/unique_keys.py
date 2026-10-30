"""Bounded exact-key transfer for the relation constructors used by the compiler."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache

import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.core.unique_keys import UniqueKeys


def relation_unique_keys(
    table: ir.Table, seeds: Mapping[ops.Relation, UniqueKeys] | None = None
) -> UniqueKeys:
    """Infer only exact projections, grouping, sets and direct equijoin bindings.

    Unknown expressions and predicates never establish facts. Seeds belong to
    exact relation nodes, so unrelated tables cannot share facts through names.
    """
    known = seeds or {}

    @cache
    def infer(node: ops.Relation) -> UniqueKeys:
        slots = tuple(node.schema.names)
        if node in known:
            return known[node].project(slots)
        if isinstance(node, ops.Aggregate):
            return UniqueKeys(slots, (tuple(node.groups),))
        if isinstance(node, ops.Project):
            mapping = {
                value.name: name
                for name, value in node.values.items()
                if isinstance(value, ops.Field) and value.rel == node.parent
            }
            retained = tuple(mapping)
            return infer(node.parent).project(retained).project(slots, mapping)
        if isinstance(node, (ops.Filter, ops.Sort, ops.DropNull)):
            return infer(node.parent).project(slots)
        if isinstance(node, ops.Limit):
            result = infer(node.parent).project(slots)
            return result.add(()) if isinstance(node.n, int) and node.n <= 1 else result
        if isinstance(node, ops.Distinct):
            return infer(node.parent).project(slots).add(slots)
        if isinstance(node, (ops.Union, ops.Intersection, ops.Difference)):
            result = (
                UniqueKeys(slots)
                if isinstance(node, ops.Union)
                else infer(node.left).project(slots)
            )
            return result.add(slots) if node.distinct else result
        if isinstance(node, ops.JoinReference):
            return infer(node.parent).project(slots)
        if isinstance(node, ops.JoinChain) and len(node.rest) == 1:
            link = node.rest[0]
            left, right = infer(node.first), infer(link.table)
            left_bound: list[str] = []
            right_bound: list[str] = []

            def equality(predicate: ops.Value) -> None:
                if isinstance(predicate, ops.And):
                    equality(predicate.left)
                    equality(predicate.right)
                elif isinstance(predicate, (ops.Equals, ops.IdenticalTo)):
                    lhs, rhs = predicate.left, predicate.right
                    if not isinstance(lhs, ops.Field) or not isinstance(rhs, ops.Field):
                        return
                    if lhs.dtype.copy(nullable=True) != rhs.dtype.copy(nullable=True):
                        return
                    # String dtype does not disclose backend collation. A
                    # case-insensitive equality can match several exact keys;
                    # only already-normalized non-string carriers qualify.
                    if lhs.dtype.is_string() or rhs.dtype.is_string():
                        return
                    if lhs.rel == link.table and rhs.rel == node.first:
                        lhs, rhs = rhs, lhs
                    if lhs.rel == node.first and rhs.rel == link.table:
                        left_bound.append(lhs.name)
                        right_bound.append(rhs.name)

            for predicate in link.predicates:
                equality(predicate)
            left_mapping = {
                value.name: name
                for name, value in node.values.items()
                if isinstance(value, ops.Field) and value.rel == node.first
            }
            right_mapping = {
                value.name: name
                for name, value in node.values.items()
                if isinstance(value, ops.Field) and value.rel == link.table
            }
            lhs = left.project(tuple(left_mapping)).project(slots, left_mapping)
            rhs = right.project(tuple(right_mapping)).project(slots, right_mapping)
            candidates: tuple[tuple[str, ...], ...] = ()
            if link.how in ("semi", "anti") or (
                link.how in ("inner", "left") and right.proves(tuple(right_bound))
            ):
                candidates = lhs.keys
            if link.how == "inner" and left.proves(tuple(left_bound)):
                candidates = (*candidates, *rhs.keys)
            if link.how == "cross":
                candidates = tuple((*a, *b) for a in lhs.keys for b in rhs.keys)
            return UniqueKeys(slots, candidates)
        return UniqueKeys(slots)

    return infer(table.op())
