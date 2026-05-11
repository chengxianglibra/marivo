from __future__ import annotations

import contextlib
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from marivo.core.intent.primitives import make_provenance, new_step_id
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.core.semantic.step_metadata import build_step_semantic_metadata
from marivo.runtime.evidence.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.calendar_alignment_metadata import normalize_resolved_policy_summary
from marivo.runtime.intents.normalization import (
    normalize_dimensions,
    normalize_metric_ref,
    validate_and_normalize_calendar_policy_ref,
    validate_granularity,
    validate_hour_boundaries,
)
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_contracts import TimeGrain, bucket_window
from marivo.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


def _build_step_metadata(compiled_queries: Any) -> dict[str, Any] | None:
    result = build_step_semantic_metadata(compiled_queries)
    if result is not None:
        assert_no_canonical_refs_in_semantic_payload(result, surface="step_semantic_metadata")
    return result


def _malformed_resolved_policy_summary() -> ValueError:
    return ValueError("observe: INVALID_ARGUMENT - malformed resolved calendar alignment metadata")


def _resolved_policy_summary_from_compiled(compiled_query: Any) -> dict[str, Any] | None:
    metadata = getattr(compiled_query, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    resolved_calendar_alignment = metadata.get("resolved_calendar_alignment")
    if resolved_calendar_alignment is None:
        return None
    return normalize_resolved_policy_summary(
        resolved_calendar_alignment,
        error_factory=_malformed_resolved_policy_summary,
    )


def _extract_predicate_filter_lineage(compiled_query: Any) -> dict[str, Any] | None:
    """Extract predicate_filter_lineage from the first MeasurementNode in the IR bundle."""
    ir_bundle = getattr(compiled_query, "ir_bundle", None)
    if ir_bundle is None:
        return None
    for node in ir_bundle.get("plan", {}).get("nodes") or []:
        if node.get("node_type") == "measurement":
            lineage: dict[str, Any] | None = node.get("predicate_filter_lineage")
            if lineage is not None:
                return lineage
    return None


def _resolve_observe_time_scope(time_scope_raw: dict[str, Any]) -> tuple[str, str, str | None]:
    kind = time_scope_raw.get("kind")
    # AOI-aligned McpTimeScope uses {field, start, end} without kind.
    # Treat missing kind as "range" when start and end are present.
    if kind is None and "start" in time_scope_raw and "end" in time_scope_raw:
        kind = "range"
    if kind == "range":
        try:
            return (
                str(time_scope_raw["start"]),
                str(time_scope_raw["end"]),
                time_scope_raw.get("field"),
            )
        except KeyError as exc:
            raise ValueError(
                "observe: INVALID_ARGUMENT - range time_scope requires start and end"
            ) from exc

    if kind in {"snapshot_now", "latest_available"}:
        start = datetime.now(UTC).date()
        end = start + timedelta(days=1)
        return start.isoformat(), end.isoformat(), time_scope_raw.get("field")

    if kind == "as_of":
        raw_at = time_scope_raw.get("at")
        if not isinstance(raw_at, str) or not raw_at.strip():
            raise ValueError("observe: INVALID_ARGUMENT - as_of time_scope requires at")
        observed_date = datetime.fromisoformat(raw_at.replace("Z", "+00:00")).date()
        return (
            observed_date.isoformat(),
            (observed_date + timedelta(days=1)).isoformat(),
            time_scope_raw.get("field"),
        )

    raise ValueError(f"observe: INVALID_ARGUMENT - unsupported time_scope.kind={kind!r}")


def _series_from_rows(
    rows: list[dict[str, Any]], *, granularity: TimeGrain
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for row in rows:
        bucket_raw = row.get("bucket_start")
        raw_value = row.get("value")
        series_value: float | None = None
        with contextlib.suppress(TypeError, ValueError):
            if raw_value is not None:
                series_value = float(raw_value)
        if bucket_raw is None:
            continue
        try:
            window = bucket_window(bucket_raw, granularity)
        except (ValueError, TypeError):
            bucket_str = str(bucket_raw)
            window = {"start": bucket_str, "end": bucket_str}
        series.append({"window": window, "value": series_value})
    return series


def _coerce_numeric_or_none(value: Any) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        if value is not None:
            return float(value)
    return None


def _window_start(point: Mapping[str, Any], *, label: str) -> str:
    window = _require_mapping(point.get("window"), label=label)
    start = window.get("start")
    if not isinstance(start, str) or not start:
        raise ValueError(f"observe: INVALID_ARGUMENT - {label}.start must be a string")
    return start


def _series_map(series: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    by_start: dict[str, dict[str, Any]] = {}
    for point in series:
        by_start[_window_start(point, label=label)] = point
    return by_start


def _truncate_bucket_start(start: str, *, granularity: TimeGrain) -> str:
    if granularity == "hour":
        current_dt = datetime.fromisoformat(start).replace(minute=0, second=0, microsecond=0)
        return current_dt.isoformat(timespec="seconds")

    current_date = date.fromisoformat(start[:10])
    if granularity == "week":
        current_date = current_date - timedelta(days=current_date.weekday())
    elif granularity == "month":
        current_date = current_date.replace(day=1)
    return current_date.isoformat()


def _advance_bucket_start(start: str, *, granularity: TimeGrain) -> str:
    if granularity == "hour":
        current_dt = datetime.fromisoformat(start) + timedelta(hours=1)
        return current_dt.isoformat(timespec="seconds")

    current_date = date.fromisoformat(start[:10])
    if granularity == "day":
        current_date = current_date + timedelta(days=1)
    elif granularity == "week":
        current_date = current_date + timedelta(weeks=1)
    else:
        year = current_date.year + (1 if current_date.month == 12 else 0)
        month = 1 if current_date.month == 12 else current_date.month + 1
        current_date = date(year, month, 1)
    return current_date.isoformat()


def _expected_bucket_windows(
    *, start: str, end: str, granularity: TimeGrain
) -> list[dict[str, str]]:
    current = _truncate_bucket_start(start, granularity=granularity)
    windows: list[dict[str, str]] = []
    if granularity == "hour":
        end_boundary = datetime.fromisoformat(end)
        while datetime.fromisoformat(current) < end_boundary:
            windows.append(bucket_window(current, granularity))
            current = _advance_bucket_start(current, granularity=granularity)
        return windows

    end_date_boundary = date.fromisoformat(end[:10])
    while date.fromisoformat(current[:10]) < end_date_boundary:
        windows.append(bucket_window(current, granularity))
        current = _advance_bucket_start(current, granularity=granularity)
    return windows


def _build_dense_series(
    *,
    sparse_series: list[dict[str, Any]],
    start: str,
    end: str,
    granularity: TimeGrain,
) -> list[dict[str, Any]]:
    sparse_by_start = _series_map(sparse_series, label="series.window")
    dense_series: list[dict[str, Any]] = []
    for window in _expected_bucket_windows(start=start, end=end, granularity=granularity):
        start_key = str(window["start"])
        point = sparse_by_start.get(start_key)
        dense_series.append(
            {
                "window": dict(point.get("window") or window) if point is not None else window,
                "value": _coerce_numeric_or_none(point.get("value")) if point is not None else None,
            }
        )
    return dense_series


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"observe: INVALID_ARGUMENT - {label} must be an object")
    return value


def _build_aligned_time_series_payloads(
    *,
    current_series: list[dict[str, Any]],
    baseline_series: list[dict[str, Any]],
    resolved_policy_summary: dict[str, Any] | None,
    granularity: str,
) -> dict[str, list[dict[str, Any]]]:
    if resolved_policy_summary is None:
        return {}
    if granularity != "day":
        return {}

    bucket_pairing = resolved_policy_summary.get("bucket_pairing")
    if not isinstance(bucket_pairing, list):
        raise ValueError(
            "observe: INVALID_ARGUMENT - resolved_policy_summary.bucket_pairing must be a list"
        )

    current_by_start = _series_map(current_series, label="series.window")
    baseline_by_start = _series_map(baseline_series, label="baseline_series.window")

    aligned_baseline_series: list[dict[str, Any]] = []
    yoy_series: list[dict[str, Any]] = []
    for pairing in bucket_pairing:
        pairing_map = _require_mapping(pairing, label="resolved_policy_summary.bucket_pairing[]")
        current_bucket_start = pairing_map.get("current_bucket_start")
        if not isinstance(current_bucket_start, str) or not current_bucket_start:
            raise ValueError(
                "observe: INVALID_ARGUMENT - resolved_policy_summary.bucket_pairing[].current_bucket_start must be a string"
            )
        current_point = current_by_start.get(current_bucket_start)

        baseline_bucket_start = pairing_map.get("baseline_bucket_start")
        baseline_point = (
            baseline_by_start.get(baseline_bucket_start)
            if isinstance(baseline_bucket_start, str) and baseline_bucket_start
            else None
        )
        current_window = (
            dict(_require_mapping(current_point.get("window"), label="series.window"))
            if current_point is not None
            else bucket_window(current_bucket_start, "day")
        )
        current_value = (
            _coerce_numeric_or_none(current_point.get("value")) if current_point else None
        )
        baseline_value = (
            _coerce_numeric_or_none(baseline_point.get("value"))
            if baseline_point is not None
            else None
        )
        baseline_value_float = baseline_value
        baseline_window = (
            dict(_require_mapping(baseline_point.get("window"), label="baseline_series.window"))
            if baseline_point is not None
            else (
                bucket_window(baseline_bucket_start, "day")
                if isinstance(baseline_bucket_start, str) and baseline_bucket_start
                else None
            )
        )
        absolute_delta = (
            current_value - baseline_value_float
            if current_value is not None and baseline_value_float is not None
            else None
        )
        relative_delta: float | None = None
        if (
            absolute_delta is not None
            and baseline_value_float is not None
            and baseline_value_float != 0.0
        ):
            relative_delta = absolute_delta / baseline_value_float
        aligned_baseline_series.append(
            {
                "window": current_window,
                "baseline_window": baseline_window,
                "value": baseline_value,
            }
        )
        yoy_series.append(
            {
                "window": current_window,
                "baseline_window": baseline_window,
                "current_value": current_value,
                "baseline_value": baseline_value,
                "absolute_delta": absolute_delta,
                "relative_delta": relative_delta,
            }
        )
    return {
        "aligned_baseline_series": aligned_baseline_series,
        "yoy_series": yoy_series,
    }


def _sort_segment_payloads(
    payloads: list[dict[str, Any]], *, dimensions: list[str], value_field: str
) -> None:
    payloads.sort(
        key=lambda item: (
            -(item[value_field] if item[value_field] is not None else float("-inf")),
            *[str((item.get("keys") or {}).get(dimension, "")) for dimension in dimensions],
        )
    )


def _build_segmented_yoy_payloads(
    *,
    rows: list[dict[str, Any]],
    dimensions: list[str],
    resolved_policy_summary: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    if resolved_policy_summary is None:
        return None

    segmented_yoy: list[dict[str, Any]] = []
    for row in rows:
        keys = {dimension: row.get(dimension) for dimension in dimensions if dimension in row}
        segmented_yoy.append(
            {
                "keys": keys,
                "current_value": _coerce_numeric_or_none(row.get("current_value")),
                "baseline_value": _coerce_numeric_or_none(row.get("baseline_value")),
                "absolute_delta": _coerce_numeric_or_none(row.get("absolute_delta")),
                "relative_delta": _coerce_numeric_or_none(row.get("relative_delta")),
            }
        )

    _sort_segment_payloads(segmented_yoy, dimensions=dimensions, value_field="current_value")
    return segmented_yoy


def _build_data_coverage_summary(
    *,
    series: list[dict[str, Any]],
    aligned_yoy_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected_bucket_count = len(series)
    present_bucket_count = sum(1 for point in series if point.get("value") is not None)
    summary: dict[str, Any] = {
        "expected_bucket_count": expected_bucket_count,
        "present_bucket_count": present_bucket_count,
        "missing_bucket_count": expected_bucket_count - present_bucket_count,
        "coverage_ratio": (
            present_bucket_count / expected_bucket_count if expected_bucket_count else 0.0
        ),
    }
    if aligned_yoy_series is None:
        return summary

    aligned_expected_bucket_count = len(aligned_yoy_series)
    aligned_present_current_bucket_count = sum(
        1 for point in aligned_yoy_series if point.get("current_value") is not None
    )
    aligned_present_baseline_bucket_count = sum(
        1 for point in aligned_yoy_series if point.get("baseline_value") is not None
    )
    aligned_present_both_bucket_count = sum(
        1
        for point in aligned_yoy_series
        if point.get("current_value") is not None and point.get("baseline_value") is not None
    )
    summary.update(
        {
            "aligned_expected_bucket_count": aligned_expected_bucket_count,
            "aligned_present_current_bucket_count": aligned_present_current_bucket_count,
            "aligned_present_baseline_bucket_count": aligned_present_baseline_bucket_count,
            "aligned_present_both_bucket_count": aligned_present_both_bucket_count,
        }
    )
    return summary


def _time_series_data_complete(summary: Mapping[str, Any] | None) -> bool | None:
    if summary is None:
        return None
    missing_bucket_count = summary.get("missing_bucket_count")
    expected_bucket_count = summary.get("expected_bucket_count")
    if not isinstance(missing_bucket_count, int) or not isinstance(expected_bucket_count, int):
        return None
    if missing_bucket_count > 0:
        return False
    if expected_bucket_count > 0:
        return True
    return None


def _time_series_quality_status(*, row_count: int, data_complete: bool | None) -> str:
    if row_count <= 0:
        return "not_ready"
    if data_complete is False:
        return "needs_attention"
    return "ready"


def _build_scoped_query_for_window(
    runtime: MarivoRuntime,
    *,
    session_id: str,
    engine_type: str,
    metric_ref: str,
    table: str,
    start: str,
    end: str,
    grain: str,
    scope_raw: Any,
    all_dimensions: list[str],
) -> dict[str, Any]:
    mq_params: dict[str, Any] = {
        "table": table,
        "metric": metric_ref,
        "time_scope": {
            "mode": "single_window",
            "grain": grain,
            "current": {"start": start, "end": end},
        },
    }
    if scope_raw:
        mq_params["scope"] = scope_raw
    resolved = normalize_metric_query_request(mq_params)
    runtime.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_ref,
        fallback_columns=all_dimensions,
    )
    return runtime.build_scoped_query(session_id, resolved, engine_type=engine_type)


def run_observe_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute an `observe` intent, producing a typed observation artifact.

    Supported modes (result_mode='standard'):
      - scalar: no granularity, no dimensions
      - time_series: granularity set (hour/day/week/month)
      - segmented: dimensions list set

    Supported time_scope kinds:
      - range: explicit [start, end) bounds
      - snapshot_now: resolved to today's UTC date range
      - latest_available: resolved to today's UTC date range (v1 approximation)
      - as_of: resolved to a single-day range around the given timestamp

    Inferential summary modes (numeric_sample_summary, rate_sample_summary) are
    not yet implemented.
    """
    p = params or {}

    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("observe intent requires 'time_scope'")

    result_mode: str = p.get("result_mode") or "standard"
    if result_mode not in {"standard", "numeric_sample_summary", "rate_sample_summary"}:
        raise ValueError(
            f"observe result_mode='{result_mode}' is not valid. "
            "Must be one of: 'standard', 'numeric_sample_summary', 'rate_sample_summary'."
        )

    try:
        normalized_calendar_policy_ref = validate_and_normalize_calendar_policy_ref(
            p.get("calendar_policy_ref")
        )
    except ValueError as exc:
        raise ValueError(f"observe: {exc}") from exc

    granularity = validate_granularity(p.get("granularity") or None)
    dimensions = normalize_dimensions(p.get("dimensions"))

    if granularity is not None and dimensions is not None:
        raise ValueError(
            "observe: granularity and dimensions cannot both be set. "
            "Use granularity for time_series mode or dimensions for segmented mode, not both."
        )
    if result_mode != "standard" and granularity is not None:
        raise ValueError(
            f"observe: granularity is not allowed with result_mode='{result_mode}'. "
            "Inferential summary modes do not support granularity."
        )
    if result_mode != "standard" and dimensions is not None:
        raise ValueError(
            f"observe: dimensions is not allowed with result_mode='{result_mode}'. "
            "Inferential summary modes do not support dimensions."
        )

    # --- Resolve time scope → (start_str, end_str, resolved response shape) ---
    start_str, end_str, time_scope_field = _resolve_observe_time_scope(time_scope_raw)
    resolved_time_scope: dict[str, Any] = {"kind": "range", "start": start_str, "end": end_str}

    if granularity == "hour":
        validate_hour_boundaries(
            granularity,
            str(time_scope_raw.get("start") or ""),
            str(time_scope_raw.get("end") or ""),
        )

    if granularity == "hour":
        grain = "hour"
    elif granularity is not None:
        grain = "day"
    else:
        grain = (
            "hour"
            if ("T" in start_str or (" " in start_str and ":" in start_str.split(" ", 1)[-1]))
            else "day"
        )

    execution_context = runtime.resolve_metric_execution_context(metric_ref, session_id=session_id)
    table = execution_context.table_name

    scope_raw = p.get("scope")
    mq_params: dict[str, Any] = {
        "table": table,
        "metric": metric_ref,
        "time_scope": {
            "mode": "single_window",
            "grain": grain,
            "current": {"start": start_str, "end": end_str},
        },
    }
    if scope_raw:
        mq_params["scope"] = scope_raw
    if time_scope_field:
        mq_params["time_scope_field"] = time_scope_field
    if dimensions:
        mq_params["dimensions"] = dimensions
    if normalized_calendar_policy_ref is not None:
        mq_params["calendar_policy_ref"] = normalized_calendar_policy_ref

    resolved = normalize_metric_query_request(mq_params)
    all_dimensions = runtime.resolve_metric_dimensions(metric_ref)
    engine_resolution = runtime.resolve_engine_for_session(session_id, [resolved.table])
    if not isinstance(engine_resolution, tuple) or len(engine_resolution) != 3:
        engine_resolution = runtime.resolve_engine([resolved.table])
    engine, engine_type, qualified = engine_resolution
    runtime.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_ref,
        fallback_columns=all_dimensions,
    )
    metric_sql = runtime.resolve_metric_sql_for_execution(
        metric_ref,
        execution_context,
        engine_type=engine_type,
    )
    if all_dimensions is None:
        raise ValueError(f"Metric '{metric_name}' not found or not published")
    scoped_query = _build_scoped_query_for_window(
        runtime,
        session_id=session_id,
        engine_type=engine_type,
        metric_ref=metric_ref,
        table=table,
        start=start_str,
        end=end_str,
        grain=grain,
        scope_raw=scope_raw,
        all_dimensions=all_dimensions,
    )
    qualified_table = qualified.get(resolved.table, resolved.table)
    step_id = new_step_id()
    now = datetime.now(UTC).isoformat()

    if result_mode == "numeric_sample_summary":
        # --- Numeric Sample Summary mode ---
        metric_value_sql = runtime.resolve_metric_value_sql_for_execution(
            metric_ref, execution_context
        )
        if metric_value_sql is None:
            raise ValueError(
                f"Metric '{metric_name}' cannot produce a per-row numeric value expression"
            )

        # metric_value_sql is used as a per-row value expression (not an outer aggregate).
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": qualified_table,
                    "time_scope": mq_params["time_scope"],
                    "calendar_policy_ref": normalized_calendar_policy_ref,
                    "select": [
                        "COUNT(*) AS n",
                        f"AVG({metric_value_sql}) AS mean",
                        f"VARIANCE({metric_value_sql}) AS variance",
                        f"STDDEV_SAMP({metric_value_sql}) AS std",
                        f"MIN({metric_value_sql}) AS min_val",
                        f"MAX({metric_value_sql}) AS max_val",
                    ],
                    "group_by": [],
                    "scoped_query": scoped_query,
                    "limit": 1,
                },
            ),
            engine_type=engine_type,
            semantic_context={"metric_execution_context": execution_context},
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        resolved_policy_summary_ns = _resolved_policy_summary_from_compiled(compiled_query)
        predicate_filter_lineage_ns = _extract_predicate_filter_lineage(compiled_query)

        n_numeric: int = 0
        mean_val: float | None = None
        variance_val: float | None = None
        std_val: float | None = None
        min_val: float | None = None
        max_val: float | None = None
        if rows:
            _row = rows[0]
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("n") is not None:
                    n_numeric = int(_row["n"])
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("mean") is not None:
                    mean_val = float(_row["mean"])
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("variance") is not None:
                    variance_val = float(_row["variance"])
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("std") is not None:
                    std_val = float(_row["std"])
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("min_val") is not None:
                    min_val = float(_row["min_val"])
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("max_val") is not None:
                    max_val = float(_row["max_val"])

        quality_status_ns = "ready" if n_numeric > 0 else "not_ready"
        observation_ns: dict[str, Any] = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "numeric_sample_summary",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "calendar_policy_ref": normalized_calendar_policy_ref,
            "resolved_policy_summary": resolved_policy_summary_ns,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_ns,
            "unit": None,
            "sample_summary": {
                "n": n_numeric,
                "mean": mean_val,
                "variance": variance_val,
                "standard_deviation": std_val,
                "min": min_val,
                "max": max_val,
            },
            "analytical_metadata": {
                "additivity_constraints": execution_context.additivity_constraints,
                "aggregation_semantics": None,
                "timezone": None,
                "data_complete": None,
                "quality_status": quality_status_ns,
                "row_count": n_numeric,
                "sample_size": n_numeric,
                "null_rate": None,
            },
            "execution_metadata": {
                "query_hash": provenance.get("query_hash", ""),
                "engine": engine_type,
                "executed_at": now,
            },
        }
        artifact_name_ns = f"{metric_name}_observe_numeric_summary"
        summary_ns = (
            f"observe {metric_name} numeric_sample_summary [{start_str} → {end_str}]: n={n_numeric}"
        )
        result_ns = commit_step_result(
            runtime,
            session_id,
            step_id,
            "observe",
            "observation",
            artifact_name_ns,
            observation_ns,
            summary_ns,
            provenance=provenance,
            semantic_metadata=_build_step_metadata(compiled_query),
        )
        return result_ns

    if result_mode == "rate_sample_summary":
        # --- Rate Sample Summary mode ---
        metric_value_sql = runtime.resolve_metric_value_sql_for_execution(
            metric_ref, execution_context
        )
        if metric_value_sql is None:
            raise ValueError(
                f"Metric '{metric_name}' cannot produce a per-row rate value expression"
            )

        # metric_value_sql is treated as a per-row 0/1 binary expression (rate numerator).
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": qualified_table,
                    "time_scope": mq_params["time_scope"],
                    "calendar_policy_ref": normalized_calendar_policy_ref,
                    "select": [
                        "COUNT(*) AS n",
                        f"SUM(CAST(({metric_value_sql}) AS DOUBLE)) AS k",
                    ],
                    "group_by": [],
                    "scoped_query": scoped_query,
                    "limit": 1,
                },
            ),
            engine_type=engine_type,
            semantic_context={"metric_execution_context": execution_context},
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        resolved_policy_summary_rs = _resolved_policy_summary_from_compiled(compiled_query)
        predicate_filter_lineage_rs = _extract_predicate_filter_lineage(compiled_query)

        n_rate: int = 0
        k_rate: float = 0.0
        if rows:
            _row = rows[0]
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("n") is not None:
                    n_rate = int(_row["n"])
            with contextlib.suppress(TypeError, ValueError):
                if _row.get("k") is not None:
                    k_rate = float(_row["k"])

        rate_val: float | None = k_rate / n_rate if n_rate > 0 else None
        quality_status_rs = "ready" if n_rate > 0 else "not_ready"
        observation_rs: dict[str, Any] = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "rate_sample_summary",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "calendar_policy_ref": normalized_calendar_policy_ref,
            "resolved_policy_summary": resolved_policy_summary_rs,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_rs,
            "unit": None,
            "sample_summary": {
                "successes": round(k_rate),
                "trials": n_rate,
                "rate": rate_val,
            },
            "analytical_metadata": {
                "additivity_constraints": execution_context.additivity_constraints,
                "aggregation_semantics": None,
                "timezone": None,
                "data_complete": None,
                "quality_status": quality_status_rs,
                "row_count": n_rate,
                "sample_size": n_rate,
                "null_rate": None,
            },
            "execution_metadata": {
                "query_hash": provenance.get("query_hash", ""),
                "engine": engine_type,
                "executed_at": now,
            },
        }
        artifact_name_rs = f"{metric_name}_observe_rate_summary"
        summary_rs = (
            f"observe {metric_name} rate_sample_summary "
            f"[{start_str} → {end_str}]: k={round(k_rate)} / n={n_rate}"
        )
        result_rs = commit_step_result(
            runtime,
            session_id,
            step_id,
            "observe",
            "observation",
            artifact_name_rs,
            observation_rs,
            summary_rs,
            provenance=provenance,
            semantic_metadata=_build_step_metadata(compiled_query),
        )
        return result_rs

    if granularity is not None:
        granularity_typed = cast("TimeGrain", granularity)
        # --- Time-series mode ---
        # Use aggregate_query select path: bucket alias is reliable across engines.
        time_col = resolved.resolved_time_axis.analysis_time_expr
        if not time_col:
            raise ValueError("windowed execution requires resolved_time_axis.analysis_time_expr")
        bucket_expr = f"DATE_TRUNC('{granularity}', {time_col})"
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": qualified_table,
                    "time_scope": mq_params["time_scope"],
                    "calendar_policy_ref": normalized_calendar_policy_ref,
                    "select": [
                        f"{bucket_expr} AS bucket_start",
                        f"{metric_sql} AS value",
                    ],
                    "group_by": ["bucket_start"],  # alias-expanded by compiler for Trino
                    "order_by": "bucket_start",
                    "scoped_query": scoped_query,
                    "limit": 1000,
                },
            ),
            engine_type=engine_type,
            semantic_context={"metric_execution_context": execution_context},
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        sparse_series = _series_from_rows(rows, granularity=granularity_typed)
        series = _build_dense_series(
            sparse_series=sparse_series,
            start=start_str,
            end=end_str,
            granularity=granularity_typed,
        )
        resolved_policy_summary = _resolved_policy_summary_from_compiled(compiled_query)
        predicate_filter_lineage_ts = _extract_predicate_filter_lineage(compiled_query)
        if normalized_calendar_policy_ref is not None and resolved_policy_summary is None:
            raise ValueError(
                "observe: INVALID_ARGUMENT - calendar_policy_ref did not resolve frozen calendar alignment metadata"
            )

        aligned_series_payload: dict[str, list[dict[str, Any]]] = {}
        if resolved_policy_summary is not None and granularity == "day":
            baseline_window = _require_mapping(
                resolved_policy_summary.get("baseline_window"),
                label="resolved_policy_summary.baseline_window",
            )
            baseline_start = str(baseline_window.get("start") or "")
            baseline_end = str(baseline_window.get("end") or "")
            baseline_time_scope = {
                "mode": "single_window",
                "grain": grain,
                "current": {
                    "start": baseline_start,
                    "end": baseline_end,
                },
            }
            baseline_scoped_query = _build_scoped_query_for_window(
                runtime,
                session_id=session_id,
                engine_type=engine_type,
                metric_ref=metric_ref,
                table=table,
                start=baseline_start,
                end=baseline_end,
                grain=grain,
                scope_raw=scope_raw,
                all_dimensions=all_dimensions,
            )
            baseline_compiled_query = runtime.compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="aggregate_query",
                    params={
                        "table": qualified_table,
                        "time_scope": baseline_time_scope,
                        "select": [
                            f"{bucket_expr} AS bucket_start",
                            f"{metric_sql} AS value",
                        ],
                        "group_by": ["bucket_start"],
                        "order_by": "bucket_start",
                        "scoped_query": baseline_scoped_query,
                        "limit": 1000,
                    },
                ),
                engine_type=engine_type,
                semantic_context={"metric_execution_context": execution_context},
            )
            baseline_rows = list(execute_compiled(engine, baseline_compiled_query).rows)
            baseline_sparse_series = _series_from_rows(baseline_rows, granularity=granularity_typed)
            baseline_series = _build_dense_series(
                sparse_series=baseline_sparse_series,
                start=baseline_start,
                end=baseline_end,
                granularity=granularity_typed,
            )
            aligned_series_payload = _build_aligned_time_series_payloads(
                current_series=series,
                baseline_series=baseline_series,
                resolved_policy_summary=resolved_policy_summary,
                granularity=granularity,
            )
        data_coverage_summary = _build_data_coverage_summary(
            series=series,
            aligned_yoy_series=aligned_series_payload.get("yoy_series"),
        )
        if resolved_policy_summary is not None:
            resolved_policy_summary = {
                **resolved_policy_summary,
                "data_coverage_summary": data_coverage_summary,
            }

        data_complete = _time_series_data_complete(data_coverage_summary)
        quality_status = _time_series_quality_status(
            row_count=len(rows),
            data_complete=data_complete,
        )
        observation: dict[str, Any] = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "time_series",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "calendar_policy_ref": normalized_calendar_policy_ref,
            "resolved_policy_summary": resolved_policy_summary,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_ts,
            "unit": None,
            "granularity": granularity,
            "series": series,
            "analytical_metadata": {
                "additivity_constraints": execution_context.additivity_constraints,
                "aggregation_semantics": "sum",
                "timezone": None,
                "data_complete": data_complete,
                "quality_status": quality_status,
                "row_count": len(rows),
                "sample_size": len(rows),
                "null_rate": None,
            },
            "execution_metadata": {
                "query_hash": provenance.get("query_hash", ""),
                "engine": engine_type,
                "executed_at": now,
            },
        }
        observation.update(aligned_series_payload)
        artifact_name = f"{metric_name}_observe_time_series"
        summary = (
            f"observe {metric_name} time_series/{granularity} "
            f"[{start_str} → {end_str}]: {len(series)} buckets"
        )

    elif dimensions:
        # --- Segmented mode ---
        # metric_query single_window with dimensions generates GROUP BY on dimension cols
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": qualified_table,
                    "metric": metric_name,
                    "time_scope": mq_params["time_scope"],
                    "calendar_policy_ref": normalized_calendar_policy_ref,
                    "scoped_query": scoped_query,
                },
            ),
            engine_type=engine_type,
            semantic_context={
                "metric_sql": metric_sql,
                "dimensions": dimensions,
                "metric_execution_context": execution_context,
            },
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        resolved_policy_summary = _resolved_policy_summary_from_compiled(compiled_query)
        predicate_filter_lineage_seg = _extract_predicate_filter_lineage(compiled_query)
        if normalized_calendar_policy_ref is not None and resolved_policy_summary is None:
            raise ValueError(
                "observe: INVALID_ARGUMENT - calendar_policy_ref did not resolve frozen calendar alignment metadata"
            )

        segments: list[dict[str, Any]] = []
        for row in rows:
            raw_value = row.get("current_value")
            seg_value: float | None = None
            with contextlib.suppress(TypeError, ValueError):
                if raw_value is not None:
                    seg_value = float(raw_value)
            keys = {dim: row.get(dim) for dim in dimensions if dim in row}
            segments.append({"keys": keys, "value": seg_value, "share": None})

        _sort_segment_payloads(segments, dimensions=dimensions, value_field="value")
        segmented_yoy = _build_segmented_yoy_payloads(
            rows=rows,
            dimensions=dimensions,
            resolved_policy_summary=resolved_policy_summary,
        )
        quality_status = "ready" if rows else "not_ready"
        observation = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "segmented",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "calendar_policy_ref": normalized_calendar_policy_ref,
            "resolved_policy_summary": resolved_policy_summary,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_seg,
            "unit": None,
            "dimensions": dimensions,
            "segments": segments,
            "scope_value": None,
            "analytical_metadata": {
                "additivity_constraints": execution_context.additivity_constraints,
                "aggregation_semantics": "sum",
                "timezone": None,
                "data_complete": None,
                "quality_status": quality_status,
                "row_count": len(rows),
                "sample_size": len(rows),
                "null_rate": None,
            },
            "execution_metadata": {
                "query_hash": provenance.get("query_hash", ""),
                "engine": engine_type,
                "executed_at": now,
            },
        }
        if segmented_yoy is not None:
            observation["segmented_yoy"] = segmented_yoy
        artifact_name = f"{metric_name}_observe_segmented"
        summary = (
            f"observe {metric_name} segmented [{start_str} → {end_str}]: {len(segments)} segments"
        )

    else:
        # --- Scalar mode ---
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": qualified_table,
                    "metric": metric_name,
                    "time_scope": mq_params["time_scope"],
                    "calendar_policy_ref": normalized_calendar_policy_ref,
                    "scoped_query": scoped_query,
                },
            ),
            engine_type=engine_type,
            semantic_context={
                "metric_sql": metric_sql,
                "dimensions": [],
                "metric_execution_context": execution_context,
            },
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        predicate_filter_lineage_scalar = _extract_predicate_filter_lineage(compiled_query)

        value: float | None = None
        sample_size: int | None = None
        if rows:
            row = rows[0]
            raw_value = row.get("current_value")
            if raw_value is not None:
                with contextlib.suppress(TypeError, ValueError):
                    value = float(raw_value)
            raw_sessions = row.get("current_sessions")
            if raw_sessions is not None:
                with contextlib.suppress(TypeError, ValueError):
                    sample_size = int(raw_sessions)

        quality_status = "ready" if rows else "not_ready"
        observation = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "scalar",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "calendar_policy_ref": normalized_calendar_policy_ref,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_scalar,
            "unit": None,
            "analytical_metadata": {
                "additivity_constraints": execution_context.additivity_constraints,
                "aggregation_semantics": "sum",
                "timezone": None,
                "data_complete": None,
                "quality_status": quality_status,
                "row_count": sample_size,
                "sample_size": sample_size,
                "null_rate": None,
            },
            "execution_metadata": {
                "query_hash": provenance.get("query_hash", ""),
                "engine": engine_type,
                "executed_at": now,
            },
            "value": value,
        }
        artifact_name = f"{metric_name}_observe_scalar"
        summary = (
            f"observe {metric_name} [{start_str} → {end_str}]: "
            f"{value if value is not None else 'no data'}"
        )

    if "resolved_policy_summary" not in observation:
        observation["resolved_policy_summary"] = _resolved_policy_summary_from_compiled(
            compiled_query
        )

    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "observe",
        "observation",
        artifact_name,
        observation,
        summary,
        provenance=provenance,
        semantic_metadata=build_step_semantic_metadata(compiled_query),
    )
    return result
