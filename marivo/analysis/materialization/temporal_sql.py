"""Optimize and lower Analysis-owned temporal expressions on their source backend."""

import ibis.expr.types as ir

from marivo.datasource._temporal_lowering import (
    governed_temporal_operation as governed_temporal_operation,
)
from marivo.datasource._temporal_lowering import lower_temporal as lower_native_temporal
from marivo.datasource._temporal_lowering import needs_temporal_lowering


def lower_temporal(expression: ir.Expr, engine: str) -> ir.Expr:
    """Optimize Analysis filters before applying the shared native lowering."""
    from marivo.analysis.materialization.date_day_keys import has_deferred_day_keys, lower_day_keys
    from marivo.analysis.materialization.encoded_time import EncodedTime, lower_encoded_times
    from marivo.analysis.materialization.time_filter import cancel_utc, optimize_time

    deferred = has_deferred_day_keys(expression)
    encoded = bool(expression.op().find(EncodedTime))
    if not deferred and not encoded and not needs_temporal_lowering(expression, engine):
        return expression
    expression = optimize_time(
        expression if engine == "clickhouse" else cancel_utc(expression), engine
    )
    if deferred:
        expression = lower_day_keys(expression)
    if encoded:
        expression = lower_encoded_times(expression)
    return lower_native_temporal(expression, engine)
