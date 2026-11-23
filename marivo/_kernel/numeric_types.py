"""Pure Ibis numeric literal binding and bounded Decimal result metadata."""

from __future__ import annotations

from decimal import Decimal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir


def exact_unsigned(value: ir.Value) -> ir.Value:
    """Represent the complete UInt64 domain without signed overflow or floating loss."""
    if value.type().is_unsigned_integer() and value.type().nbytes == 8:
        return value.cast(dt.Decimal(20, 0))
    return value


def _projected_unsigned(project: ops.Project, name: str) -> ops.Value | None:
    definition = project.values[name]
    # Observation can alias the Decimal value in a second projection. Reuse
    # the original UInt64 only if that projection also retained the raw input.
    if isinstance(definition, ops.Field) and isinstance(definition.rel, ops.Project):
        origin = definition.rel.values[definition.name]
        if isinstance(origin, ops.Cast) and isinstance(origin.arg, ops.Field):
            for field_name, value in definition.rel.values.items():
                if value == origin.arg:
                    definition = origin.copy(arg=ops.Field(definition.rel, field_name))
                    break
    if (
        isinstance(definition, ops.Cast)
        and definition.to == dt.Decimal(20, 0, nullable=definition.to.nullable)
        and definition.arg.dtype.is_uint64()
    ):
        return definition.arg
    return None


def _unsigned_aggregate(aggregate: ops.Aggregate) -> ops.Aggregate:
    # Extend only this aggregate's input branch. The original parent may also
    # feed DISTINCT, UNION or another consumer that needs its exact schema.
    project = aggregate.parent
    branch: set[ops.Relation] = {aggregate, project}
    while isinstance(project, ops.Filter | ops.Sort | ops.Limit):
        project = project.parent
        branch.add(project)
    if not isinstance(project, ops.Project):
        return aggregate

    extra: dict[str, ops.Value] = {}
    consumers: dict[ops.Node, str] = {}
    eligible = (ops.Cast, ops.Count, ops.CountDistinct, ops.Min, ops.Max)
    for metric in aggregate.metrics.values():
        for node in metric.find(eligible, filter=ops.Value):
            assert isinstance(node, eligible)
            if isinstance(node, ops.Cast) and not node.to.is_float64():
                continue
            if not isinstance(node.arg, ops.Field) or node.arg.rel != aggregate.parent:
                continue
            name = node.arg.name
            raw = _projected_unsigned(project, name)
            column = f"{name}:uint64"
            if raw is not None and column not in project.values:
                extra[column] = raw
                consumers[node] = column
    if not extra:
        return aggregate

    def in_branch(node: ops.Node) -> bool:
        return isinstance(node, ops.Value) or node in branch

    if aggregate.find(ops.Subquery, filter=in_branch):
        return aggregate

    def rewrite(node: ops.Node, kwargs: dict[str, object] | None) -> ops.Node:
        if node == project:
            return project.copy(values={**project.values, **extra})
        updated = node.copy(**kwargs) if kwargs else node
        if node in consumers:
            assert isinstance(updated, eligible) and isinstance(updated.arg, ops.Field)
            native = updated.copy(arg=ops.Field(updated.arg.rel, consumers[node]))
            # Extrema commute with the exact UInt64-to-Decimal conversion.
            if isinstance(updated, (ops.Min, ops.Max)):
                return ops.Cast(native, to=updated.dtype)
            return native
        return updated

    updated = aggregate.replace(rewrite, filter=in_branch)
    assert isinstance(updated, ops.Aggregate)
    return updated


def native_unsigned_aggregates(expression: ir.Table) -> ir.Table:
    """Use exact UInt64 inputs for aggregates that do not require Decimal arithmetic."""

    def rewrite(node: ops.Node, kwargs: dict[str, object] | None) -> ops.Node:
        updated = node.copy(**kwargs) if kwargs else node
        return _unsigned_aggregate(updated) if isinstance(updated, ops.Aggregate) else updated

    result = expression.op().replace(rewrite).to_expr()
    assert isinstance(result, ir.Table)
    return result


def bind_numeric_literal(value: ir.Value) -> ir.Value:
    """Bind unspecified Ibis Decimal literals from their literal precision/scale."""
    node = value.op()
    if (
        isinstance(node, ops.Literal)
        and isinstance(node.value, Decimal)
        and isinstance(node.dtype, dt.Decimal)
        and node.dtype.precision is None
    ):
        exponent = node.value.as_tuple().exponent
        assert isinstance(exponent, int)
        scale = max(0, -exponent)
        precision = max(1, max(0, node.value.adjusted() + 1) + scale)
        return ibis.literal(node.value, type=dt.Decimal(precision, scale))
    return value


def decimal_result_type(operation: str, a: dt.DataType, b: dt.DataType) -> dt.Decimal | None:
    """Infer bound Decimal add/subtract/multiply metadata, capped at precision 38."""
    if a.is_floating() or b.is_floating() or not (a.is_decimal() or b.is_decimal()):
        return None

    def parts(dtype: dt.DataType) -> tuple[int, int]:
        if isinstance(dtype, dt.Decimal):
            return dtype.precision or 38, dtype.scale or 0
        # Use the bound integer width, including literal types.
        return len(str(2 ** (dtype.nbytes * 8 - int(dtype.is_signed_integer())) - 1)), 0

    p1, s1 = parts(a)
    p2, s2 = parts(b)
    scale = s1 + s2 if operation == "multiply" else max(s1, s2)
    precision = p1 + p2 if operation == "multiply" else max(p1 - s1, p2 - s2) + scale + 1
    return dt.Decimal(min(38, precision), max(0, scale - max(0, precision - 38)))
