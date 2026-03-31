from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR
from app.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from app.service import SemanticLayerService

_VALID_GRANULARITIES: frozenset[str] = frozenset({"hour", "day", "week", "month"})


def run_observe_intent(
    svc: SemanticLayerService, session_id: str, params: dict[str, Any] | None
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

    metric_name: str = p.get("metric") or ""
    if not metric_name:
        raise ValueError("observe intent requires 'metric'")

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("observe intent requires 'time_scope'")

    result_mode: str = p.get("result_mode") or "standard"
    if result_mode != "standard":
        raise NotImplementedError(
            f"observe result_mode='{result_mode}' is not yet implemented. "
            "Only 'standard' is supported in v1."
        )

    granularity: str | None = p.get("granularity") or None
    dimensions: list[str] | None = p.get("dimensions") or None

    if granularity is not None and granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            f"observe granularity='{granularity}' is not valid. "
            f"Must be one of: {sorted(_VALID_GRANULARITIES)}"
        )
    if granularity is not None and dimensions is not None:
        raise ValueError(
            "observe: granularity and dimensions cannot both be set. "
            "Use granularity for time_series mode or dimensions for segmented mode, not both."
        )

    # --- Resolve time scope kind → (start_str, end_str, resolved response shape) ---
    kind = time_scope_raw.get("kind")
    resolved_time_scope: dict[str, Any]
    if kind == "range":
        start_str: str = time_scope_raw["start"]
        end_str: str = time_scope_raw["end"]
        resolved_time_scope = {"kind": "range", "start": start_str, "end": end_str}
    elif kind == "snapshot_now":
        today = datetime.now(UTC).date()
        start_str = today.isoformat()
        end_str = (today + timedelta(days=1)).isoformat()
        resolved_time_scope = {"kind": "snapshot_now", "observed_at": start_str}
    elif kind == "latest_available":
        today = datetime.now(UTC).date()
        start_str = today.isoformat()
        end_str = (today + timedelta(days=1)).isoformat()
        resolved_time_scope = {"kind": "latest_available", "data_as_of": start_str}
    elif kind == "as_of":
        at_raw: str = time_scope_raw.get("at") or ""
        try:
            at_date = datetime.fromisoformat(at_raw).date()
        except ValueError:
            at_date = date.fromisoformat(at_raw[:10])
        start_str = at_date.isoformat()
        end_str = (at_date + timedelta(days=1)).isoformat()
        resolved_time_scope = {"kind": "as_of", "at": start_str}
    else:
        raise NotImplementedError(f"observe time_scope.kind='{kind}' is not yet implemented.")

    if granularity is not None and kind != "range":
        raise ValueError(
            f"observe: granularity is not allowed with time_scope.kind='{kind}'. "
            "granularity is only valid with kind='range'."
        )

    grain = (
        "hour"
        if ("T" in start_str or (" " in start_str and ":" in start_str.split(" ", 1)[-1]))
        else "day"
    )

    table = svc._resolve_metric_table(metric_name)
    if table is None:
        raise ValueError(
            f"Metric '{metric_name}' is not published or has no source table mapping. "
            "Ensure the metric exists in the semantic layer and is mapped to a source object."
        )

    scope_raw = p.get("scope")
    mq_params: dict[str, Any] = {
        "table": table,
        "metric": metric_name,
        "time_scope": {
            "mode": "single_window",
            "grain": grain,
            "current": {"start": start_str, "end": end_str},
        },
    }
    if scope_raw:
        mq_params["scope"] = scope_raw
    if dimensions:
        mq_params["dimensions"] = dimensions

    resolved = normalize_metric_query_request(mq_params)
    metric_sql = svc.resolve_metric_sql(metric_name)
    all_dimensions = svc.resolve_metric_dimensions(metric_name)
    if metric_sql is None or all_dimensions is None:
        raise ValueError(f"Metric '{metric_name}' not found or not published")

    short_name = resolved.table.split(".")[-1]
    engine, engine_type, qualified = svc._resolve_engine([short_name])
    svc._resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_name,
        fallback_columns=all_dimensions,
    )
    scoped_query = svc._build_scoped_query(session_id, resolved)
    qualified_table = qualified.get(short_name, resolved.table)
    step_id = svc._new_step_id()
    now = datetime.now(UTC).isoformat()

    if granularity is not None:
        # --- Time-series mode ---
        # Use aggregate_query select path: bucket alias is reliable across engines.
        time_col = resolved.resolved_time_axis.analysis_time_expr
        bucket_expr = f"DATE_TRUNC('{granularity}', {time_col})"
        compiled_query = svc._compile_step_with_feedback(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": qualified_table,
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
            semantic_context={},
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = svc._make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )

        series: list[dict[str, Any]] = []
        for row in rows:
            bucket_raw = row.get("bucket_start")
            raw_value = row.get("value")
            series_value: float | None = None
            with contextlib.suppress(TypeError, ValueError):
                if raw_value is not None:
                    series_value = float(raw_value)
            if bucket_raw is not None:
                bucket_str = str(bucket_raw)[:10]  # truncate to date
                try:
                    bucket_date = date.fromisoformat(bucket_str)
                    if granularity == "hour":
                        bucket_end = (
                            datetime.fromisoformat(str(bucket_raw)) + timedelta(hours=1)
                        ).isoformat()
                    elif granularity == "day":
                        bucket_end = (bucket_date + timedelta(days=1)).isoformat()
                    elif granularity == "week":
                        bucket_end = (bucket_date + timedelta(weeks=1)).isoformat()
                    elif granularity == "month":
                        if bucket_date.month == 12:
                            bucket_end = bucket_date.replace(
                                year=bucket_date.year + 1, month=1, day=1
                            ).isoformat()
                        else:
                            bucket_end = bucket_date.replace(
                                month=bucket_date.month + 1, day=1
                            ).isoformat()
                    else:
                        bucket_end = (bucket_date + timedelta(days=1)).isoformat()
                except (ValueError, TypeError):
                    bucket_end = bucket_str
                series.append(
                    {"window": {"start": bucket_str, "end": bucket_end}, "value": series_value}
                )

        quality_status = "ready" if rows else "not_ready"
        observation: dict[str, Any] = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "time_series",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "scope": scope_raw or {},
            "unit": None,
            "granularity": granularity,
            "series": series,
            "analytical_metadata": {
                "metric_additivity": "additive",
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
        artifact_name = f"{metric_name}_observe_time_series"
        summary = (
            f"observe {metric_name} time_series/{granularity} "
            f"[{start_str} → {end_str}]: {len(series)} buckets"
        )

    elif dimensions:
        # --- Segmented mode ---
        # metric_query single_window with dimensions generates GROUP BY on dimension cols
        compiled_query = svc._compile_step_with_feedback(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": qualified_table,
                    "metric": metric_name,
                    "scoped_query": scoped_query,
                },
            ),
            engine_type=engine_type,
            semantic_context={"metric_sql": metric_sql, "dimensions": dimensions},
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = svc._make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
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

        segments.sort(
            key=lambda s: (
                -(s["value"] if s["value"] is not None else float("-inf")),
                *[str(s["keys"].get(d, "")) for d in dimensions],
            )
        )
        quality_status = "ready" if rows else "not_ready"
        observation = {
            "schema_version": "1.0",
            "metric_contract_version": None,
            "derivation_version": "1.0",
            "observation_type": "segmented",
            "metric": metric_name,
            "time_scope": resolved_time_scope,
            "scope": scope_raw or {},
            "unit": None,
            "dimensions": dimensions,
            "segments": segments,
            "scope_value": None,
            "analytical_metadata": {
                "metric_additivity": "additive",
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
        artifact_name = f"{metric_name}_observe_segmented"
        summary = (
            f"observe {metric_name} segmented [{start_str} → {end_str}]: {len(segments)} segments"
        )

    else:
        # --- Scalar mode ---
        compiled_query = svc._compile_step_with_feedback(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "table": qualified_table,
                    "metric": metric_name,
                    "scoped_query": scoped_query,
                },
            ),
            engine_type=engine_type,
            semantic_context={"metric_sql": metric_sql, "dimensions": []},
        )
        rows = list(execute_compiled(engine, compiled_query).rows)
        provenance = svc._make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )

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
            "unit": None,
            "analytical_metadata": {
                "metric_additivity": "additive",
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

    artifact_id = svc._insert_artifact(
        session_id, step_id, "observation", artifact_name, observation
    )

    result: dict[str, Any] = {
        "intent_type": "observe",
        "step_type": "observe",
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": "observe",
        },
        "artifact_id": artifact_id,
        **observation,
    }

    svc._insert_step(step_id, session_id, "observe", summary, result, provenance=provenance)
    return result
