"""Explicit governed percentile method selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.refs import MetricKind, Ref, SemanticKind
from marivo.semantic.errors import SemanticLoadError, repair
from marivo.semantic.runtime_metric import RuntimeMetricExpr

QuantileMethod = Literal["linear_interpolation@v1", "duckdb_tdigest@v1"]


class QuantileMethodV1(BaseModel):
    """One authored quantile and exact backend method, never a cost fallback."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)
    method: QuantileMethod
    q: float = Field(gt=0, lt=1)


@dataclass(frozen=True, slots=True, repr=False)
class QuantileMetricInput:
    """Immutable method selection for one governed median/percentile Metric.

    Acquire with ``ms.quantile_metric(metric, method=...)`` and pass to
    ``session.observe``. The original Metric declaration continues to own q.
    """

    metric: Ref[MetricKind] | RuntimeMetricExpr
    method: QuantileMethod

    def __post_init__(self) -> None:
        if self.method not in ("linear_interpolation@v1", "duckdb_tdigest@v1") or not (
            (type(self.metric) is Ref and self.metric.kind is SemanticKind.METRIC)
            or isinstance(self.metric, RuntimeMetricExpr)
        ):
            action = "Use a governed median/percentile Metric and explicitly choose linear_interpolation@v1 or duckdb_tdigest@v1."
            raise SemanticLoadError(
                kind="invalid_quantile_method",
                message="Quantile method selection requires a governed Metric and a registered method.",
                expected="Ref[metric] or RuntimeMetric expression and an exact registered quantile method",
                received=f"metric={type(self.metric).__name__}, method={type(self.method).__name__}",
                hint=action,
                repair=repair(kind="reauthor", canonical_id="quantile_metric", action=action),
            )

    def __repr__(self) -> str:
        identity = self.metric.key if isinstance(self.metric, Ref) else self.metric.label
        safe = repr(identity[:96])
        return f"<QuantileMetricInput metric={safe} method={self.method}; use .show()>"

    def render(self) -> str:
        """Describe the fixed quantile method and its observation continuation.

        Returns:
            Bounded text containing the Metric identity and selected method.
        Example:
            print(ms.quantile_metric(metric, method="duckdb_tdigest@v1").render())
        Constraints:
            Performs no source reads or execution; the Metric still owns q.
        """
        return f"{self!r}\nPass this value to session.observe; projection preserves its method."

    def show(self) -> None:
        """Print the fixed quantile method and its observation continuation.

        Returns:
            None; writes the bounded description to standard output.
        Example:
            ms.quantile_metric(metric, method="duckdb_tdigest@v1").show()
        Constraints:
            Performs no source reads or execution; the Metric still owns q.
        """
        print(self.render())


def quantile_metric(
    metric: Ref[MetricKind] | RuntimeMetricExpr, *, method: QuantileMethod
) -> QuantileMetricInput:
    """Choose the exact or approximate implementation of a governed quantile.

    Args:
        metric: A governed median/percentile Metric ref or RuntimeMetric expression.
        method: ``linear_interpolation@v1`` for exact interpolation, or
            ``duckdb_tdigest@v1`` for explicit semantic approximation.

    Returns:
        An immutable QuantileMetricInput accepted by Session.observe.

    Example:
        >>> selected = ms.quantile_metric(
        ...     ms.ref.metric("sales.p95_amount"), method="duckdb_tdigest@v1"
        ... )
        >>> result = session.observe(selected).aggregate().execute()

    Constraints:
        The Metric owns q. Construction performs no data access. Observation
        verifies the root is a supported median/percentile. Projection cannot
        change the method, and execution never selects a fallback method.
    """
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
