"""Analysis error adapter for dependency-neutral runtime metric lowering."""

from typing import cast

from marivo.analysis.intents.observe_errors import (
    ObserveErrorCode,
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.refs import MetricKind, Ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.metric_graph_lowering import MetricExpressionForestV1
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.runtime_metric_lowering import (
    RuntimeMetricLoweringError,
)
from marivo.semantic.runtime_metric_lowering import (
    lower_metric_inputs as _lower_metric_inputs,
)
from marivo.semantic.validator import Registry


def lower_metric_inputs(
    registry: Registry,
    inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
    *,
    sidecar: CompiledExpressionSidecar | None = None,
) -> MetricExpressionForestV1:
    """Lower inputs while preserving the public analysis planning error contract."""

    try:
        return _lower_metric_inputs(registry, inputs, sidecar=sidecar)
    except RuntimeMetricLoweringError as exc:
        repairs = [
            RepairAction(
                action=str(item["action"]),
                target=str(item["target"]),
                arg=str(item["arg"]),
                value=item["value"],
                safety=RepairSafety(str(item["safety"])),
                why=str(item["why"]),
            )
            for item in exc.repairs
        ]
        raise_observe_planning_error(
            code=cast("ObserveErrorCode", exc.code),
            message=str(exc),
            candidates=exc.candidates,
            repair=repairs,
        )


__all__ = ["lower_metric_inputs"]
