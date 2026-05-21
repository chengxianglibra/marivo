from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.decompose_strategies import dispatch_decomposition_strategy
from marivo.runtime.intents.metric_frame import (
    has_dimension_axis,
    has_time_axis,
    read_axes_from_artifact,
)
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_AOI_PARAM_KEYS: frozenset[str] = frozenset({"compare_artifact_id", "dimension", "limit"})


def run_decompose_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute a `decompose` intent: attribute a compare delta across a dimension.

    Input:
      compare_artifact_id: committed scalar_delta or time_series_delta compare artifact id
      dimension:           single semantic dimension to decompose over
      limit:               optional max returned contribution rows

    Output: committed delta_decomposition artifact.

    Empty semantics: fails (NOT_ATTRIBUTABLE) if no contribution rows can be formed.
    """
    p = params or {}
    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; decompose accepts only AOI request fields"
        )

    compare_artifact_id_raw = p.get("compare_artifact_id")
    compare_artifact_id = (
        compare_artifact_id_raw.strip() if isinstance(compare_artifact_id_raw, str) else ""
    )
    compare_step_id: str = ""
    dimension: str = (p.get("dimension") or "").strip()
    limit_raw = p.get("limit")
    limit: int | None = None
    if limit_raw is not None:
        limit = int(limit_raw)
        if limit <= 0:
            raise ValueError("decompose: INVALID_ARGUMENT - limit must be > 0")

    # ── Input validation ──────────────────────────────────────────────────────
    if not compare_artifact_id:
        raise ValueError("decompose: INVALID_ARGUMENT - compare_artifact_id is required")

    if not dimension:
        raise ValueError("decompose: INVALID_ARGUMENT - dimension is required")

    # ── Resolve compare artifact ──────────────────────────────────────────────
    compare_artifact = runtime.resolve_artifact_by_id(session_id, compare_artifact_id)
    if compare_artifact is None:
        raise ValueError(
            "decompose: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"compare_artifact_id '{compare_artifact_id}'"
        )

    normalized_compare = _normalize_decompose_compare_input(compare_artifact)
    comparison_type: str = normalized_compare["comparison_type"]

    # ── Extract metadata from compare artifact ────────────────────────────────
    metric_name: str = normalized_compare["metric_name"]
    unit: str | None = normalized_compare["unit"]
    scope_current_value: float | None = normalized_compare["scope_current_value"]
    scope_baseline_value: float | None = normalized_compare["scope_baseline_value"]
    scope_absolute_delta: float | None = normalized_compare["scope_absolute_delta"]
    scope_relative_delta: float | None = normalized_compare["scope_relative_delta"]
    scope_direction: str = normalized_compare["scope_direction"]
    source_observation_type: str = normalized_compare["source_observation_type"]
    source_analytical_metadata: dict[str, Any] = normalized_compare["analytical_metadata"]
    lineage_info: dict[str, Any] = compare_artifact.get("lineage") or {}
    current_source_ref: dict[str, Any] = lineage_info.get("current_source_ref") or {}
    baseline_source_ref: dict[str, Any] = lineage_info.get("baseline_source_ref") or {}
    left_obs_step_id: str = current_source_ref.get("step_id") or ""
    right_obs_step_id: str = baseline_source_ref.get("step_id") or ""

    if not left_obs_step_id or not right_obs_step_id:
        raise ValueError(
            "decompose: STEP_NOT_FOUND - compare artifact lineage is missing upstream "
            "observe step IDs; cannot form canonical observation refs"
        )

    resolved_input: dict[str, Any] = compare_artifact.get("resolved_input_summary") or {}
    current_time_scope: dict[str, Any] = normalized_compare["current_time_scope"]
    baseline_time_scope: dict[str, Any] = normalized_compare["baseline_time_scope"]
    current_scope: dict[str, Any] = resolved_input.get("current_scope") or {}
    baseline_scope: dict[str, Any] = resolved_input.get("baseline_scope") or {}

    # ── Validate metric and dimension ────────────────────────────────────────
    resolved_metric = runtime.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"decompose: metric '{metric_name}' not found or not published")

    _resolved_header = resolved_metric.semantic_object.get("header") or {}
    metric_aggregation_semantics = _resolved_header.get("aggregation_semantics") or "sum"

    runtime_dimensions = runtime.resolve_metric_dimensions(metric_name) or []
    valid_dimensions = (
        runtime_dimensions or resolved_metric.allowed_dimensions or list(resolved_metric.dimensions)
    )
    if not valid_dimensions:
        raise ValueError(f"decompose: metric '{metric_name}' declares no dimensions")
    if dimension not in valid_dimensions:
        raise ValueError(
            f"decompose: UNSUPPORTED_DIMENSION - '{dimension}' is not declared for "
            f"metric '{metric_name}'. Available: {sorted(valid_dimensions)}"
        )

    all_dimensions = list(runtime_dimensions or resolved_metric.dimensions)

    table = runtime.resolve_metric_table(metric_name, session_id=session_id)
    if table is None:
        raise ValueError(f"decompose: metric '{metric_name}' has no source table mapping")

    # ── Engine resolution ─────────────────────────────────────────────────────
    engine_resolution = runtime.resolve_engine_for_session(session_id, [table])
    if not isinstance(engine_resolution, tuple) or len(engine_resolution) != 3:
        engine_resolution = runtime.resolve_engine([table])
    engine, engine_type, qualified = engine_resolution
    metric_sql = runtime.resolve_metric_sql_for_execution(metric_name, engine_type=engine_type)
    qualified_table = qualified.get(table, table)

    # ── Fetch artifact IDs for canonical refs ─────────────────────────────────
    left_obs_artifact_id: str | None = runtime.resolve_artifact_id_for_step(
        session_id, left_obs_step_id
    )
    right_obs_artifact_id: str | None = runtime.resolve_artifact_id_for_step(
        session_id, right_obs_step_id
    )

    # ── Execute segmented queries for left and right scopes ───────────────────
    left_rows, left_sql, left_query_hash, left_elapsed_ms = _run_segmented_query(
        runtime,
        session_id,
        metric_name,
        metric_sql,
        qualified_table,
        dimension,
        all_dimensions,
        current_time_scope,
        current_scope,
        engine,
        engine_type,
        table_name=table,
    )
    right_rows, right_sql, _, right_elapsed_ms = _run_segmented_query(
        runtime,
        session_id,
        metric_name,
        metric_sql,
        qualified_table,
        dimension,
        all_dimensions,
        baseline_time_scope,
        baseline_scope,
        engine,
        engine_type,
        table_name=table,
    )

    now = datetime.now(UTC).isoformat()
    execution_metadata: dict[str, Any] = {
        "query_hash": left_query_hash,
        "engine": engine_type,
        "executed_at": now,
    }

    _sql_texts: list[dict[str, str | float]] = []
    if left_elapsed_ms is not None:
        _sql_texts.append(
            {
                "sql": left_sql or "(current segmented query)",
                "engine_type": engine_type,
                "label": "current_query",
                "elapsed_ms": left_elapsed_ms,
            }
        )
    if right_elapsed_ms is not None:
        _sql_texts.append(
            {
                "sql": right_sql or "(baseline segmented query)",
                "engine_type": engine_type,
                "label": "baseline_query",
                "elapsed_ms": right_elapsed_ms,
            }
        )

    # ── Build decomposition via strategy dispatcher ──────────────────────────────
    left_map: dict[Any, float | None] = {
        row.get(dimension): _safe_float(row.get("current_value")) for row in left_rows
    }
    right_map: dict[Any, float | None] = {
        row.get(dimension): _safe_float(row.get("current_value")) for row in right_rows
    }

    decomp = dispatch_decomposition_strategy(
        aggregation_semantics=metric_aggregation_semantics,
        left_map=left_map,
        right_map=right_map,
        scope_absolute_delta=scope_absolute_delta,
        dimension=dimension,
    )

    rows = decomp.rows
    returned_rows = rows[:limit] if limit is not None else rows
    unexplained_absolute_delta = decomp.unexplained_absolute_delta
    unexplained_share = decomp.unexplained_share
    unexplained_reason = decomp.unexplained_reason
    issues = decomp.issues
    attribution_status = (
        "needs_attention" if any(i["severity"] == "error" for i in issues) else "attributable"
    )

    # ── Build v2.0 axes+series from decomposition rows ──────────────────────────
    decompose_axes: list[dict[str, str]] = [{"kind": "dimension", "name": dimension}]
    decompose_series: list[dict[str, Any]] = [
        {
            "keys": {dimension: row["key"]},
            "points": [{k: v for k, v in row.items() if k != "key"}],
        }
        for row in returned_rows
    ]

    # ── Build artifact ────────────────────────────────────────────────────────
    step_id = new_step_id()

    current_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_obs_step_id,
        "artifact_id": left_obs_artifact_id,
        "observation_type": source_observation_type,
    }
    baseline_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_obs_step_id,
        "artifact_id": right_obs_artifact_id,
        "observation_type": source_observation_type,
    }
    compare_ref_out: dict[str, Any] = {
        "step_type": "compare",
        "session_id": session_id,
        "step_id": compare_step_id,
        "artifact_id": compare_artifact_id,
        "comparison_type": comparison_type,
    }

    artifact: dict[str, Any] = {
        "schema_version": "2.0",
        "decomposition_type": "delta_decomposition",
        "metric": metric_name,
        "compare_ref": compare_ref_out,
        "current_ref": current_ref_out,
        "baseline_ref": baseline_ref_out,
        "axes": decompose_axes,
        "series": decompose_series,
        # Backward-compatible aliases for downstream intents during v2.0 transition
        "dimension": dimension,
        "rows": returned_rows,
        "method": decomp.method,
        "unit": unit,
        "current_time_scope": current_time_scope,
        "baseline_time_scope": baseline_time_scope,
        "resolved_scopes": {
            "current": current_scope,
            "baseline": baseline_scope,
        },
        "scope_current_value": scope_current_value,
        "scope_baseline_value": scope_baseline_value,
        "scope_absolute_delta": scope_absolute_delta,
        "scope_relative_delta": scope_relative_delta,
        "scope_direction": scope_direction,
        "attribution": {"status": attribution_status, "issues": issues},
        "unexplained_absolute_delta": unexplained_absolute_delta,
        "unexplained_share": unexplained_share,
        "unexplained_reason": unexplained_reason,
        "analytical_metadata": {
            "method": decomp.method,
            "aggregation_semantics": metric_aggregation_semantics,
            "reconciliation_status": decomp.quality.reconciliation_status,
            "reconciliation_gap": decomp.quality.reconciliation_gap,
            "confidence_grade": decomp.quality.confidence_grade,
            "confidence_rationale": decomp.quality.confidence_rationale,
            "recommended_use": decomp.quality.recommended_use,
            "flat_tolerance_relative": 0.01,
            "current_row_count": len(left_rows),
            "baseline_row_count": len(right_rows),
            "returned_row_count": len(returned_rows),
            **source_analytical_metadata,
            "time_boundary_constraint": {
                "scope": "frozen_compare_window",
                "time_rollup_implied": False,
            },
        },
        "source_lineage": {
            "compare_artifact": compare_ref_out,
            "current_artifact": current_ref_out,
            "baseline_artifact": baseline_ref_out,
        },
        "execution_metadata": execution_metadata,
    }

    artifact_name = f"{metric_name}_decompose_{dimension}"
    summary = (
        f"decompose {metric_name} by {dimension}: "
        f"{len(decompose_series)} series entries "
        f"(scope Δ {scope_absolute_delta if scope_absolute_delta is not None else 'n/a'})"
    )

    provenance: dict[str, Any] = {
        "compare_artifact_id": compare_artifact_id,
        "dimension": dimension,
        "limit": limit,
    }
    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "decompose",
        "delta_decomposition",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
        reasoning=reasoning,
        sql_texts=_sql_texts or None,
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_decompose_compare_input(compare_artifact: dict[str, Any]) -> dict[str, Any]:
    """Normalize a compare artifact for decompose input.

    Reads from v2.0 (axes+series) format with fallback to legacy top-level fields.
    """
    axes = read_axes_from_artifact(compare_artifact)
    comparison_type = compare_artifact.get("comparison_type", "")

    # Determine observation type from axes (v2.0 canonical)
    if has_time_axis(axes) and has_dimension_axis(axes):
        source_observation_type = "panel"
    elif has_time_axis(axes):
        source_observation_type = "time_series"
    elif has_dimension_axis(axes):
        source_observation_type = "segmented"
    else:
        source_observation_type = "scalar"

    # Infer comparison_type from axes if not explicitly set (v2.0 artifacts always
    # include comparison_type, but this makes the normalizer robust for edge cases)
    if not comparison_type:
        if source_observation_type == "scalar":
            comparison_type = "scalar_delta"
        elif source_observation_type == "time_series":
            comparison_type = "time_series_delta"
        elif source_observation_type == "segmented":
            comparison_type = "segmented_delta"

    resolved_input: dict[str, Any] = compare_artifact.get("resolved_input_summary") or {}

    if comparison_type == "scalar_delta":
        # Read from v2.0 series format; fall back to top-level aliases for v1.0 compat
        series_list = compare_artifact.get("series") or []
        points = (series_list[0].get("points") or []) if series_list else []
        if points:
            point = points[0]
            scope_current_value = _safe_float(point.get("current_value"))
            scope_baseline_value = _safe_float(point.get("baseline_value"))
            scope_absolute_delta = _safe_float(point.get("delta"))
            scope_relative_delta = _safe_float(point.get("delta_pct"))
            scope_direction = point.get("direction") or "undefined"
        else:
            # v1.0 fallback: read from top-level fields
            scope_current_value = _safe_float(compare_artifact.get("current_value"))
            scope_baseline_value = _safe_float(compare_artifact.get("baseline_value"))
            scope_absolute_delta = _safe_float(compare_artifact.get("absolute_delta"))
            scope_relative_delta = _safe_float(compare_artifact.get("relative_delta"))
            scope_direction = compare_artifact.get("direction") or "undefined"

        return {
            "comparison_type": "scalar_delta",
            "metric_name": compare_artifact.get("metric") or "",
            "unit": compare_artifact.get("unit"),
            "scope_current_value": scope_current_value,
            "scope_baseline_value": scope_baseline_value,
            "scope_absolute_delta": scope_absolute_delta,
            "scope_relative_delta": scope_relative_delta,
            "scope_direction": scope_direction,
            "source_observation_type": source_observation_type,
            "current_time_scope": dict(resolved_input.get("current_time_scope") or {}),
            "baseline_time_scope": dict(resolved_input.get("baseline_time_scope") or {}),
            "analytical_metadata": {"decomposition_source": "scalar_delta"},
        }

    if comparison_type == "time_series_delta":
        analytical = compare_artifact.get("analytical_metadata") or {}
        current_time_scope = dict(resolved_input.get("current_time_scope") or {})
        baseline_time_scope = dict(resolved_input.get("baseline_time_scope") or {})
        matched_current_time_scope = analytical.get("matched_current_time_scope")
        matched_baseline_time_scope = analytical.get("matched_baseline_time_scope")
        matched_time_scope = analytical.get("matched_time_scope")
        if isinstance(matched_current_time_scope, dict) and matched_current_time_scope:
            current_time_scope = dict(matched_current_time_scope)
        elif isinstance(matched_time_scope, dict) and matched_time_scope:
            current_time_scope = dict(matched_time_scope)
        if isinstance(matched_baseline_time_scope, dict) and matched_baseline_time_scope:
            baseline_time_scope = dict(matched_baseline_time_scope)
        elif isinstance(matched_time_scope, dict) and matched_time_scope:
            baseline_time_scope = dict(matched_time_scope)

        # Read summary values from series points aggregation (v2.0 canonical).
        # Aggregate matched-pair current/baseline values, then compute delta.
        # Fall back to top-level summary_* fields if series is absent or empty.
        series_list = compare_artifact.get("series") or []
        series_points = (series_list[0].get("points") or []) if series_list else []
        matched_current_values: list[float] = []
        matched_baseline_values: list[float] = []
        for point in series_points:
            presence = point.get("presence")
            cv = _safe_float(point.get("current_value"))
            bv = _safe_float(point.get("baseline_value"))
            if (
                (presence == "both" or (cv is not None and bv is not None))
                and cv is not None
                and bv is not None
            ):
                matched_current_values.append(cv)
                matched_baseline_values.append(bv)

        if matched_current_values:
            scope_current_value = sum(matched_current_values)
        else:
            scope_current_value = _safe_float(compare_artifact.get("summary_current_value"))

        if matched_baseline_values:
            scope_baseline_value = sum(matched_baseline_values)
        else:
            scope_baseline_value = _safe_float(compare_artifact.get("summary_baseline_value"))

        if scope_current_value is not None and scope_baseline_value is not None:
            scope_absolute_delta = scope_current_value - scope_baseline_value
            if scope_baseline_value != 0:
                scope_relative_delta = scope_absolute_delta / scope_baseline_value
            else:
                scope_relative_delta = None
        else:
            scope_absolute_delta = _safe_float(compare_artifact.get("summary_absolute_delta"))
            scope_relative_delta = _safe_float(compare_artifact.get("summary_relative_delta"))

        flat_tolerance = 0.01
        if scope_absolute_delta is not None:
            if scope_absolute_delta == 0 or (
                scope_relative_delta is not None and abs(scope_relative_delta) <= flat_tolerance
            ):
                scope_direction = "flat"
            elif scope_absolute_delta > 0:
                scope_direction = "increase"
            else:
                scope_direction = "decrease"
        else:
            scope_direction = compare_artifact.get("summary_direction") or "undefined"

        # Derive granularity from axes
        granularity = None
        for ax in axes:
            if ax.get("kind") == "time":
                granularity = ax.get("grain")

        return {
            "comparison_type": "time_series_delta",
            "metric_name": compare_artifact.get("metric") or "",
            "unit": compare_artifact.get("unit"),
            "scope_current_value": scope_current_value,
            "scope_baseline_value": scope_baseline_value,
            "scope_absolute_delta": scope_absolute_delta,
            "scope_relative_delta": scope_relative_delta,
            "scope_direction": scope_direction,
            "source_observation_type": source_observation_type,
            "current_time_scope": current_time_scope,
            "baseline_time_scope": baseline_time_scope,
            "analytical_metadata": {
                "decomposition_source": "time_series_summary_delta",
                "source_granularity": granularity or compare_artifact.get("granularity"),
                "source_matched_bucket_count": analytical.get("matched_bucket_count"),
                "source_dropped_current_buckets": analytical.get("dropped_current_buckets"),
                "source_dropped_baseline_buckets": analytical.get("dropped_baseline_buckets"),
                "source_pairing_basis": analytical.get("pairing_basis"),
                "source_pairing_rule": analytical.get("pairing_rule"),
            },
        }

    raise ValueError(
        "decompose: INVALID_ARGUMENT - compare_artifact_id must point to a scalar_delta or "
        f"time_series_delta artifact, got '{comparison_type}'"
    )


def _run_segmented_query(
    runtime: MarivoRuntime,
    session_id: str,
    metric_name: str,
    metric_sql: str,
    qualified_table: str,
    dimension: str,
    all_dimensions: list[str],
    time_scope: dict[str, Any],
    scope: dict[str, Any],
    engine: Any,
    engine_type: str,
    table_name: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, str | None, float | None]:
    """Run a single segmented metric query for one time scope.

    Returns (rows, sql_text, query_hash, elapsed_ms) where sql_text is the
    translated SQL, query_hash is an MD5 of the translated SQL, and elapsed_ms
    is the query execution duration in milliseconds.
    """
    start_str, end_str = _extract_date_range(time_scope)

    mq_params: dict[str, Any] = {
        "table": table_name or qualified_table,
        "metric": metric_name,
        "time_scope": {
            "mode": "single_window",
            "boundary_mode": "exact",
            "current": {"start": start_str, "end": end_str},
        },
        "dimensions": [dimension],
    }
    if scope:
        mq_params["scope"] = scope
    time_scope_field = str(time_scope.get("field") or "").strip()
    if time_scope_field:
        mq_params["time_scope_field"] = time_scope_field

    resolved = normalize_metric_query_request(mq_params)
    runtime.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_name,
        fallback_columns=all_dimensions,
    )
    scoped_query = runtime.build_scoped_query(session_id, resolved, engine_type=engine_type)
    compiled_query = runtime.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="metric_query",
            params={
                "table": qualified_table,
                "metric": metric_name,
                "time_scope": mq_params["time_scope"],
                **({"time_scope_field": time_scope_field} if time_scope_field else {}),
                "scoped_query": scoped_query,
            },
        ),
        engine_type=engine_type,
        semantic_context={"metric_sql": metric_sql, "dimensions": [dimension]},
    )
    result = execute_compiled(engine, compiled_query, session_id=session_id)
    sql = result.metadata.get("translated_sql") or ""
    query_hash: str | None = hashlib.md5(sql.encode()).hexdigest() if sql else None
    elapsed_ms = result.metadata.get("elapsed_ms")
    return list(result.rows), sql, query_hash, elapsed_ms


def _extract_date_range(time_scope: dict[str, Any]) -> tuple[str, str]:
    """Extract (start_str, end_str) from a canonical time_scope dict."""
    if "start" in time_scope and "end" in time_scope:
        return time_scope["start"], time_scope["end"]
    raise ValueError("decompose: cannot extract date range from time_scope without start/end")


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    with contextlib.suppress(TypeError, ValueError):
        return float(v)
    return None
