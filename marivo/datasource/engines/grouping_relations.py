"""Typed native aggregation of a complete key and its parent key."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import sqlglot.expressions as sge
from ibis.common.collections import FrozenOrderedDict
from ibis.expr.schema import Schema
from typing_extensions import override

from marivo.datasource.engines._ibis_typing import RelationalOperation


class GroupingSetsAggregate(RelationalOperation):
    parent: ops.Relation
    groups: FrozenOrderedDict[str, ops.Value]
    metrics: FrozenOrderedDict[str, ops.Scalar]
    values = FrozenOrderedDict()
    parent_keys: tuple[str, ...]
    kind: str

    if TYPE_CHECKING:

        def __init__(
            self,
            *,
            parent: ops.Relation,
            groups: FrozenOrderedDict[str, ops.Value],
            metrics: FrozenOrderedDict[str, ops.Scalar],
            parent_keys: tuple[str, ...],
            kind: str,
        ) -> None: ...

    @property
    @override
    def schema(self) -> Schema:
        return Schema(
            {
                **{
                    name: value.dtype.copy(nullable=True)
                    if name in self.groups and name not in self.parent_keys
                    else value.dtype
                    for name, value in (*self.groups.items(), *self.metrics.items())
                },
                self.kind: dt.int8,
            }
        )


def grouping_sets_aggregate(
    source: ir.Table,
    groups: Mapping[str, ir.Value],
    metrics: Mapping[str, ir.Value],
    parent_keys: tuple[str, ...],
    kind: str,
) -> ir.Table:
    """Compute fine (kind 0) and parent (kind 1) states over the same rows."""
    if not set(parent_keys) < set(groups) or kind in (*groups, *metrics):
        raise ValueError("Grouping sets require a proper parent key and a distinct role column")
    aggregate = source.group_by(**groups).aggregate(**metrics).op()
    assert isinstance(aggregate, ops.Aggregate)
    return GroupingSetsAggregate(
        parent=aggregate.parent,
        groups=aggregate.groups,
        metrics=aggregate.metrics,
        parent_keys=parent_keys,
        kind=kind,
    ).to_expr()


def compile_grouping_sets(
    op: GroupingSetsAggregate,
    parent: sge.Expr,
    groups: Mapping[str, sge.Expr],
    metrics: Mapping[str, sge.Expr],
) -> sge.Select:
    mask = sge.Grouping(expressions=[value.copy() for value in groups.values()])
    role = sge.If(
        this=sge.EQ(this=mask, expression=sge.Literal.number(0)),
        true=sge.Literal.number(0),
        false=sge.Literal.number(1),
    )
    return (
        sge.select(
            *(
                sge.alias_(value, name, quoted=True)
                for name, value in (*groups.items(), *metrics.items(), (op.kind, role))
            )
        )
        .from_(parent)
        .group_by(
            sge.GroupingSets(
                expressions=[
                    sge.Tuple(expressions=[value.copy() for value in groups.values()]),
                    sge.Tuple(expressions=[groups[name].copy() for name in op.parent_keys]),
                ]
            )
        )
    )
