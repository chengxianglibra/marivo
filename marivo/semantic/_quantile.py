"""Private explicit percentile method selection for the lazy cutover."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.refs import MetricKind, Ref

QuantileMethod = Literal["linear_interpolation@v1", "duckdb_tdigest@v1"]


class QuantileMethodV1(BaseModel):
    """One authored quantile and exact backend method, never a cost fallback."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    method: QuantileMethod
    q: float = Field(gt=0, lt=1)


@dataclass(frozen=True, slots=True, repr=False)
class QuantileMetricInput:
    """Explicit private method on a governed median/percentile Metric ref."""

    metric: Ref[MetricKind]
    method: QuantileMethod


def quantile_metric(metric: Ref[MetricKind], *, method: QuantileMethod) -> QuantileMetricInput:
    """Select a private method; q remains owned by the governed Metric declaration.

    Args: metric: Governed percentile Metric. method: Exact registered method id.
    Returns: Private observation input. Example: ``quantile_metric(p95,
        method="duckdb_tdigest@v1")``.
    Constraints: No public authoring entry or backend-dependent fallback.
    """
    if method not in ("linear_interpolation@v1", "duckdb_tdigest@v1"):
        raise ValueError("expected one registered quantile method")
    return QuantileMetricInput(metric, method)


ApproximationClass = Literal[
    "exact", "sampled_population", "semantic_percentile", "sampled_semantic_percentile"
]


def approximation_class(*, sampled: bool, semantic: bool) -> ApproximationClass:
    return (
        ("sampled_semantic_percentile" if sampled else "semantic_percentile")
        if semantic
        else ("sampled_population" if sampled else "exact")
    )


def decode_approximation(value: object) -> ApproximationClass:
    match value:
        case "exact" | "sampled_population" | "semantic_percentile" | "sampled_semantic_percentile":
            return value
        case _:
            raise ValueError("invalid approximation class")
