from __future__ import annotations

import contextlib
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from marivo.core.intent.primitives import make_provenance, new_step_id
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.core.semantic.step_metadata import build_step_semantic_metadata
from marivo.runtime.evidence.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from marivo.runtime.intents._helpers import (
    aoi_filter_to_scope,
    build_scoped_query_for_window,
    commit_step_result,
    extract_predicate_filter_lineage,
    resolve_time_scope,
)
from marivo.runtime.intents.normalization import (
    normalize_dimensions,
    normalize_metric_ref,
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
    elif granularity == "quarter":
        quarter_start_month = ((current_date.month - 1) // 3) * 3 + 1
        current_date = current_date.replace(month=quarter_start_month, day=1)
    elif granularity == "year":
        current_date = current_date.replace(month=1, day=1)
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
    elif granularity == "month":
        year = current_date.year + (1 if current_date.month == 12 else 0)
        month = 1 if current_date.month == 12 else current_date.month + 1
        current_date = date(year, month, 1)
    elif granularity == "quarter":
        year = current_date.year + ((current_date.month - 1 + 3) // 12)
        month = ((current_date.month - 1 + 3) % 12) + 1
        current_date = date(year, month, 1)
    else:
        current_date = date(current_date.year + 1, 1, 1)
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


def _sort_segment_payloads(
    payloads: list[dict[str, Any]], *, dimensions: list[str], value_field: str
) -> None:
    payloads.sort(
        key=lambda item: (
            -(item[value_field] if item[value_field] is not None else float("-inf")),
            *[str((item.get("keys") or {}).get(dimension, "")) for dimension in dimensions],
        )
    )


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


def run_observe_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute an `observe` intent, producing a typed observation artifact.

    Output modes (inferred from granularity/dimensions):
      - scalar: no granularity, no dimensions
      - time_series: granularity set (hour/day/week/month/quarter/year)
      - segmented: dimensions list set

    Supported time_scope kinds:
      - range: explicit [start, end) bounds
    """
    p = params or {}
    if "calendar_policy_ref" in p:
        raise ValueError(
            "observe: INVALID_ARGUMENT - calendar_policy_ref is no longer supported; "
            "use compare.compare_type for calendar alignment"
        )

    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("observe intent requires 'time_scope'")

    granularity = validate_granularity(p.get("granularity") or None)
    dimensions = normalize_dimensions(p.get("dimensions"))

    if granularity is not None and dimensions is not None:
        raise ValueError(
            "observe: granularity and dimensions cannot both be set. "
            "Use granularity for time_series mode or dimensions for segmented mode, not both."
        )

    # --- Resolve time scope → (start_str, end_str, resolved response shape) ---
    start_str, end_str, time_scope_field = resolve_time_scope(time_scope_raw)
    resolved_time_scope: dict[str, Any] = {"kind": "range", "start": start_str, "end": end_str}
    if time_scope_field:
        resolved_time_scope["field"] = time_scope_field

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
    resolved_metric = runtime.resolve_metric(metric_name)
    _resolved_header = (
        (resolved_metric.semantic_object.get("header") or {}) if resolved_metric else {}
    )
    aggregation_semantics = _resolved_header.get("aggregation_semantics") or "sum"
    table = execution_context.table_name

    scope_raw = p.get("scope")
    if not scope_raw:
        try:
            scope_raw = aoi_filter_to_scope(p.get("filter"), label="filter")
        except ValueError as exc:
            raise ValueError(f"observe: INVALID_ARGUMENT - {exc}") from exc
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
    scoped_query = build_scoped_query_for_window(
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
        rows = list(execute_compiled(engine, compiled_query, session_id=session_id).rows)
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
        predicate_filter_lineage_ts = extract_predicate_filter_lineage(compiled_query)
        data_coverage_summary = _build_data_coverage_summary(series=series)

        data_complete = _time_series_data_complete(data_coverage_summary)
        quality_status = _time_series_quality_status(
            row_count=len(rows),
            data_complete=data_complete,
        )
        observation = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "time_series",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_ts,
            "unit": None,
            "granularity": granularity,
            "series": series,
            "analytical_metadata": {
                "additive_dimensions": execution_context.additive_dimensions,
                "aggregation_semantics": aggregation_semantics,
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
        artifact_name = f"{metric_name}_observe_time_series"
        summary = (
            f"observe {metric_name} time_series/{granularity} "
            f"[{start_str} → {end_str}]: {len(series)} buckets"
        )

    elif dimensions:
        # --- Segmented mode ---
        # metric_query single_window with dimensions generates GROUP BY on dimension cols
        step_params_seg: dict[str, Any] = {
            "table": qualified_table,
            "metric": metric_name,
            "time_scope": mq_params["time_scope"],
            "scoped_query": scoped_query,
        }
        if time_scope_field:
            step_params_seg["time_scope_field"] = time_scope_field
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params=step_params_seg,
            ),
            engine_type=engine_type,
            semantic_context={
                "metric_sql": metric_sql,
                "dimensions": dimensions,
                "metric_execution_context": execution_context,
            },
        )
        rows = list(execute_compiled(engine, compiled_query, session_id=session_id).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        predicate_filter_lineage_seg = extract_predicate_filter_lineage(compiled_query)

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
        quality_status = "ready" if rows else "not_ready"
        observation = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "segmented",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_seg,
            "unit": None,
            "dimensions": dimensions,
            "segments": segments,
            "scope_value": None,
            "analytical_metadata": {
                "additive_dimensions": execution_context.additive_dimensions,
                "aggregation_semantics": aggregation_semantics,
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
        artifact_name = f"{metric_name}_observe_segmented"
        summary = (
            f"observe {metric_name} segmented [{start_str} → {end_str}]: {len(segments)} segments"
        )

    else:
        # --- Scalar mode ---
        step_params_scalar: dict[str, Any] = {
            "table": qualified_table,
            "metric": metric_name,
            "time_scope": mq_params["time_scope"],
            "scoped_query": scoped_query,
        }
        if time_scope_field:
            step_params_scalar["time_scope_field"] = time_scope_field
        compiled_query = runtime.compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params=step_params_scalar,
            ),
            engine_type=engine_type,
            semantic_context={
                "metric_sql": metric_sql,
                "dimensions": [],
                "metric_execution_context": execution_context,
            },
        )
        rows = list(execute_compiled(engine, compiled_query, session_id=session_id).rows)
        provenance = make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        predicate_filter_lineage_scalar = extract_predicate_filter_lineage(compiled_query)

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
            "scope": scope_raw or {},
            "predicate_filter_lineage": predicate_filter_lineage_scalar,
            "unit": None,
            "analytical_metadata": {
                "additive_dimensions": execution_context.additive_dimensions,
                "aggregation_semantics": aggregation_semantics,
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
