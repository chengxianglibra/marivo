from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.core.semantic.calendar import (
    build_calendar_annotation_rows,
    compare_type_to_alignment_plan,
    resolve_calendar_baseline_window,
    resolve_calendar_bucket_pairing,
)
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.predicate_lineage_reuse import (
    resolve_predicate_lineage_reuse_for_intent,
)
from marivo.runtime.semantic.calendar_data_runtime import (
    CalendarDataReaderLike,
    CalendarDataResolutionError,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


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


def _coverage_ratio_value(summary: Any) -> float | None:
    if not isinstance(summary, dict):
        return None
    value = summary.get("coverage_ratio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"compare: INVALID_ARGUMENT - {label} must be an object")
    return value


def _window_start(row: dict[str, Any], *, label: str) -> str:
    window = _require_mapping(row.get("window"), label=label)
    start = window.get("start")
    if not isinstance(start, str) or not start:
        raise ValueError(f"compare: INVALID_ARGUMENT - {label}.start must be a string")
    return start


def _window_start_day(row: dict[str, Any], *, label: str) -> str:
    return _parse_date_like(_window_start(row, label=label)).isoformat()


def _series_map_by_start(series: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    by_start: dict[str, dict[str, Any]] = {}
    for row in series:
        by_start[_window_start(row, label=label)] = row
    return by_start


def _series_map_by_start_day(
    series: list[dict[str, Any]], *, label: str
) -> dict[str, dict[str, Any]]:
    by_start: dict[str, dict[str, Any]] = {}
    for row in series:
        by_start[_window_start_day(row, label=label)] = row
    return by_start


def _matched_scope_from_windows(windows: list[tuple[str, str]]) -> dict[str, Any] | None:
    if not windows:
        return None
    return {
        "kind": "range",
        "start": windows[0][0],
        "end": windows[-1][1],
    }


def _parse_date_like(value: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError("compare: INVALID_ARGUMENT - calendar alignment requires date boundary")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T")).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def _time_scope_window(artifact: dict[str, Any], *, label: str) -> tuple[date, date]:
    time_scope = _require_mapping(artifact.get("time_scope"), label=f"{label}.time_scope")
    start = _parse_date_like(str(time_scope.get("start") or ""))
    end = _parse_date_like(str(time_scope.get("end") or ""))
    if start >= end:
        raise ValueError(f"compare: INVALID_ARGUMENT - {label}.time_scope.start must be before end")
    return start, end


def _resolve_time_series_pairing_basis(
    *,
    runtime: MarivoRuntime,
    compare_type: str,
    left_artifact: dict[str, Any],
    right_artifact: dict[str, Any],
) -> dict[str, Any]:
    left_series: list[dict[str, Any]] = left_artifact.get("series") or []
    right_series: list[dict[str, Any]] = right_artifact.get("series") or []
    left_series_map = {_series_row_key(row): row for row in left_series}
    right_series_map = {_series_row_key(row): row for row in right_series}
    default_keys = sorted(set(left_series_map) | set(right_series_map))

    alignment_plan = compare_type_to_alignment_plan(compare_type)
    if alignment_plan is not None:
        current_window = _time_scope_window(left_artifact, label="left")
        baseline_window = resolve_calendar_baseline_window(
            current_window=current_window,
            rule=alignment_plan.resolved_baseline_generation_rule,
        )
        if alignment_plan.requires_calendar_data:
            reader = runtime.calendar_data_reader
            if not isinstance(reader, CalendarDataReaderLike):
                raise ValueError(
                    "compare: INVALID_ARGUMENT - compare_type "
                    f"'{compare_type}' requires configured calendar data"
                )
            try:
                calendar_data = reader.read_for_alignment(
                    current_window=current_window,
                    baseline_window=baseline_window,
                )
            except CalendarDataResolutionError as exc:
                raise ValueError(
                    "compare: INVALID_ARGUMENT - compare_type "
                    f"'{compare_type}' calendar data unavailable: {exc}"
                ) from exc
            annotation_rows = calendar_data.annotation_rows
        else:
            annotation_rows = build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=None,
            )
        pairing_resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=alignment_plan.matching_strategy,
            fallback_strategy=alignment_plan.fallback_strategy,
            annotation_rows=annotation_rows,
        )
        left_by_current = _series_map_by_start_day(left_series, label="left.series.window")
        right_by_baseline = _series_map_by_start_day(right_series, label="right.series.window")
        paired_left: dict[str, dict[str, Any]] = {}
        paired_right: dict[str, dict[str, Any]] = {}
        paired_keys: list[str] = []
        for pairing in pairing_resolution.bucket_pairing:
            current_bucket_start = pairing.get("current_bucket_start")
            baseline_bucket_start = pairing.get("baseline_bucket_start")
            if not isinstance(current_bucket_start, str) or not current_bucket_start:
                continue
            left_current_row = left_by_current.get(current_bucket_start)
            if left_current_row is None:
                continue
            key = _series_row_key(left_current_row)
            paired_left[key] = left_current_row
            if isinstance(baseline_bucket_start, str) and baseline_bucket_start:
                right_baseline_row = right_by_baseline.get(baseline_bucket_start)
                if right_baseline_row is not None:
                    paired_right[key] = {
                        "window": dict(left_current_row.get("window") or {}),
                        "value": right_baseline_row.get("value"),
                        "_matched_window": dict(right_baseline_row.get("window") or {}),
                    }
            paired_keys.append(key)
        if not paired_keys:
            raise ValueError(
                "compare: NOT_COMPARABLE - compare_type "
                f"'{compare_type}' produced no aligned buckets"
            )
        return {
            "pairing_basis": "compare_type_calendar_alignment",
            "pairing_rule": alignment_plan.resolved_alignment_mode,
            "series_keys": paired_keys,
            "left_series_map": paired_left,
            "right_series_map": paired_right,
            "compare_type": compare_type,
            "calendar_alignment": {
                "compare_type": compare_type,
                "comparison_basis": alignment_plan.comparison_basis,
                "resolved_alignment_mode": alignment_plan.resolved_alignment_mode,
                "current_window": {
                    "start": current_window[0].isoformat(),
                    "end": current_window[1].isoformat(),
                },
                "baseline_window": {
                    "start": baseline_window[0].isoformat(),
                    "end": baseline_window[1].isoformat(),
                },
                "bucket_pairing": pairing_resolution.bucket_pairing,
                "rollup_safe": pairing_resolution.rollup_safe,
                "comparability_warnings": pairing_resolution.comparability_warnings,
            },
        }

    return {
        "pairing_basis": "observed_series",
        "pairing_rule": "intersection_by_time_bucket",
        "series_keys": default_keys,
        "left_series_map": left_series_map,
        "right_series_map": right_series_map,
        "compare_type": compare_type,
    }


def run_compare_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
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
    left_artifact_id_raw = p.get("left_artifact_id")
    right_artifact_id_raw = p.get("right_artifact_id")
    left_artifact_id = left_artifact_id_raw.strip() if isinstance(left_artifact_id_raw, str) else ""
    right_artifact_id = (
        right_artifact_id_raw.strip() if isinstance(right_artifact_id_raw, str) else ""
    )
    uses_aoi_artifact_refs = bool(left_artifact_id or right_artifact_id) or (
        "left_ref" not in p and "right_ref" not in p
    )
    left_step_id: str = "" if uses_aoi_artifact_refs else left_ref_raw.get("step_id") or ""
    right_step_id: str = "" if uses_aoi_artifact_refs else right_ref_raw.get("step_id") or ""
    left_session_id: str = (
        session_id if uses_aoi_artifact_refs else left_ref_raw.get("session_id") or session_id
    )
    right_session_id: str = (
        session_id if uses_aoi_artifact_refs else right_ref_raw.get("session_id") or session_id
    )
    compare_type_raw = p.get("compare_type")
    compare_type = str(compare_type_raw or "normal").strip() or "normal"
    try:
        alignment_plan = compare_type_to_alignment_plan(compare_type)
    except ValueError as exc:
        raise ValueError(f"compare: INVALID_ARGUMENT - {exc}") from exc

    if uses_aoi_artifact_refs:
        if not left_artifact_id or not right_artifact_id:
            raise ValueError(
                "compare: INVALID_ARGUMENT - both left_artifact_id and right_artifact_id are required"
            )
        left_artifact = runtime.resolve_artifact_by_id(session_id, left_artifact_id)
        if left_artifact is None:
            raise ValueError(
                f"compare: ARTIFACT_NOT_FOUND - no committed artifact for left_artifact_id '{left_artifact_id}'"
            )
        right_artifact = runtime.resolve_artifact_by_id(session_id, right_artifact_id)
        if right_artifact is None:
            raise ValueError(
                f"compare: ARTIFACT_NOT_FOUND - no committed artifact for right_artifact_id '{right_artifact_id}'"
            )
    else:
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

        left_artifact = runtime.resolve_artifact_for_ref(left_session_id, left_step_id)
        if left_artifact is None:
            raise ValueError(
                f"compare: STEP_NOT_FOUND - no committed artifact for step '{left_step_id}'"
            )
        right_artifact = runtime.resolve_artifact_for_ref(right_session_id, right_step_id)
        if right_artifact is None:
            raise ValueError(
                f"compare: STEP_NOT_FOUND - no committed artifact for step '{right_step_id}'"
            )
        left_artifact_id = runtime.resolve_artifact_id_for_step(session_id, left_step_id) or ""
        right_artifact_id = runtime.resolve_artifact_id_for_step(session_id, right_step_id) or ""

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
    elif not fatal and alignment_plan is not None:
        raise ValueError(
            f"compare: INVALID_ARGUMENT - compare_type '{compare_type}' "
            "requires time_series observations"
        )

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

    # Resolve analytical metadata for downstream passthrough.
    left_am = left_artifact.get("analytical_metadata") or {}
    right_am = right_artifact.get("analytical_metadata") or {}

    predicate_lineage_summary = resolve_predicate_lineage_reuse_for_intent(
        intent_name="compare",
        left_predicate_filter_lineage=left_artifact.get("predicate_filter_lineage"),
        right_predicate_filter_lineage=right_artifact.get("predicate_filter_lineage"),
    )
    issues.extend(predicate_lineage_summary["issues"])
    if predicate_lineage_summary["fatal_message"] is not None:
        fatal = True

    if fatal:
        fatal_message = predicate_lineage_summary["fatal_message"] or issues[0]["message"]
        raise ValueError(f"compare: NOT_COMPARABLE - {fatal_message}")

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
    step_id = new_step_id()
    now = datetime.now(UTC).isoformat()
    flat_tolerance_relative = 0.01

    left_ref_out = {
        "session_id": left_session_id,
        "step_id": left_step_id,
        "step_type": "observe",
        "artifact_id": left_artifact_id,
    }
    right_ref_out = {
        "session_id": right_session_id,
        "step_id": right_step_id,
        "step_type": "observe",
        "artifact_id": right_artifact_id,
    }
    lineage: dict[str, Any] = {
        "left_source_ref": left_ref_out,
        "right_source_ref": right_ref_out,
        "observation_schema_version": left_artifact.get("schema_version"),
        "derivation_version": "1.0",
        "compare_type": compare_type,
    }
    resolved_input_summary: dict[str, Any] = {
        "left_time_scope": left_artifact.get("time_scope"),
        "right_time_scope": right_artifact.get("time_scope"),
        "left_scope": left_artifact.get("scope") or {},
        "right_scope": right_artifact.get("scope") or {},
    }
    if predicate_lineage_summary["reuse_summary"] is not None:
        resolved_input_summary["predicate_lineage"] = predicate_lineage_summary["reuse_summary"]
    analytical_metadata: dict[str, Any] = {
        "aggregation_semantics": left_am.get("aggregation_semantics", "sum"),
        "additive_dimensions": left_am.get("additive_dimensions"),
        "relative_delta_denominator": "right",
        "flat_tolerance_relative": flat_tolerance_relative,
        "left_row_count": left_am.get("row_count"),
        "right_row_count": right_am.get("row_count"),
        "compare_type": compare_type,
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
        pairing_basis = _resolve_time_series_pairing_basis(
            runtime=runtime,
            compare_type=compare_type,
            left_artifact=left_artifact,
            right_artifact=right_artifact,
        )
        left_series_map = pairing_basis["left_series_map"]
        right_series_map = pairing_basis["right_series_map"]
        all_series_keys = pairing_basis["series_keys"]
        if not all_series_keys:
            raise ValueError(
                "compare: NOT_COMPARABLE - no time-series buckets found in either observation"
            )

        time_series_rows: list[dict[str, Any]] = []
        matched_left_values: list[float] = []
        matched_right_values: list[float] = []
        matched_left_windows: list[tuple[str, str]] = []
        matched_right_windows: list[tuple[str, str]] = []

        for key in all_series_keys:
            left_row = left_series_map.get(key)
            right_row = right_series_map.get(key)
            anchor = left_row or right_row or {}
            window = dict(anchor.get("window") or {})
            left_value = _coerce_numeric_or_none(left_row.get("value")) if left_row else None
            right_value = _coerce_numeric_or_none(right_row.get("value")) if right_row else None
            if left_row and right_row and left_value is not None and right_value is not None:
                presence = "both"
                row_abs = _compute_absolute_delta(left_value, right_value)
                row_rel = _compute_relative_delta(row_abs, right_value)
                row_dir = _compute_direction(row_abs, row_rel, flat_tolerance_relative)
                matched_left_values.append(left_value)
                matched_right_values.append(right_value)
                matched_left_windows.append(_normalize_window(left_row.get("window") or {}))
                matched_right_windows.append(
                    _normalize_window(
                        right_row.get("_matched_window") or right_row.get("window") or {}
                    )
                )
            elif left_value is not None:
                presence = "left_only"
                row_abs = left_value
                row_rel = None
                row_dir = "undefined"
            elif right_value is not None:
                presence = "right_only"
                row_abs = -right_value if right_value is not None else None
                row_rel = None
                row_dir = "undefined"
            else:
                presence = "left_only" if left_row else "right_only"
                row_abs = None
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

        matched_time_scope = _matched_scope_from_windows(matched_left_windows)
        matched_left_time_scope = _matched_scope_from_windows(matched_left_windows)
        matched_right_time_scope = _matched_scope_from_windows(matched_right_windows)

        analytical_metadata.update(
            {
                "left_bucket_count": len(left_series),
                "right_bucket_count": len(right_series),
                "matched_bucket_count": len(matched_left_values),
                "dropped_left_buckets": sum(
                    1 for row in left_series if _coerce_numeric_or_none(row.get("value")) is None
                )
                + max(
                    0,
                    len(left_series)
                    - len(matched_left_values)
                    - sum(
                        1
                        for row in left_series
                        if _coerce_numeric_or_none(row.get("value")) is None
                    ),
                ),
                "dropped_right_buckets": sum(
                    1 for row in right_series if _coerce_numeric_or_none(row.get("value")) is None
                )
                + max(
                    0,
                    len(right_series)
                    - len(matched_right_values)
                    - sum(
                        1
                        for row in right_series
                        if _coerce_numeric_or_none(row.get("value")) is None
                    ),
                ),
                "pairing_basis": pairing_basis["pairing_basis"],
                "pairing_rule": pairing_basis["pairing_rule"],
                "matched_time_scope": matched_time_scope,
                "matched_left_time_scope": matched_left_time_scope,
                "matched_right_time_scope": matched_right_time_scope,
            }
        )
        if "compare_type" in pairing_basis:
            analytical_metadata["compare_type"] = pairing_basis["compare_type"]
        calendar_alignment = pairing_basis.get("calendar_alignment")
        if isinstance(calendar_alignment, dict):
            resolved_input_summary["calendar_alignment"] = calendar_alignment
            analytical_metadata["calendar_alignment"] = {
                key: calendar_alignment.get(key)
                for key in (
                    "compare_type",
                    "comparison_basis",
                    "resolved_alignment_mode",
                    "rollup_safe",
                )
            }

        data_coverage_summary = None
        calendar_alignment = resolved_input_summary.get("calendar_alignment")
        if isinstance(calendar_alignment, dict):
            data_coverage_summary = calendar_alignment.get("effective_data_coverage_summary")
        coverage_ratio = _coverage_ratio_value(data_coverage_summary)
        if coverage_ratio is not None and coverage_ratio < 0.9999:
            comparability["status"] = "needs_attention"
            if not any(
                issue.get("code") == "metric_data_coverage_incomplete"
                for issue in comparability["issues"]
            ):
                comparability["issues"].append(
                    {
                        "code": "metric_data_coverage_incomplete",
                        "severity": "warning",
                        "message": (
                            "metric data coverage is incomplete, so one or more aligned or "
                            "requested buckets do not have business metric values"
                        ),
                        "details": {"effective_data_coverage_summary": data_coverage_summary},
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

    provenance: dict[str, Any] = {
        "left_step_id": left_step_id,
        "right_step_id": right_step_id,
        "left_artifact_id": left_artifact_id,
        "right_artifact_id": right_artifact_id,
    }
    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "compare",
        "compare_artifact",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
    )
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
