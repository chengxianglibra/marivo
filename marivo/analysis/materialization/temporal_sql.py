"""Optimize and lower Analysis-owned temporal expressions on their source backend."""

import ibis.expr.types as ir

from marivo.datasource._temporal_lowering import (
    governed_temporal_operation as governed_temporal_operation,
)
from marivo.datasource._temporal_lowering import lower_temporal as lower_native_temporal
from marivo.datasource._temporal_lowering import needs_temporal_lowering


def lower_temporal(expression: ir.Expr, engine: str) -> ir.Expr:
    """Optimize Analysis filters before applying the shared native lowering."""
    from marivo.analysis.materialization.time_filter import cancel_utc, optimize_time

    if not needs_temporal_lowering(expression, engine):
        return expression
    expression = optimize_time(expression if engine == "clickhouse" else cancel_utc(expression))
    return lower_native_temporal(expression, engine)
