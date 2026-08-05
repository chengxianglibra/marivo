"""Resolve canonical MetricFrame axis columns for analysis intents."""

from __future__ import annotations

from typing import Any, Literal

from marivo.analysis.frames.metric import MetricFrame

type AxisRole = Literal["dimension", "time_dimension"]


def metric_dimension_columns(frame: MetricFrame) -> list[str]:
    """Return dimension columns in stable structured-metadata order."""
    return _columns_for_role(frame, role="dimension")


def metric_time_axis(frame: MetricFrame) -> tuple[str, str]:
    """Return the canonical time column and grain for a MetricFrame."""
    bindings = [
        binding
        for binding in getattr(frame.meta, "axis_bindings", ())
        if binding.role == "time_dimension"
    ]
    axis = _first_axis_mapping(frame, role="time_dimension")
    if bindings:
        binding = bindings[0]
        grain = binding.grain or _axis_grain(axis) or "day"
        return binding.column, grain
    if axis is not None:
        column = axis.get("column") or axis.get("field")
        if isinstance(column, str) and column:
            return column, _axis_grain(axis) or "day"
    return "time", "day"


def _columns_for_role(frame: MetricFrame, *, role: AxisRole) -> list[str]:
    columns = [
        binding.column
        for binding in getattr(frame.meta, "axis_bindings", ())
        if binding.role == role
    ]
    if columns:
        return list(dict.fromkeys(columns))

    display_role = "time" if role == "time_dimension" else "dimension"
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict) or axis.get("role") != display_role:
            continue
        column = axis.get("column") or axis.get("field")
        if isinstance(column, str) and column:
            columns.append(column)
    return list(dict.fromkeys(columns))


def _first_axis_mapping(frame: MetricFrame, *, role: AxisRole) -> dict[str, Any] | None:
    display_role = "time" if role == "time_dimension" else "dimension"
    for axis in frame.meta.axes.values():
        if isinstance(axis, dict) and axis.get("role") == display_role:
            return axis
    if role == "time_dimension":
        time_axis = frame.meta.axes.get("time")
        if isinstance(time_axis, dict):
            return time_axis
    return None


def _axis_grain(axis: dict[str, Any] | None) -> str | None:
    if axis is None:
        return None
    grain = axis.get("grain")
    return grain if isinstance(grain, str) and grain else None
