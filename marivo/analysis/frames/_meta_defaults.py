"""Compute default analysis_scope and quality_summary for frame meta at commit time."""

from __future__ import annotations

from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis._semantic_persistence import SlicePredicateV1
from marivo.analysis.evidence.types import (
    AnalysisScope,
    EventAnalysisScope,
    EventFunnelAnalysisScope,
    EventTimeToEventAnalysisScope,
    EvidenceScope,
    JsonValue,
    LifecycleAnalysisScope,
    QualitySummary,
    SubjectSetAnalysisScope,
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
            # ``col`` is a single column name (guarded by ``in frame._df.columns``),
            # so ``isna().sum()`` is a scalar int; pandas-stubs types the index
            # expression as Series|DataFrame, hence the ignore.
            null_rate = 0.0 if n == 0 else float(frame._df[col].isna().sum()) / n  # type: ignore[arg-type]

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
    """Derive a typed metric, Event, or SubjectSet scope from frame metadata."""
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
        cohort = getattr(event_meta, "cohort", None)
        journey_scope = EventAnalysisScope(
            pattern=cast("dict[str, JsonValue]", pattern.model_dump(mode="json")),
            roles=cast("tuple[dict[str, JsonValue], ...]", roles),
            matching=cast("dict[str, JsonValue]", matching.model_dump(mode="json")),
            cohort_window=cast(
                "dict[str, JsonValue]",
                cohort_window.model_dump(mode="json"),
            ),
            completion_through=str(event_meta.completion_through),
            coverage=cast("dict[str, JsonValue]", coverage),
            cohort_binding=(
                cast("dict[str, JsonValue]", cohort.model_dump(mode="json"))
                if cohort is not None
                else None
            ),
        )
        semantic_kind = getattr(event_meta, "semantic_kind", None)
        if semantic_kind == "funnel":
            return EventFunnelAnalysisScope(
                source_artifact_ref=str(event_meta.source_journey_ref),
                source_scope=journey_scope,
                axes=tuple(
                    cast("dict[str, JsonValue]", axis.model_dump(mode="json"))
                    for axis in event_meta.axes
                ),
                grouped_reconciliation=cast(
                    "dict[str, JsonValue]",
                    event_meta.grouped_reconciliation.model_dump(mode="json"),
                ),
            )
        if semantic_kind == "time_to_event":
            return EventTimeToEventAnalysisScope(
                source_artifact_ref=str(event_meta.source_journey_ref),
                source_scope=journey_scope,
                start_step=cast(
                    "dict[str, JsonValue]",
                    event_meta.start_step.model_dump(mode="json"),
                ),
                end_step=cast(
                    "dict[str, JsonValue]",
                    event_meta.end_step.model_dump(mode="json"),
                ),
            )
        return journey_scope

    if getattr(meta, "kind", None) == "lifecycle_frame":
        lifecycle_meta = cast("Any", meta)
        semantic_kind = cast(
            "Literal['history', 'distribution', 'transitions', 'dwell', 'violations']",
            str(lifecycle_meta.semantic_kind),
        )
        source_history_ref = getattr(lifecycle_meta, "source_history_ref", None)
        window_value = getattr(lifecycle_meta, "window", None)
        lifecycle_coverage: dict[str, JsonValue] | None = None
        replay_semantics: dict[str, JsonValue] | None = None
        if semantic_kind == "history":
            lifecycle_coverage = {
                "basis": lifecycle_meta.coverage_basis,
                "inputs": tuple(
                    item.model_dump(mode="json") for item in lifecycle_meta.input_coverage
                ),
            }
            replay_semantics = {
                "operator_version": lifecycle_meta.operator_version,
                "seed": lifecycle_meta.seed.model_dump(mode="json"),
                "violation_behavior_id": lifecycle_meta.violation_behavior_id,
            }
        reducer_payload: dict[str, JsonValue] | None = None
        if semantic_kind == "distribution":
            reducer_payload = {
                "at": list(lifecycle_meta.at),
                "axes": [axis.model_dump(mode="json") for axis in lifecycle_meta.axes],
                "grouped_reconciliation_hash": (lifecycle_meta.grouped_reconciliation_hash),
            }
        elif semantic_kind == "transitions":
            reducer_payload = {
                "modeled_pairs": [
                    pair.model_dump(mode="json") for pair in lifecycle_meta.modeled_pairs
                ],
            }
        elif semantic_kind == "dwell":
            reducer_payload = {
                "source_interval_count": lifecycle_meta.source_interval_count,
            }
        elif semantic_kind == "violations":
            reducer_payload = {
                "violation_count": lifecycle_meta.violation_count,
            }
        cohort = getattr(lifecycle_meta, "cohort", None)
        return LifecycleAnalysisScope(
            state_model_ref=lifecycle_meta.state_model_ref,
            state_model_fingerprint=lifecycle_meta.state_model_fingerprint,
            analysis_axis=semantic_kind,
            source_history_ref=source_history_ref,
            window=(
                cast("dict[str, JsonValue]", window_value.model_dump(mode="json"))
                if window_value is not None
                else None
            ),
            coverage=lifecycle_coverage,
            cohort_binding=(
                cast("dict[str, JsonValue]", cohort.model_dump(mode="json"))
                if cohort is not None
                else None
            ),
            replay_semantics=replay_semantics,
            reducer=reducer_payload,
        )

    if getattr(meta, "kind", None) == "subject_set":
        subject_meta = cast("Any", meta)
        return SubjectSetAnalysisScope(
            source_artifact_ref=subject_meta.source.artifact_ref,
            source_artifact_fingerprint=subject_meta.source.artifact_fingerprint,
            selection=cast(
                "dict[str, JsonValue]",
                subject_meta.selection.model_dump(mode="json"),
            ),
            selection_fingerprint=subject_meta.selection_fingerprint,
            coverage_status=subject_meta.coverage_status,
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
