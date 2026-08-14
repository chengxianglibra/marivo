"""Resolve canonical MetricFrame axis columns for analysis intents.

Issue #54: axis resolution must come only from the typed ``axis_bindings``
authority. The legacy compact ``axes`` dict is a render-boundary projection and
is never a fallback source for intents.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from marivo.analysis.frames.metric import MetricFrame

AxisRole: TypeAlias = Literal["dimension", "time_dimension"]


def metric_dimension_columns(frame: MetricFrame) -> list[str]:
    """Return dimension columns in stable structured-metadata order."""
    return _columns_for_role(frame, role="dimension")


def metric_time_axis(frame: MetricFrame) -> tuple[str, str]:
    """Return the canonical time column and grain for a MetricFrame."""
    for binding in frame.meta.axis_bindings:
        if binding.role != "time_dimension":
            continue
        grain = binding.grain or "day"
        return binding.column, grain
    return "time", "day"


def _columns_for_role(frame: MetricFrame, *, role: AxisRole) -> list[str]:
    columns = [binding.column for binding in frame.meta.axis_bindings if binding.role == role]
    return list(dict.fromkeys(columns))


__all__ = ["metric_dimension_columns", "metric_time_axis"]
