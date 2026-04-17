from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.intents.calendar_alignment_metadata import normalize_resolved_policy_summary

if TYPE_CHECKING:
    from app.service import SemanticLayerService


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

    # time_series unsupported in v1 (check before other comparability)
    if left_obs_type == "time_series" or right_obs_type == "time_series":
        raise ValueError(
            "compare: UNSUPPORTED_COMPARISON - time_series comparisons are not supported in v1"
        )

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

    calendar_alignment_summary = _resolve_calendar_alignment_reuse(
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

    else:
        # Segmented mode
        dims: list[str] = left_artifact.get("dimensions") or []
        left_segs: list[dict[str, Any]] = left_artifact.get("segments") or []
        right_segs: list[dict[str, Any]] = right_artifact.get("segments") or []

        def _seg_key(seg: dict[str, Any]) -> tuple:  # type: ignore[type-arg]
            return tuple(str(seg.get("keys", {}).get(d)) for d in dims)

        left_map = {_seg_key(s): s for s in left_segs}
        right_map = {_seg_key(s): s for s in right_segs}
        all_keys = set(left_map) | set(right_map)
        if not all_keys:
            comparability["issues"].append(
                {
                    "code": "data_incomplete",
                    "severity": "warning",
                    "message": "no segments found in either observation",
                }
            )
            comparability["status"] = "needs_attention"

        rows: list[dict[str, Any]] = []
        for key in sorted(all_keys):
            l_seg = left_map.get(key)
            r_seg = right_map.get(key)
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
            rows.append(
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
            "rows": rows,
            "scope_left_value": scope_lv,
            "scope_right_value": scope_rv,
            "scope_absolute_delta": scope_abs,
            "scope_relative_delta": scope_rel,
            "scope_direction": scope_dir,
        }
        artifact_name = f"{metric_name}_compare_segmented"
        summary = f"compare {metric_name} segmented: {len(rows)} delta rows"

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


def _resolve_calendar_alignment_reuse(
    *,
    left_resolved_policy_summary: Any,
    right_resolved_policy_summary: Any,
) -> dict[str, Any]:
    left_summary = _normalize_resolved_policy_summary(left_resolved_policy_summary)
    right_summary = _normalize_resolved_policy_summary(right_resolved_policy_summary)
    if left_summary is None and right_summary is None:
        return {"issues": [], "fatal_message": None, "reuse_summary": None}
    if left_summary is None or right_summary is None:
        return {
            "issues": [
                {
                    "code": "calendar_alignment_metadata_mismatch",
                    "severity": "error",
                    "message": (
                        "calendar alignment metadata must be present on both observations when "
                        "either side freezes a resolved policy summary"
                    ),
                }
            ],
            "fatal_message": (
                "calendar alignment metadata must be present on both observations when either "
                "side freezes a resolved policy summary"
            ),
            "reuse_summary": None,
        }

    mismatch = _calendar_alignment_mismatch(left_summary=left_summary, right_summary=right_summary)
    if mismatch is not None:
        return {
            "issues": [mismatch],
            "fatal_message": str(mismatch["message"]),
            "reuse_summary": None,
        }

    issues: list[dict[str, Any]] = []
    warnings = sorted(
        {
            *left_summary["comparability_warnings"],
            *right_summary["comparability_warnings"],
        }
    )
    for warning_code in warnings:
        issues.append(
            {
                "code": warning_code,
                "severity": "warning",
                "message": f"upstream observation froze calendar alignment warning '{warning_code}'",
            }
        )

    min_aligned_ratio = min(
        left_summary["coverage_summary"]["aligned_ratio"],
        right_summary["coverage_summary"]["aligned_ratio"],
    )
    max_unpaired_bucket_count = max(
        left_summary["coverage_summary"]["unpaired_bucket_count"],
        right_summary["coverage_summary"]["unpaired_bucket_count"],
    )
    if min_aligned_ratio < 1.0 or max_unpaired_bucket_count > 0:
        issues.append(
            {
                "code": "alignment_coverage_insufficient",
                "severity": "warning",
                "message": (
                    "upstream observation froze incomplete calendar bucket alignment coverage"
                ),
                "details": {
                    "left_coverage_summary": left_summary["coverage_summary"],
                    "right_coverage_summary": right_summary["coverage_summary"],
                },
            }
        )

    return {
        "issues": issues,
        "fatal_message": None,
        "reuse_summary": {
            "reuse_source": "observation_resolved_policy_summary",
            "policy_ref": left_summary["policy_ref"],
            "comparison_basis": left_summary["comparison_basis"],
            "resolved_calendar_source": left_summary["resolved_calendar_source"],
            "resolved_calendar_version": left_summary["resolved_calendar_version"],
            "comparability_warnings": warnings,
            "left_coverage_summary": left_summary["coverage_summary"],
            "right_coverage_summary": right_summary["coverage_summary"],
            "effective_coverage_summary": {
                "aligned_bucket_count": min(
                    left_summary["coverage_summary"]["aligned_bucket_count"],
                    right_summary["coverage_summary"]["aligned_bucket_count"],
                ),
                "unpaired_bucket_count": max_unpaired_bucket_count,
                "aligned_ratio": min_aligned_ratio,
            },
        },
    }


def _normalize_resolved_policy_summary(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        return normalize_resolved_policy_summary(
            value,
            error_factory=lambda: ValueError(
                "compare: INVALID_ARGUMENT - malformed resolved calendar alignment metadata"
            ),
        )
    except ValueError as error:
        raise error from None


def _calendar_alignment_mismatch(
    *,
    left_summary: dict[str, Any],
    right_summary: dict[str, Any],
) -> dict[str, Any] | None:
    mismatch_fields = (
        ("policy_ref", "calendar_policy_mismatch"),
        ("comparison_basis", "calendar_comparison_basis_mismatch"),
        ("resolved_calendar_source", "calendar_source_mismatch"),
        ("resolved_calendar_version", "calendar_version_mismatch"),
    )
    for field_name, code in mismatch_fields:
        left_value = left_summary[field_name]
        right_value = right_summary[field_name]
        if left_value != right_value:
            return {
                "code": code,
                "severity": "error",
                "message": (
                    f"left {field_name} '{left_value}' != right {field_name} '{right_value}'"
                ),
            }
    return None
