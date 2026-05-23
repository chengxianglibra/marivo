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
    build_attribution_frame_artifact,
    dimension_names_from_axes,
    has_dimension_axis,
    has_time_axis,
    is_delta_frame_artifact,
    read_axes_from_artifact,
    read_delta_frame_shape,
)
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_AOI_PARAM_KEYS: frozenset[str] = frozenset({"compare_artifact_id", "dimension", "limit"})
_SUPPORTED_DELTA_FRAME_SHAPES: frozenset[str] = frozenset(
    {"scalar_delta", "time_series_delta", "segmented_delta", "panel_delta"}
)


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

    Output: committed attribution_frame artifact.

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

    normalized_compare = _normalize_decompose_compare_input(
        compare_artifact,
        requested_dimension=dimension,
    )
    comparison_shape: str = normalized_compare["shape"]

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
    fast_path_rows: list[dict[str, Any]] | None = normalized_compare["fast_path_rows"]
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

    current_time_scope: dict[str, Any] = normalized_compare["current_time_scope"]
    baseline_time_scope: dict[str, Any] = normalized_compare["baseline_time_scope"]
    current_scope: dict[str, Any] = normalized_compare["current_scope"]
    baseline_scope: dict[str, Any] = normalized_compare["baseline_scope"]

    # ── Validate metric and dimension ────────────────────────────────────────
    resolved_metric = runtime.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"decompose: metric '{metric_name}' not found or not published")

    _resolved_header = resolved_metric.semantic_object.get("header") or {}
    metric_decomposition_semantics = _resolved_header.get("decomposition_semantics") or "sum"

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

    if fast_path_rows is not None:
        left_rows: list[dict[str, Any]] = []
        right_rows: list[dict[str, Any]] = []
        left_sql = None
        right_sql = None
        left_query_hash = None
        left_elapsed_ms = None
        right_elapsed_ms = None
        left_map: dict[Any, float | None] = {}
        right_map: dict[Any, float | None] = {}
        for row in fast_path_rows:
            key = row.get("key")
            current_value = _safe_float(row.get("current_value"))
            baseline_value = _safe_float(row.get("baseline_value"))
            if current_value is not None:
                left_map[key] = current_value
            if baseline_value is not None:
                right_map[key] = baseline_value
    else:
        # ── Execute segmented queries for left and right scopes ───────────────
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
        left_map = {row.get(dimension): _safe_float(row.get("current_value")) for row in left_rows}
        right_map = {
            row.get(dimension): _safe_float(row.get("current_value")) for row in right_rows
        }

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
    decomp = dispatch_decomposition_strategy(
        decomposition_semantics=metric_decomposition_semantics,
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

    attribution_series = _attribution_series_from_rows(returned_rows, dimension=dimension)

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
        "shape": comparison_shape,
    }

    scope_payload = {
        "current_value": scope_current_value,
        "baseline_value": scope_baseline_value,
        "delta_abs": scope_absolute_delta,
        "delta_pct": scope_relative_delta,
        "direction": scope_direction,
    }
    quality_payload = {
        "reconciliation_status": decomp.quality.reconciliation_status,
        "reconciliation_gap": decomp.quality.reconciliation_gap,
        "confidence_grade": decomp.quality.confidence_grade,
        "unexplained_delta_abs": unexplained_absolute_delta,
        "unexplained_pct": unexplained_share,
        "unexplained_reason": unexplained_reason,
    }
    subject = {
        "kind": "comparison",
        "metric_ref": f"metric.{metric_name}",
        "current": {"time_scope": current_time_scope, "scope": current_scope},
        "baseline": {"time_scope": baseline_time_scope, "scope": baseline_scope},
    }
    artifact_frame = build_attribution_frame_artifact(
        artifact_id="",
        metric_ref=f"metric.{metric_name}",
        dimension=dimension,
        subject=subject,
        series=attribution_series,
        scope=scope_payload,
        quality=quality_payload,
        lineage={
            "operation": "decompose",
            "source_artifact_ids": [compare_artifact_id],
        },
    )
    artifact_frame.pop("artifact_id", None)
    artifact_frame.pop("metric_ref", None)
    artifact: dict[str, Any] = {
        **artifact_frame,
        "schema_version": "2.0",
        "metric": metric_name,
        "compare_ref": compare_ref_out,
        "current_ref": current_ref_out,
        "baseline_ref": baseline_ref_out,
        "method": decomp.method,
        "unit": unit,
        "current_time_scope": current_time_scope,
        "baseline_time_scope": baseline_time_scope,
        "resolved_scopes": {
            "current": current_scope,
            "baseline": baseline_scope,
        },
        "attribution": {"status": attribution_status, "issues": issues},
        "unexplained_absolute_delta": unexplained_absolute_delta,
        "unexplained_share": unexplained_share,
        "unexplained_reason": unexplained_reason,
        "analytical_metadata": {
            "method": decomp.method,
            "decomposition_semantics": metric_decomposition_semantics,
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
            "delta_frame": compare_ref_out,
            "current_artifact": current_ref_out,
            "baseline_artifact": baseline_ref_out,
        },
        "execution_metadata": execution_metadata,
    }

    artifact_name = f"{metric_name}_decompose_{dimension}"
    summary = (
        f"decompose {metric_name} by {dimension}: "
        f"{len(attribution_series)} series entries "
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
        "attribution_frame",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
        reasoning=reasoning,
        sql_texts=_sql_texts or None,
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _attribution_series_from_rows(
    rows: list[dict[str, Any]],
    *,
    dimension: str,
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for rank_0, row in enumerate(rows):
        key = row.get("key")
        contribution_abs = _safe_float(row.get("absolute_contribution"))
        if contribution_abs is None:
            raise ValueError(
                "decompose: INVALID_ARTIFACT - attribution rows require numeric "
                "absolute_contribution"
            )
        point: dict[str, Any] = {
            "contribution_abs": contribution_abs,
            "contribution_pct": _safe_float(row.get("contribution_share")),
            "current_value": row.get("current_value"),
            "baseline_value": row.get("baseline_value"),
            "rank": rank_0 + 1,
        }
        presence = row.get("presence")
        if presence in {"both", "current_only", "baseline_only"}:
            point["presence"] = presence
        series.append(
            {
                "keys": {dimension: key},
                "points": [point],
            }
        )
    return series


def _require_delta_frame_source(
    source_artifact: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    if not is_delta_frame_artifact(source_artifact):
        raise ValueError("decompose: INVALID_ARGUMENT - source artifact must be delta_frame")
    shape = read_delta_frame_shape(source_artifact)
    if shape not in _SUPPORTED_DELTA_FRAME_SHAPES:
        raise ValueError(
            f"decompose: INVALID_ARGUMENT - delta_frame shape '{shape}' is not supported"
        )
    capabilities = source_artifact.get("capabilities") or []
    if "decomposable" not in capabilities:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame source requires decomposable capability"
        )
    axes = _read_required_axes_for_source(source_artifact)
    _validate_delta_axes(shape, axes)
    _read_required_payload_series_for_source(source_artifact)
    _read_required_payload_scope_for_source(source_artifact)
    return shape, axes


def _read_required_axes_for_source(source_artifact: dict[str, Any]) -> list[dict[str, str]]:
    axes_raw = source_artifact.get("axes", [])
    if not isinstance(axes_raw, list):
        raise ValueError("decompose: INVALID_ARGUMENT - delta_frame axes must be a list")
    axes: list[dict[str, str]] = []
    for axis in axes_raw:
        if not isinstance(axis, dict):
            raise ValueError(
                "decompose: INVALID_ARGUMENT - delta_frame axes entries must be objects"
            )
        axes.append(axis)
    return axes


def _read_required_payload_series_for_source(
    source_artifact: dict[str, Any],
) -> list[dict[str, Any]]:
    payload = source_artifact.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame payload must be an object with payload.series"
        )
    series_raw = payload.get("series")
    if not isinstance(series_raw, list) or not series_raw:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame payload.series must be a non-empty list"
        )
    series: list[dict[str, Any]] = []
    for entry in series_raw:
        if not isinstance(entry, dict):
            raise ValueError(
                "decompose: INVALID_ARGUMENT - delta_frame payload.series entries must be objects"
            )
        points = entry.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError(
                "decompose: INVALID_ARGUMENT - delta_frame series entry points must be a non-empty list"
            )
        series.append(entry)
    return series


