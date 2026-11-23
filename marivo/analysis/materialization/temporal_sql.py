"""Optimize and lower Analysis-owned temporal expressions on their source backend."""

import ibis.expr.types as ir

from marivo.analysis.materialization.date_day_keys import has_deferred_day_keys, lower_day_keys
from marivo.analysis.materialization.encoded_time import EncodedTime, lower_encoded_times
from marivo.analysis.materialization.time_filter import cancel_utc, optimize_time
from marivo.analysis.model.optimization_policy import (
    DEFAULT_OPTIMIZATION_POLICY,
    OptimizationPolicy,
)
from marivo.datasource.spi import (
    governed_temporal_operation as governed_temporal_operation,
)
from marivo.datasource.spi import lower_native_temporal, needs_temporal_lowering, query_dialect


def lower_temporal(
    expression: ir.Expr, engine: str, *, policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY
) -> ir.Expr:
    """Optimize Analysis filters before applying the shared native lowering."""

    deferred = has_deferred_day_keys(expression)
    encoded = bool(expression.op().find(EncodedTime))
    if not deferred and not encoded and not needs_temporal_lowering(expression, engine):
        return expression
    if not query_dialect(engine).preserve_utc_conversions:
        expression = cancel_utc(expression)
    if policy.allows("encoded_time_predicate"):
        expression = optimize_time(expression, engine)
    if deferred:
        expression = lower_day_keys(
            expression, lift_aggregates=policy.allows("injective_time_bucket")
        )
    if encoded:
        expression = lower_encoded_times(expression)
    return lower_native_temporal(expression, engine)
