"""Public analysis facade for dependency-neutral runtime metric descriptors."""

from marivo.semantic.runtime_metric import (
    FrozenSliceMap,
    RuntimeAggregateExpr,
    RuntimeLinearExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    aggregate,
    linear,
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
    "RuntimeLinearExpr",
    "RuntimeMetricExpr",
    "RuntimeRatioExpr",
    "RuntimeSliceExpr",
    "RuntimeWeightedMeanExpr",
    "aggregate",
    "linear",
    "ratio",
    "slice",
    "weighted_mean",
]