def _read_required_payload_scope_for_source(source_artifact: dict[str, Any]) -> dict[str, Any]:
    payload = source_artifact.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame payload must be an object with payload.scope"
        )
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame payload.scope must be an object"
        )
    required_fields = {
        "current_value",
        "baseline_value",
        "delta_abs",
        "delta_pct",
        "direction",
    }
    missing_fields = sorted(required_fields - set(scope))
    if missing_fields:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame payload.scope missing field(s): "
            f"{missing_fields}"
        )
    return scope


def _validate_delta_axes(shape: str, axes: list[dict[str, str]]) -> None:
    time_axes = [axis for axis in axes if axis.get("kind") == "time"]
    dimension_axes = [axis for axis in axes if axis.get("kind") == "dimension"]
    comparison_axes = [axis for axis in axes if axis.get("kind") == "comparison_side"]
    unknown_axes = [
        axis.get("kind")
        for axis in axes
        if axis.get("kind") not in {"time", "dimension", "comparison_side"}
    ]
    if unknown_axes:
        raise ValueError("decompose: INVALID_ARGUMENT - delta_frame axes contain unsupported kind")
    if len(comparison_axes) > 1:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame axes must contain at most one comparison_side"
        )
    if any(not axis.get("name") for axis in dimension_axes):
        raise ValueError("decompose: INVALID_ARGUMENT - dimension axis must declare name")
    structural_axis_count = len(time_axes) + len(dimension_axes)
    if shape == "scalar_delta" and structural_axis_count != 0:
        raise ValueError("decompose: INVALID_ARGUMENT - scalar_delta must not declare data axes")
    if shape == "time_series_delta" and (structural_axis_count != 1 or len(time_axes) != 1):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - time_series_delta requires exactly one time axis"
        )
    if shape == "segmented_delta" and (structural_axis_count != 1 or len(dimension_axes) != 1):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - segmented_delta requires exactly one dimension axis"
        )
    if shape == "panel_delta" and (
        structural_axis_count != 2 or len(time_axes) != 1 or len(dimension_axes) != 1
    ):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - panel_delta requires exactly one time axis and one dimension axis"
        )


