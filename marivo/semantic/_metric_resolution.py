"""Shared resolution for tier-1 metric additivity and temporal folds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marivo.semantic.ir import (
    Additivity,
    AggKind,
    AggregateFoldInput,
    MetricIR,
    SemiAdditive,
    TimeFoldIR,
)


@dataclass(frozen=True)
class MetricTemporalContract:
    """A metric's status-time axis and fold, independent of spatial additivity."""

    status_time_dimension: str
    fold: TimeFoldIR


def fold_input_to_ir(value: AggregateFoldInput) -> TimeFoldIR | None:
    """Convert an already-validated public fold value to semantic IR."""

    if value is None:
        return None
    if isinstance(value, tuple):
        return TimeFoldIR(kind="percentile", q=value[1])
    return TimeFoldIR(kind=value)


def fold_ir_to_input(value: TimeFoldIR | None) -> AggregateFoldInput:
    """Convert a semantic fold to its canonical graph/runtime value."""

    if value is None:
        return None
    if value.kind == "percentile":
        if value.q is None:
            raise AssertionError("percentile TimeFoldIR requires q")
        return ("percentile", value.q)
    return value.kind


def resolve_measure_aggregate_additivity(
    agg: AggKind | None,
    target_additivity: Additivity,
) -> Additivity | None:
    """Resolve spatial reaggregation semantics for one measure aggregate."""

    agg_name = agg[0] if isinstance(agg, tuple) else agg
    if agg_name == "count":
        return "additive"
    if agg_name == "sum":
        if target_additivity == "additive" or isinstance(target_additivity, SemiAdditive):
            return target_additivity
        return None
    return "non_additive"


def resolve_aggregate_temporal_contract(
    target_additivity: Additivity,
    *,
    fold_override: TimeFoldIR | None,
) -> MetricTemporalContract | None:
    """Resolve a measure aggregate's time contract without changing its additivity."""

    if not isinstance(target_additivity, SemiAdditive):
        return None
    return MetricTemporalContract(
        status_time_dimension=target_additivity.over,
        fold=fold_override or target_additivity.fold,
    )


def resolve_metric_temporal_contract(
    metric: MetricIR,
    registry: Any,
) -> MetricTemporalContract | None:
    """Resolve the effective status-time contract for one loaded metric."""

    if metric.aggregation is None:
        if isinstance(metric.additivity, SemiAdditive):
            return MetricTemporalContract(
                status_time_dimension=metric.additivity.over,
                fold=metric.additivity.fold,
            )
        return None

    target_kind = metric.aggregation_target_kind or (
        "measure" if metric.measure is not None else None
    )
    if target_kind != "measure":
        return None
    target_id = metric.aggregation_target or metric.measure
    if target_id is None:
        return None
    target = registry.measures.get(target_id)
    if target is None:
        target = registry.dimensions.get(target_id)
    target_additivity = getattr(target, "additivity", None)
    if target_additivity is None:
        return None
    return resolve_aggregate_temporal_contract(
        target_additivity,
        fold_override=metric.fold_override,
    )


__all__ = [
    "MetricTemporalContract",
    "fold_input_to_ir",
    "fold_ir_to_input",
    "resolve_aggregate_temporal_contract",
    "resolve_measure_aggregate_additivity",
    "resolve_metric_temporal_contract",
]
