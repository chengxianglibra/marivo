"""Compute default analysis_scope and quality_summary for frame meta at commit time."""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from marivo.analysis._semantic_persistence import SlicePredicateV1
from marivo.analysis.evidence.types import (
    AnalysisScope,
    EventAnalysisScope,
    EvidenceScope,
    JsonValue,
    QualitySummary,
)
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.refs import RefPayloadV1
from marivo.semantic.metric_graph import DeltaComparisonIdentityV1, MetricIdentity

GRAIN_FREQ = {"hour": "h", "day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}


def normalize_coverage_buckets(timestamps: pd.Series, *, grain: str) -> pd.Series:
    """Normalize observed timestamps to the represented coverage bucket."""
    if grain == "hour":
        return cast("pd.Series", timestamps.dt.floor("h"))
    return cast("pd.Series", timestamps.dt.normalize())


def _coverage_summary_val(meta: BaseFrameMeta, key: str) -> float | int | None:
    """Extract a single value from the frame meta's coverage_summary dict."""
    coverage_summary = getattr(meta, "coverage_summary", None)
    if isinstance(coverage_summary, dict):
        val = coverage_summary.get(key)
        if isinstance(val, (int, float)):
            return val
    return None


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
        # Canonical "value" column takes priority over the legacy metric-name column.
        if "value" in frame._df.columns:
            col = "value"
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
                        observed_ts = normalize_coverage_buckets(
                            pd.to_datetime(frame._df[time_col]).dropna(), grain=grain
                        )
                        observed_set = set(observed_ts.unique())
                        missing = sum(
                            1
                            for ts in normalize_coverage_buckets(pd.Series(expected), grain=grain)
                            if pd.Timestamp(ts) not in observed_set
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
        sample_coverage_min=_coverage_summary_val(meta, "min"),
        sample_coverage_avg=_coverage_summary_val(meta, "avg"),
        sample_coverage_partial_buckets=(
            int(v)
            if isinstance(v := _coverage_summary_val(meta, "partial_buckets"), (int, float))
            else None
        ),
        zero_denominator_rows=getattr(meta, "zero_denominator_rows", None),
        evaluated_check_count=(
            len(checks) if isinstance(checks := getattr(meta, "checks_run", None), list) else None
        ),
        failed_check_count=getattr(meta, "blocking_issue_count", None),
        warning_check_count=getattr(meta, "warning_count", None),
    )


def compute_analysis_scope(frame: BaseFrame) -> EvidenceScope:
    """Derive a typed metric or Event Journey scope from frame metadata."""
    meta = frame.meta
    if getattr(meta, "kind", None) == "event_frame":
        event_meta = cast("Any", meta)
        pattern = event_meta.pattern
        matching = event_meta.matching
        cohort_window = event_meta.cohort_window
        role_endpoints = event_meta.role_endpoints
        input_coverage = event_meta.input_coverage
        roles = tuple(
            {
                "step_key": step.key,
                "event_ref": RefPayloadV1.from_ref(step.event).to_dict(),
                "participant_name": step.participant.name,
                "endpoint_ref": role_endpoints[step.key].to_dict(),
            }
            for step in pattern.steps
        )
        coverage = {
            "basis": event_meta.coverage_basis,
            "inputs": tuple(item.model_dump(mode="json") for item in input_coverage),
        }
        return EventAnalysisScope(
            pattern=cast("dict[str, JsonValue]", pattern.model_dump(mode="json")),
            roles=cast("tuple[dict[str, JsonValue], ...]", roles),
            matching=cast("dict[str, JsonValue]", matching.model_dump(mode="json")),
            cohort_window=cast(
                "dict[str, JsonValue]",
                cohort_window.model_dump(mode="json"),
            ),
            completion_through=str(event_meta.completion_through),
            coverage=cast("dict[str, JsonValue]", coverage),
        )

    metric_identities: tuple[MetricIdentity, ...] = ()
    comparison: DeltaComparisonIdentityV1 | None = None
    axis_refs: tuple[RefPayloadV1, ...] = ()
    segment_predicates: tuple[SlicePredicateV1, ...] = ()
    window: dict[str, JsonValue] | None = None

    # Use getattr to avoid importing concrete meta types which pull in
    # transitive deps that violate the analysis.evidence isolation contract.
    window_attr = getattr(meta, "window", None)
    alignment = getattr(meta, "alignment", None)
    forecast_window = getattr(meta, "forecast_window", None)
    identities_attr = getattr(meta, "metric_identities", None)
    if isinstance(identities_attr, tuple):
        metric_identities = identities_attr
    bindings_attr = getattr(meta, "axis_bindings", None)
    if isinstance(bindings_attr, tuple):
        axis_refs = tuple(binding.ref for binding in bindings_attr)
    predicates_attr = getattr(meta, "slice_predicates", None)
    if isinstance(predicates_attr, tuple):
        segment_predicates = predicates_attr
    comparison_attr = getattr(meta, "comparison_identity", None)
    if isinstance(comparison_attr, DeltaComparisonIdentityV1):
        comparison = comparison_attr

    if window_attr is not None:
        window = (
            {str(k): v for k, v in window_attr.items()} if isinstance(window_attr, dict) else None
        )
    elif alignment is not None:
        window = (
            {str(k): str(v) for k, v in alignment.items()} if isinstance(alignment, dict) else None
        )
    elif forecast_window is not None:
        window = (
            {str(k): v for k, v in forecast_window.items()}
            if isinstance(forecast_window, dict)
            else None
        )

    return AnalysisScope(
        metric_identities=metric_identities,
        comparison=comparison,
        axis_refs=axis_refs,
        segment_predicates=segment_predicates,
        window=window,
        assumptions=(),
    )
