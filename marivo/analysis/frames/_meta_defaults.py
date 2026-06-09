"""Compute default confidence_scope and quality_summary for frame meta at commit time."""

from __future__ import annotations

from typing import Any

import pandas as pd

from marivo.analysis.evidence.types import QualitySummary
from marivo.analysis.followups import ConfidenceScope
from marivo.analysis.frames.base import BaseFrame

GRAIN_FREQ = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}


def compute_quality_summary(frame: BaseFrame) -> QualitySummary:
    """Lightweight quality summary computed synchronously at commit time."""
    meta = frame.meta
    sample_size = meta.row_count

    null_rate: float | None = None
    coverage: float | None = None

    # MetricFrame-specific fields accessed via getattr to avoid
    # importing concrete frame meta types (transitive deps violate
    # the analysis.evidence isolation contract).
    measure = getattr(meta, "measure", None)
    semantic_kind = getattr(meta, "semantic_kind", None)
    axes = getattr(meta, "axes", None)
    window = getattr(meta, "window", None)

    if isinstance(measure, dict):
        col = measure.get("field") or measure.get("name")
        if col and col in frame._df.columns:
            n = len(frame._df)
            null_rate = 0.0 if n == 0 else float(frame._df[col].isna().sum()) / n

        if semantic_kind in {"time_series", "panel"} and isinstance(axes, dict):
            time_axis = axes.get("time", {})
            if isinstance(time_axis, dict):
                time_col = time_axis.get("field") or time_axis.get("column") or "time"
                grain = time_axis.get("grain", "day")
            else:
                time_col, grain = "time", "day"
            if (
                isinstance(window, dict)
                and window.get("start")
                and window.get("end")
                and grain in GRAIN_FREQ
            ):
                try:
                    expected = pd.date_range(
                        pd.Timestamp(window["start"]),
                        pd.Timestamp(window["end"]),
                        freq=GRAIN_FREQ[grain],
                        inclusive="left",
                    )
                    if time_col in frame._df.columns and len(frame._df) > 0:
                        observed_ts = pd.to_datetime(frame._df[time_col]).dropna().dt.normalize()
                        observed_set = set(observed_ts.unique())
                        missing = sum(
                            1 for ts in expected if pd.Timestamp(ts).normalize() not in observed_set
                        )
                        coverage = 1.0 - (missing / len(expected)) if len(expected) > 0 else None
                    else:
                        coverage = 0.0
                except Exception:
                    coverage = None

    return QualitySummary(
        coverage=coverage,
        null_rate=null_rate,
        sample_size=sample_size,
        metric_definition_compatibility="unknown",
    )


def compute_confidence_scope(frame: BaseFrame) -> ConfidenceScope:
    """Derive ConfidenceScope from frame meta fields."""
    meta = frame.meta
    metric_ids: list[str] = []
    segment_keys: dict[str, Any] = {}
    window: dict[str, Any] | None = None

    # Use getattr to avoid importing concrete meta types which pull in
    # transitive deps that violate the analysis.evidence isolation contract.
    metric_id = getattr(meta, "metric_id", None)
    metric_ids_attr = getattr(meta, "metric_ids", None)
    axes = getattr(meta, "axes", None)
    window_attr = getattr(meta, "window", None)
    alignment = getattr(meta, "alignment", None)
    forecast_window = getattr(meta, "forecast_window", None)
    target_metric_id = getattr(meta, "target_metric_id", None)

    if metric_id is not None:
        metric_ids = [str(metric_id)]
    elif metric_ids_attr is not None:
        metric_ids = list(metric_ids_attr)
    elif target_metric_id is not None:
        metric_ids = [str(target_metric_id)]

    if isinstance(axes, dict):
        segment_keys = {k: v for k, v in axes.items() if k != "time" and isinstance(v, dict)}

    if window_attr is not None:
        window = window_attr
    elif alignment is not None:
        window = alignment
    elif forecast_window is not None:
        window = forecast_window

    return ConfidenceScope(
        metric_ids=metric_ids,
        segment_keys=segment_keys,
        window=window,
        assumptions=[],
    )
