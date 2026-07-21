"""Public analysis facade for dependency-neutral runtime metric descriptors."""

from marivo.semantic.runtime_metric import (
    FrozenSliceMap,
    RuntimeAggregateExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    aggregate,
    ratio,
    slice,
    weighted_mean,
)
from marivo.semantic.runtime_metric import (
    from_replay_payload as from_replay_payload,
)
from marivo.semantic.runtime_metric import (
    replay_payload as replay_payload,
)

__all__ = [
    "FrozenSliceMap",
    "RuntimeAggregateExpr",
    "RuntimeMetricExpr",
    "RuntimeRatioExpr",
    "RuntimeSliceExpr",
    "RuntimeWeightedMeanExpr",
    "aggregate",
    "ratio",
    "slice",
    "weighted_mean",
]
