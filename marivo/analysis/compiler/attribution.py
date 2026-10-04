"""Ibis numeric reconciliation helpers used by distribution attribution."""

from __future__ import annotations

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error


def _numeric(value: ir.Value) -> ir.NumericValue:
    if not isinstance(value, ir.NumericValue):
        raise compilation_error("numeric Attribution component", "invalid component expression")
    return value


def _finite(value: ir.Value) -> ir.BooleanValue:
    result = value.notnull()
    return (
        result & ~value.isnan() & ~value.isinf() if isinstance(value, ir.FloatingValue) else result
    )


def _close(left: ir.Value, right: ir.Value) -> ir.BooleanValue:
    a, b = _numeric(left.cast("float64")), _numeric(right.cast("float64"))
    tolerance = ibis.greatest(
        ibis.literal(1e-12), 1e-9 * ibis.greatest(a.abs(), b.abs(), ibis.literal(1.0))
    )
    return (_finite(a) & _finite(b) & ((a - b).abs() <= tolerance)).fill_null(False)


def _group(table: ir.Table, keys: tuple[str, ...], metrics: dict[str, ir.Value]) -> ir.Table:
    return (table.group_by(list(keys)) if keys else table).aggregate(**metrics)
