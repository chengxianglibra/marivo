"""Private relational scalar projections for engines without native struct columns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.materialization.errors import MaterializationError


def _name(name: str, field: str) -> str:
    return "__mv_identity_" + sha256(repr((name, field)).encode()).hexdigest()[:24]


def _leaves(value: ops.Value) -> tuple[ops.Value, ...]:
    return value.values if isinstance(value, ops.StructColumn) else (value,)


def _values(values: Mapping[str, ops.Value], *, run_ref: str | None) -> dict[str, ops.Value]:
    result: dict[str, ops.Value] = {}
    for name, value in values.items():
        if isinstance(value, ops.StructColumn):
            for field, child in zip(value.names, value.values, strict=True):
                physical = _name(name, field)
                if physical in values or physical in result or child.dtype.is_struct():
                    raise MaterializationError(
                        expected="flat identity fields with distinct physical column names",
                        received="nested or colliding scalar identity projection",
                        repair="Use scalar identity fields without generated-column name collisions.",
                        stage="compilation",
                        run_ref=run_ref,
                    )
                result[physical] = child
        else:
            result[name] = value
    return result


@dataclass(frozen=True)
class ScalarProjection:
    expression: ir.Table
    schema: pa.Schema
    columns: tuple[tuple[str, ...], ...]


def project(expression: ir.Expr, *, run_ref: str | None = None) -> ScalarProjection:
    """Flatten intermediate identities and retain an exact typed output reconstruction."""
    table = expression.as_table()

    def rewrite(
        node: ops.Node, _results: dict[ops.Node, ops.Node] | None = None, **kwargs: object
    ) -> ops.Node:
        if isinstance(node, ops.Field) and node.dtype.is_struct():
            relation = kwargs["rel"]
            assert isinstance(relation, ops.Relation)
            return ops.StructColumn(
                node.dtype.names,
                tuple(ops.Field(relation, _name(node.name, field)) for field in node.dtype.names),
            )
        if isinstance(node, ops.StructField):
            arg = kwargs["arg"]
            if isinstance(arg, ops.StructColumn):
                return arg.values[arg.names.index(node.field)]
        if isinstance(node, (ops.IsNull, ops.NotNull)):
            arg = kwargs["arg"]
            if isinstance(arg, ops.StructColumn):
                terms = [type(node)(value) for value in arg.values]
                result = terms[0]
                for term in terms[1:]:
                    result = (ops.Or if isinstance(node, ops.IsNull) else ops.And)(result, term)
                return result
        if isinstance(node, (ops.Project, ops.JoinChain)):
            values = kwargs["values"]
            assert isinstance(values, Mapping)
            kwargs["values"] = _values(values, run_ref=run_ref)
        if isinstance(node, ops.DropColumns):
            kwargs["columns_to_drop"] = frozenset(
                _name(name, field) if node.parent.schema[name].is_struct() else name
                for name in node.columns_to_drop
                for field in (
                    node.parent.schema[name].names
                    if node.parent.schema[name].is_struct()
                    else ("",)
                )
            )
        if isinstance(node, ops.Aggregate):
            groups = kwargs["groups"]
            assert isinstance(groups, Mapping)
            kwargs["groups"] = _values(groups, run_ref=run_ref)
        if isinstance(node, (ops.Sort, ops.WindowFunction)):
            key = "keys" if isinstance(node, ops.Sort) else "order_by"
            keys = kwargs[key]
            assert isinstance(keys, tuple)
            kwargs[key] = tuple(
                ops.SortKey(leaf, ascending=term.ascending, nulls_first=term.nulls_first)
                for term in keys
                for leaf in _leaves(term.arg)
            )
            if isinstance(node, ops.WindowFunction):
                groups = kwargs["group_by"]
                assert isinstance(groups, tuple)
                kwargs["group_by"] = tuple(leaf for group in groups for leaf in _leaves(group))
        if isinstance(node, (ops.Equals, ops.IdenticalTo, ops.NotEquals)):
            left, right = kwargs["left"], kwargs["right"]
            if isinstance(left, ops.StructColumn) and isinstance(right, ops.StructColumn):
                terms = [type(node)(a, b) for a, b in zip(left.values, right.values, strict=True)]
                result = terms[0]
                for term in terms[1:]:
                    result = (ops.Or if isinstance(node, ops.NotEquals) else ops.And)(result, term)
                return result
        return node.copy(**kwargs)

    rewritten = table.op().map(rewrite)[table.op()]
    physical = rewritten.to_expr()
    assert isinstance(physical, ir.Table)
    columns = tuple(
        tuple(_name(name, field) for field in dtype.names) if dtype.is_struct() else (name,)
        for name, dtype in table.schema().items()
    )
    return ScalarProjection(physical, table.schema().to_pyarrow(), columns)
