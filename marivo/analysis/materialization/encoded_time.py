"""Retain qualified encoded time authority until source filter lowering."""

from collections.abc import Mapping

import ibis.expr.operations as ops
import ibis.expr.rules as rlz
import ibis.expr.types as ir


class EncodedTime(ops.Value):  # type: ignore[misc]  # Ibis operation bases lack typing stubs.
    """An instant and its declared physical ordered encoding."""

    raw: ops.Value
    instant: ops.Value
    format: str
    timezone: str
    dtype = rlz.dtype_like("instant")
    shape = rlz.shape_like("instant")


def encoded_time(raw: ir.Value, instant: ir.Value, format: str, timezone: str) -> ir.Value:
    """Mark a physical source encoding without changing its value semantics."""
    return EncodedTime(raw.op(), instant.op(), format, timezone).to_expr()


def lower_encoded_times(expression: ir.Expr) -> ir.Expr:
    """Inline markers remaining after qualified predicates have been rewritten."""

    def inline(node: ops.Node, _results: Mapping[ops.Node, ops.Node], **kwargs: object) -> ops.Node:
        value = node.copy(**kwargs)
        return value.instant if isinstance(value, EncodedTime) else value

    return expression.op().map(inline)[expression.op()].to_expr()