def _comparison_subject_scopes(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    subject = artifact.get("subject") or {}
    current = subject.get("current") or {}
    baseline = subject.get("baseline") or {}
    resolved_input = artifact.get("resolved_input_summary") or {}
    return (
        dict(current.get("time_scope") or resolved_input.get("current_time_scope") or {}),
        dict(baseline.get("time_scope") or resolved_input.get("baseline_time_scope") or {}),
        dict(current.get("scope") or resolved_input.get("current_scope") or {}),
        dict(baseline.get("scope") or resolved_input.get("baseline_scope") or {}),
    )


def _unit_from_measures(artifact: dict[str, Any]) -> str | None:
    measures = artifact.get("measures") or []
    for measure in measures:
        if isinstance(measure, dict) and measure.get("id") == "delta_abs":
            unit = measure.get("unit")
            return str(unit) if unit is not None else None
    unit = artifact.get("unit")
    return str(unit) if unit is not None else None


def _series_complete(artifact: dict[str, Any]) -> bool:
    analytical = artifact.get("analytical_metadata") or {}
    return bool(analytical.get("series_complete") is True)


def _fast_path_rows_for_delta_frame(
    *,
    artifact: dict[str, Any],
    shape: str,
    requested_dimension: str | None,
) -> tuple[str | None, list[dict[str, Any]] | None]:
    axes = read_axes_from_artifact(artifact)
    dimensions = dimension_names_from_axes(axes)
    if not requested_dimension or dimensions != [requested_dimension]:
        return None, None
    if shape not in {"segmented_delta", "panel_delta"}:
        return None, None
    if not _series_complete(artifact):
        return requested_dimension, None
    if shape == "segmented_delta":
        return requested_dimension, _segmented_fast_path_rows(artifact, requested_dimension)
    return requested_dimension, _panel_fast_path_rows(artifact, requested_dimension)


def _delta_abs_from_point(point: dict[str, Any]) -> float | None:
    if "delta_abs" in point:
        delta_abs = _safe_float(point.get("delta_abs"))
        if delta_abs is not None:
            return delta_abs
    if "delta" in point:
        delta = _safe_float(point.get("delta"))
        if delta is not None:
            return delta
    current_value = _safe_float(point.get("current_value"))
    baseline_value = _safe_float(point.get("baseline_value"))
    if current_value is not None and baseline_value is not None:
        return current_value - baseline_value
    return None


def _presence_from_values(current_value: float | None, baseline_value: float | None) -> str:
    if current_value is not None and baseline_value is not None:
        return "both"
    if current_value is not None:
        return "current_only"
    if baseline_value is not None:
        return "baseline_only"
    return "undefined"


def _segmented_fast_path_rows(artifact: dict[str, Any], dimension: str) -> list[dict[str, Any]]:
    rows = []
    for entry in _read_required_payload_series_for_source(artifact):
        keys = entry.get("keys") or {}
        key = keys.get(dimension)
        point = ((entry.get("points") or [{}])[0]) or {}
        current_value = _safe_float(point.get("current_value"))
        baseline_value = _safe_float(point.get("baseline_value"))
        rows.append(
            {
                "key": key,
                "current_value": current_value,
                "baseline_value": baseline_value,
                "absolute_contribution": _delta_abs_from_point(point),
                "presence": point.get("presence")
                or _presence_from_values(current_value, baseline_value),
            }
        )
    return rows


def _panel_fast_path_rows(artifact: dict[str, Any], dimension: str) -> list[dict[str, Any]]:
    rows = []
    for entry in _read_required_payload_series_for_source(artifact):
        keys = entry.get("keys") or {}
        key = keys.get(dimension)
        current_values: list[float] = []
        baseline_values: list[float] = []
        delta_values: list[float] = []
        for point in entry.get("points") or []:
            current_value = _safe_float(point.get("current_value"))
            baseline_value = _safe_float(point.get("baseline_value"))
            delta_abs = _delta_abs_from_point(point)
            if current_value is not None:
                current_values.append(current_value)
            if baseline_value is not None:
                baseline_values.append(baseline_value)
            if delta_abs is not None:
                delta_values.append(delta_abs)
        current_total = sum(current_values) if current_values else None
        baseline_total = sum(baseline_values) if baseline_values else None
        absolute_contribution = sum(delta_values) if delta_values else None
        rows.append(
            {
                "key": key,
                "current_value": current_total,
                "baseline_value": baseline_total,
                "absolute_contribution": absolute_contribution,
                "presence": _presence_from_values(current_total, baseline_total),
            }
        )
    return rows


def _normalize_decompose_compare_input(
    compare_artifact: dict[str, Any],
    *,
    requested_dimension: str | None = None,
) -> dict[str, Any]:
    """Normalize a compare artifact for decompose input.

    Reads from canonical delta_frame payload format.
    """
    shape, axes = _require_delta_frame_source(compare_artifact)
    payload_scope = _read_required_payload_scope_for_source(compare_artifact)
    subject = compare_artifact.get("subject") or {}
    metric_ref = (
        compare_artifact.get("metric_ref")
        or subject.get("metric_ref")
        or compare_artifact.get("metric")
        or ""
    )
    metric_name = str(metric_ref).removeprefix("metric.")
    current_time_scope, baseline_time_scope, current_scope, baseline_scope = (
        _comparison_subject_scopes(compare_artifact)
    )

    # Determine observation type from axes (v2.0 canonical)
    if has_time_axis(axes) and has_dimension_axis(axes):
        source_observation_type = "panel"
    elif has_time_axis(axes):
        source_observation_type = "time_series"
    elif has_dimension_axis(axes):
        source_observation_type = "segmented"
    else:
        source_observation_type = "scalar"

    fast_path_dimension, fast_path_rows = _fast_path_rows_for_delta_frame(
        artifact=compare_artifact,
        shape=shape,
        requested_dimension=requested_dimension,
    )

    if shape == "scalar_delta":
        scope_current_value = _safe_float(payload_scope.get("current_value"))
        scope_baseline_value = _safe_float(payload_scope.get("baseline_value"))
        scope_absolute_delta = _safe_float(payload_scope.get("delta_abs"))
        scope_relative_delta = _safe_float(payload_scope.get("delta_pct"))
        scope_direction = payload_scope.get("direction") or "undefined"

        return {
            "shape": "scalar_delta",
            "metric_name": metric_name,
            "unit": _unit_from_measures(compare_artifact),
            "scope_current_value": scope_current_value,
            "scope_baseline_value": scope_baseline_value,
            "scope_absolute_delta": scope_absolute_delta,
            "scope_relative_delta": scope_relative_delta,
            "scope_direction": scope_direction,
            "source_observation_type": source_observation_type,
            "current_time_scope": current_time_scope,
            "baseline_time_scope": baseline_time_scope,
            "current_scope": current_scope,
            "baseline_scope": baseline_scope,
            "fast_path_dimension": fast_path_dimension,
            "fast_path_rows": fast_path_rows,
            "analytical_metadata": {"decomposition_source": "scalar_delta"},
        }

    if shape == "time_series_delta":
        analytical = compare_artifact.get("analytical_metadata") or {}
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

        scope_current_value = _safe_float(payload_scope.get("current_value"))
        scope_baseline_value = _safe_float(payload_scope.get("baseline_value"))
        scope_absolute_delta = _safe_float(payload_scope.get("delta_abs"))
        scope_relative_delta = _safe_float(payload_scope.get("delta_pct"))
        scope_direction = payload_scope.get("direction") or "undefined"

        # Derive granularity from axes
        granularity = None
        for ax in axes:
            if ax.get("kind") == "time":
                granularity = ax.get("grain")

        return {
            "shape": "time_series_delta",
            "metric_name": metric_name,
            "unit": _unit_from_measures(compare_artifact),
            "scope_current_value": scope_current_value,
            "scope_baseline_value": scope_baseline_value,
            "scope_absolute_delta": scope_absolute_delta,
            "scope_relative_delta": scope_relative_delta,
            "scope_direction": scope_direction,
            "source_observation_type": source_observation_type,
            "current_time_scope": current_time_scope,
            "baseline_time_scope": baseline_time_scope,
            "current_scope": current_scope,
            "baseline_scope": baseline_scope,
            "fast_path_dimension": fast_path_dimension,
            "fast_path_rows": fast_path_rows,
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

    if shape in {"segmented_delta", "panel_delta"}:
        scope_current_value = _safe_float(payload_scope.get("current_value"))
        scope_baseline_value = _safe_float(payload_scope.get("baseline_value"))
        scope_absolute_delta = _safe_float(payload_scope.get("delta_abs"))
        scope_relative_delta = _safe_float(payload_scope.get("delta_pct"))
        scope_direction = payload_scope.get("direction") or "undefined"
        analytical = compare_artifact.get("analytical_metadata") or {}
        granularity = None
        for ax in axes:
            if ax.get("kind") == "time":
                granularity = ax.get("grain")

        return {
            "shape": shape,
            "metric_name": metric_name,
            "unit": _unit_from_measures(compare_artifact),
            "scope_current_value": scope_current_value,
            "scope_baseline_value": scope_baseline_value,
            "scope_absolute_delta": scope_absolute_delta,
            "scope_relative_delta": scope_relative_delta,
            "scope_direction": scope_direction,
            "source_observation_type": source_observation_type,
            "current_time_scope": current_time_scope,
            "baseline_time_scope": baseline_time_scope,
            "current_scope": current_scope,
            "baseline_scope": baseline_scope,
            "fast_path_dimension": fast_path_dimension,
            "fast_path_rows": fast_path_rows,
            "analytical_metadata": {
                "decomposition_source": shape,
                "source_granularity": granularity or compare_artifact.get("granularity"),
                "source_matched_bucket_count": analytical.get("matched_bucket_count"),
            },
        }

    raise ValueError(
        "decompose: INVALID_ARGUMENT - compare_artifact_id must point to a scalar_delta or "
        f"time_series_delta artifact, got '{shape}'"
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
