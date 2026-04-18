from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.intents.calendar_alignment_metadata import resolve_calendar_alignment_reuse_for_intent

if TYPE_CHECKING:
    from app.service import SemanticLayerService


_VALID_COMPARE_MODES = frozenset({"auto", "scalar", "segmented", "time_series"})


def _normalize_window(window: dict[str, Any]) -> tuple[str, str]:
    start = str(window.get("start") or "")
    end = str(window.get("end") or start)
    return start, end


def _series_row_key(row: dict[str, Any]) -> str:
    """Return the stable time-series bucket key as ``{start}|{end}``.

    Compare aligns buckets by their resolved window boundaries, so the combined
    start/end pair is the deterministic join key across left/right series.
    """
    window = row.get("window") or {}
    start, end = _normalize_window(window)
    if not start:
        raise ValueError("compare: INVALID_ARGUMENT - time_series row missing window.start")
    return f"{start}|{end}"


def _coerce_numeric_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_compare_intent(
    svc: SemanticLayerService, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `compare` intent: compute typed delta between two observe artifacts.

    Input: left_ref + right_ref (both ObservationRef pointing to committed observe artifacts).
    Output: committed compare_artifact (scalar_delta or segmented_delta).

    Empty semantics: hard-fails only on NOT_COMPARABLE (incompatible inputs); null values
    and empty segment sets produce data_incomplete issues with needs_attention status.
    """
    p = params or {}

    left_ref_raw: dict[str, Any] = p.get("left_ref") or {}
    right_ref_raw: dict[str, Any] = p.get("right_ref") or {}
    left_step_id: str = left_ref_raw.get("step_id") or ""
    right_step_id: str = right_ref_raw.get("step_id") or ""
    left_session_id: str = left_ref_raw.get("session_id") or session_id
    right_session_id: str = right_ref_raw.get("session_id") or session_id
    mode: str = p.get("mode") or "auto"
    if mode not in _VALID_COMPARE_MODES:
        raise ValueError(
            "compare: INVALID_ARGUMENT - mode must be one of "
            "'auto', 'scalar', 'segmented', 'time_series'"
        )

    if not left_step_id or not right_step_id:
        raise ValueError("compare: both left_ref.step_id and right_ref.step_id are required")

    # Validate step_type in refs — Pydantic enforces Literal["observe"] at the HTTP surface;
    # guard here for direct callers that bypass the HTTP layer.
    for _side, _ref_raw in (("left", left_ref_raw), ("right", right_ref_raw)):
        _ref_step_type = _ref_raw.get("step_type")
        if _ref_step_type is not None and _ref_step_type != "observe":
            raise ValueError(
                f"compare: INVALID_ARGUMENT - {_side}_ref.step_type must be 'observe', "
                f"got '{_ref_step_type}'"
            )

    # Resolve artifacts from DB
    left_artifact = svc._resolve_artifact_for_ref(left_session_id, left_step_id)
    if left_artifact is None:
        raise ValueError(
            f"compare: STEP_NOT_FOUND - no committed artifact for step '{left_step_id}'"
        )

    right_artifact = svc._resolve_artifact_for_ref(right_session_id, right_step_id)
    if right_artifact is None:
        raise ValueError(
            f"compare: STEP_NOT_FOUND - no committed artifact for step '{right_step_id}'"
        )

    left_obs_type: str | None = left_artifact.get("observation_type")
    right_obs_type: str | None = right_artifact.get("observation_type")
    left_metric: str | None = left_artifact.get("metric")
    right_metric: str | None = right_artifact.get("metric")

    # Collect comparability issues
    issues: list[dict[str, Any]] = []
    fatal = False

    if left_metric != right_metric:
        issues.append(
            {
                "code": "metric_mismatch",
                "severity": "error",
                "message": f"left metric '{left_metric}' != right metric '{right_metric}'",
            }
        )
        fatal = True

    if left_obs_type != right_obs_type:
        issues.append(
            {
                "code": "observation_type_mismatch",
                "severity": "error",
                "message": f"left observation_type '{left_obs_type}' != right '{right_obs_type}'",
            }
        )
        fatal = True

    if not fatal and left_obs_type == "segmented":
        left_dims = sorted(left_artifact.get("dimensions") or [])
        right_dims = sorted(right_artifact.get("dimensions") or [])
        if left_dims != right_dims:
            issues.append(
                {
                    "code": "dimension_mismatch",
                    "severity": "error",
                    "message": f"left dimensions {left_dims} != right dimensions {right_dims}",
                }
            )
            fatal = True

    if not fatal and left_obs_type == "time_series":
        left_granularity = left_artifact.get("granularity")
        right_granularity = right_artifact.get("granularity")
        if left_granularity is None or right_granularity is None:
            issues.append(
                {
                    "code": "granularity_mismatch",
                    "severity": "error",
                    "message": "time_series observations must include non-null granularity",
                }
            )
            fatal = True
        elif left_granularity != right_granularity:
            issues.append(
                {
                    "code": "granularity_mismatch",
                    "severity": "error",
                    "message": (
                        f"left granularity '{left_granularity}' != right '{right_granularity}'"
                    ),
                }
            )
            fatal = True

    left_unit: str | None = left_artifact.get("unit")
    right_unit: str | None = right_artifact.get("unit")
    if left_unit != right_unit:
        issues.append(
            {
                "code": "unit_mismatch",
                "severity": "error",
                "message": f"left unit '{left_unit}' != right unit '{right_unit}'",
            }
        )
        fatal = True

    # Resolve analytical metadata early so aggregation_semantics can be compared.
    left_am = left_artifact.get("analytical_metadata") or {}
    right_am = right_artifact.get("analytical_metadata") or {}
    left_agg: str | None = left_am.get("aggregation_semantics")
    right_agg: str | None = right_am.get("aggregation_semantics")
    if left_agg and right_agg and left_agg != right_agg:
        issues.append(
            {
                "code": "aggregation_mismatch",
                "severity": "error",
                "message": f"left aggregation_semantics '{left_agg}' != right '{right_agg}'",
            }
        )
        fatal = True

    calendar_alignment_summary = resolve_calendar_alignment_reuse_for_intent(
        intent_name="compare",
        left_resolved_policy_summary=left_artifact.get("resolved_policy_summary"),
        right_resolved_policy_summary=right_artifact.get("resolved_policy_summary"),
    )
    issues.extend(calendar_alignment_summary["issues"])
    if calendar_alignment_summary["fatal_message"] is not None:
        fatal = True

    if fatal:
        fatal_message = calendar_alignment_summary["fatal_message"] or issues[0]["message"]
        raise ValueError(f"compare: NOT_COMPARABLE - {fatal_message}")

    # Explicit mode guard
    if mode == "scalar" and left_obs_type != "scalar":
        raise ValueError(
            f"compare: INVALID_ARGUMENT - mode='scalar' but observation_type is '{left_obs_type}'"
        )
    if mode == "segmented" and left_obs_type != "segmented":
        raise ValueError(
            f"compare: INVALID_ARGUMENT - mode='segmented' but observation_type is '{left_obs_type}'"
        )
    if mode == "time_series" and left_obs_type != "time_series":
        raise ValueError(
            "compare: INVALID_ARGUMENT - mode='time_series' but observation_type is "
            f"'{left_obs_type}'"
        )

    # Pre-flight null check for scalar: both null means all delta fields will be null.
    # This is valid per spec (delta fields become null); surface as data_incomplete.
    if (
        left_obs_type == "scalar"
        and left_artifact.get("value") is None
        and right_artifact.get("value") is None
    ):
        issues.append(
            {
                "code": "data_incomplete",
                "severity": "warning",
                "message": "both left and right scalar values are null; delta fields will be null",
            }
        )

    comparability_status = "needs_attention" if issues else "comparable"
    comparability: dict[str, Any] = {"status": comparability_status, "issues": issues}

    # Build shared metadata
    metric_name: str = left_metric or ""
    step_id = svc._new_step_id()
    now = datetime.now(UTC).isoformat()
    flat_tolerance_relative = 0.01

    left_ref_out = {
        "session_id": left_session_id,
        "step_id": left_step_id,
        "step_type": "observe",
    }
    right_ref_out = {
        "session_id": right_session_id,
        "step_id": right_step_id,
        "step_type": "observe",
    }
    lineage: dict[str, Any] = {
        "left_source_ref": left_ref_out,
        "right_source_ref": right_ref_out,
        "observation_schema_version": left_artifact.get("schema_version"),
        "derivation_version": "1.0",
    }
    resolved_input_summary: dict[str, Any] = {
        "left_time_scope": left_artifact.get("time_scope"),
        "right_time_scope": right_artifact.get("time_scope"),
        "left_scope": left_artifact.get("scope") or {},
        "right_scope": right_artifact.get("scope") or {},
    }
    if calendar_alignment_summary["reuse_summary"] is not None:
        resolved_input_summary["calendar_alignment"] = calendar_alignment_summary["reuse_summary"]
    analytical_metadata: dict[str, Any] = {
        "aggregation_semantics": left_am.get("aggregation_semantics", "sum"),
        "metric_additivity": left_am.get("metric_additivity", "additive"),
        "relative_delta_denominator": "right",
        "flat_tolerance_relative": flat_tolerance_relative,
        "left_row_count": left_am.get("row_count"),
        "right_row_count": right_am.get("row_count"),
    }
    execution_metadata: dict[str, Any] = {
        "query_hash": None,
        "engine": "service",
        "executed_at": now,
    }
    base: dict[str, Any] = {
        "artifact_type": "compare_artifact",
        "schema_version": "1.0",
        "metric": metric_name,
        "left_ref": left_ref_out,
        "right_ref": right_ref_out,
        "lineage": lineage,
        "resolved_input_summary": resolved_input_summary,
        "unit": left_unit,
        "comparability": comparability,
        "analytical_metadata": analytical_metadata,
        "execution_metadata": execution_metadata,
    }

    artifact: dict[str, Any]
    artifact_name: str
    summary: str

    if left_obs_type == "scalar":
        left_value: float | None = left_artifact.get("value")
        right_value: float | None = right_artifact.get("value")
        abs_delta = _compute_absolute_delta(left_value, right_value)
        rel_delta = _compute_relative_delta(abs_delta, right_value)
        direction = _compute_direction(abs_delta, rel_delta, flat_tolerance_relative)
        artifact = {
            **base,
            "comparison_type": "scalar_delta",
            "left_value": left_value,
            "right_value": right_value,
            "absolute_delta": abs_delta,
            "relative_delta": rel_delta,
            "direction": direction,
        }
        artifact_name = f"{metric_name}_compare_scalar"
        summary = (
            f"compare {metric_name} scalar: {direction} "
            f"(Δ {abs_delta if abs_delta is not None else 'n/a'})"
        )

    elif left_obs_type == "time_series":
        granularity = left_artifact.get("granularity")
        left_series: list[dict[str, Any]] = left_artifact.get("series") or []
        right_series: list[dict[str, Any]] = right_artifact.get("series") or []

        left_series_map = {_series_row_key(row): row for row in left_series}
        right_series_map = {_series_row_key(row): row for row in right_series}
        all_series_keys = sorted(set(left_series_map) | set(right_series_map))
        if not all_series_keys:
            raise ValueError(
                "compare: NOT_COMPARABLE - no time-series buckets found in either observation"
            )

        time_series_rows: list[dict[str, Any]] = []
        matched_left_values: list[float] = []
        matched_right_values: list[float] = []

        for key in all_series_keys:
            left_row = left_series_map.get(key)
            right_row = right_series_map.get(key)
            anchor = left_row or right_row or {}
            window = dict(anchor.get("window") or {})
            if left_row and right_row:
                presence = "both"
                left_value = _coerce_numeric_or_none(left_row.get("value"))
                right_value = _coerce_numeric_or_none(right_row.get("value"))
                row_abs = _compute_absolute_delta(left_value, right_value)
                row_rel = _compute_relative_delta(row_abs, right_value)
                row_dir = _compute_direction(row_abs, row_rel, flat_tolerance_relative)
                if left_value is not None and right_value is not None:
                    matched_left_values.append(left_value)
                    matched_right_values.append(right_value)
            elif left_row:
                presence = "left_only"
                left_value = _coerce_numeric_or_none(left_row.get("value"))
                right_value = None
                row_abs = left_value
                row_rel = None
                row_dir = "undefined"
            else:
                presence = "right_only"
                left_value = None
                right_value = _coerce_numeric_or_none((right_row or {}).get("value"))
                row_abs = -right_value if right_value is not None else None
                row_rel = None
                row_dir = "undefined"

            time_series_rows.append(
                {
                    "window": window,
                    "left_value": left_value,
                    "right_value": right_value,
                    "absolute_delta": row_abs,
                    "relative_delta": row_rel,
                    "direction": row_dir,
                    "presence": presence,
                }
            )

        summary_left_value = sum(matched_left_values) if matched_left_values else None
        summary_right_value = sum(matched_right_values) if matched_right_values else None
        summary_abs = _compute_absolute_delta(summary_left_value, summary_right_value)
        summary_rel = _compute_relative_delta(summary_abs, summary_right_value)
        summary_dir = _compute_direction(summary_abs, summary_rel, flat_tolerance_relative)

        matched_windows = [
            _normalize_window(left_series_map[key].get("window") or {})
            for key in all_series_keys
            if key in left_series_map and key in right_series_map
        ]
        matched_time_scope: dict[str, Any] | None = None
        if matched_windows:
            matched_time_scope = {
                "kind": "range",
                "start": matched_windows[0][0],
                "end": matched_windows[-1][1],
            }

        analytical_metadata.update(
            {
                "left_bucket_count": len(left_series),
                "right_bucket_count": len(right_series),
                "matched_bucket_count": len(matched_left_values),
                "dropped_left_buckets": len(left_series) - len(matched_left_values),
                "dropped_right_buckets": len(right_series) - len(matched_right_values),
                "pairing_rule": "intersection_by_time_bucket",
                "matched_time_scope": matched_time_scope,
            }
        )

        artifact = {
            **base,
            "comparison_type": "time_series_delta",
            "granularity": granularity,
            "rows": time_series_rows,
            "summary_left_value": summary_left_value,
            "summary_right_value": summary_right_value,
            "summary_absolute_delta": summary_abs,
            "summary_relative_delta": summary_rel,
            "summary_direction": summary_dir,
        }
        artifact_name = f"{metric_name}_compare_time_series"
        summary = f"compare {metric_name} time_series: {len(time_series_rows)} bucket deltas"

    else:
        # Segmented mode
        dims: list[str] = left_artifact.get("dimensions") or []
        left_segs: list[dict[str, Any]] = left_artifact.get("segments") or []
        right_segs: list[dict[str, Any]] = right_artifact.get("segments") or []

        def _seg_key(seg: dict[str, Any]) -> tuple[str, ...]:
            return tuple(str(seg.get("keys", {}).get(d)) for d in dims)

        left_segment_map = {_seg_key(s): s for s in left_segs}
        right_segment_map = {_seg_key(s): s for s in right_segs}
        all_segment_keys = set(left_segment_map) | set(right_segment_map)
        if not all_segment_keys:
            comparability["issues"].append(
                {
                    "code": "data_incomplete",
                    "severity": "warning",
                    "message": "no segments found in either observation",
                }
            )
            comparability["status"] = "needs_attention"

        segmented_rows: list[dict[str, Any]] = []
        for segment_key in sorted(all_segment_keys):
            l_seg = left_segment_map.get(segment_key)
            r_seg = right_segment_map.get(segment_key)
            if l_seg and r_seg:
                presence = "both"
                lv: float | None = l_seg.get("value")
                rv: float | None = r_seg.get("value")
                keys_dict: dict[str, Any] = l_seg.get("keys") or {}
                row_abs = _compute_absolute_delta(lv, rv)
                row_rel = _compute_relative_delta(row_abs, rv)
                row_dir = _compute_direction(row_abs, row_rel, flat_tolerance_relative)
            elif l_seg:
                presence = "left_only"
                lv = l_seg.get("value")
                rv = None
                keys_dict = l_seg.get("keys") or {}
                row_abs = lv  # absolute_delta = left_value per spec
                row_rel = None
                row_dir = "undefined"
            else:
                presence = "right_only"
                lv = None
                rv = (r_seg or {}).get("value")
                keys_dict = (r_seg or {}).get("keys") or {}
                row_abs = (-rv) if rv is not None else None  # absolute_delta = -right_value
                row_rel = None
                row_dir = "undefined"
            segmented_rows.append(
                {
                    "keys": keys_dict,
                    "left_value": lv,
                    "right_value": rv,
                    "absolute_delta": row_abs,
                    "relative_delta": row_rel,
                    "direction": row_dir,
                    "presence": presence,
                }
            )

        scope_lv: float | None = left_artifact.get("scope_value")
        scope_rv: float | None = right_artifact.get("scope_value")
        scope_abs = _compute_absolute_delta(scope_lv, scope_rv)
        scope_rel = _compute_relative_delta(scope_abs, scope_rv)
        scope_dir = _compute_direction(scope_abs, scope_rel, flat_tolerance_relative)

        artifact = {
            **base,
            "comparison_type": "segmented_delta",
            "dimensions": dims,
            "rows": segmented_rows,
            "scope_left_value": scope_lv,
            "scope_right_value": scope_rv,
            "scope_absolute_delta": scope_abs,
            "scope_relative_delta": scope_rel,
            "scope_direction": scope_dir,
        }
        artifact_name = f"{metric_name}_compare_segmented"
        summary = f"compare {metric_name} segmented: {len(segmented_rows)} delta rows"

    artifact_id = svc._commit_artifact_with_extraction(
        session_id,
        step_id,
        "compare_artifact",
        artifact_name,
        artifact,
        step_type="compare",
    )
    result: dict[str, Any] = {
        "intent_type": "compare",
        "step_type": "compare",
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": "compare",
        },
        "artifact_id": artifact_id,
        **artifact,
    }
    provenance: dict[str, Any] = {
        "left_step_id": left_step_id,
        "right_step_id": right_step_id,
    }
    svc._insert_step(step_id, session_id, "compare", summary, result, provenance=provenance)
    return result


def _compute_absolute_delta(left: float | None, right: float | None) -> float | None:
    """absolute_delta = left - right; None if either operand is None."""
    if left is None or right is None:
        return None
    return left - right


def _compute_relative_delta(absolute_delta: float | None, right: float | None) -> float | None:
    """relative_delta = absolute_delta / right; None if right is 0 or None."""
    if absolute_delta is None or right is None or right == 0:
        return None
    return absolute_delta / right


def _compute_direction(
    absolute_delta: float | None,
    relative_delta: float | None,
    flat_tolerance_relative: float,
) -> str:
    """Derive direction per compare.md semantics (increase/decrease/flat/undefined)."""
    if absolute_delta is None:
        return "undefined"
    if absolute_delta == 0:
        return "flat"
    if relative_delta is not None and abs(relative_delta) <= flat_tolerance_relative:
        return "flat"
    return "increase" if absolute_delta > 0 else "decrease"
