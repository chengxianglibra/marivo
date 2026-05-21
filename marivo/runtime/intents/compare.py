from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.core.semantic.calendar import (
    build_calendar_annotation_rows,
    compare_type_to_alignment_plan,
    resolve_calendar_bucket_pairing,
)
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.metric_frame import (
    dimension_names_from_axes,
    has_dimension_axis,
    has_time_axis,
    read_axes_from_artifact,
    time_grain_from_axes,
)
from marivo.runtime.intents.predicate_lineage_reuse import (
    resolve_predicate_lineage_reuse_for_intent,
)
from marivo.runtime.semantic.calendar_data_runtime import (
    CalendarDataReaderLike,
    CalendarDataResolutionError,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_AOI_PARAM_KEYS: frozenset[str] = frozenset(
    {"current_artifact_id", "baseline_artifact_id", "compare_type"}
)


# --- v2.0 read helpers ---
# Pure v2.0 (axes+series) accessors for reading observe artifact data.


def _read_scalar_value(artifact: dict[str, Any]) -> float | None:
    """Read scalar value from a v2.0 artifact."""
    series_list = artifact.get("series") or []
    if not series_list:
        return None
    points = series_list[0].get("points") or []
    if not points:
        return None
    return _coerce_numeric_or_none(points[0].get("value"))


def _read_time_series_points(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Read time_series points list from a v2.0 artifact."""
    series_list = artifact.get("series") or []
    if not series_list:
        return []
    return series_list[0].get("points") or []


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


def _coverage_unit_from_row(row: dict[str, Any], *, grain: str) -> str:
    start = _window_start(row, label="series.window")
    if grain == "day":
        return start[:10]
    return start


def _time_series_coverage_facts(artifact: dict[str, Any]) -> dict[str, Any]:
    grain = str(time_grain_from_axes(read_axes_from_artifact(artifact)) or "")
    series = _read_time_series_points(artifact)
    missing_units = [
        _coverage_unit_from_row(row, grain=grain) for row in series if row.get("value") is None
    ]
    return {
        "grain": grain,
        "requested_units": len(series),
        "covered_units": len(series) - len(missing_units),
        "missing_units": missing_units,
    }


def _time_series_coverage_signature(
    artifact: dict[str, Any],
) -> tuple[str, int, int, tuple[int, ...]]:
    grain = str(time_grain_from_axes(read_axes_from_artifact(artifact)) or "")
    series = _read_time_series_points(artifact)
    missing_indexes = tuple(index for index, row in enumerate(series) if row.get("value") is None)
    return (grain, len(series), len(series) - len(missing_indexes), missing_indexes)


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


def _sorted_series_rows(series: list[dict[str, Any]], *, label: str) -> list[dict[str, Any]]:
    return sorted(series, key=lambda row: _window_start(row, label=label))


def _relative_position_pairing_basis(
    *,
    left_series: list[dict[str, Any]],
    right_series: list[dict[str, Any]],
    compare_type: str,
) -> dict[str, Any]:
    left_sorted = _sorted_series_rows(left_series, label="left.series.window")
    right_sorted = _sorted_series_rows(right_series, label="right.series.window")
    paired_left: dict[str, dict[str, Any]] = {}
    paired_right: dict[str, dict[str, Any]] = {}
    paired_keys: list[str] = []
    max_len = max(len(left_sorted), len(right_sorted))
    for index in range(max_len):
        left_row = left_sorted[index] if index < len(left_sorted) else None
        right_row = right_sorted[index] if index < len(right_sorted) else None
        if left_row is None and right_row is None:
            continue
        key = _series_row_key(left_row if left_row is not None else right_row or {})
        if left_row is not None:
            key = _series_row_key(left_row)
            paired_left[key] = left_row
        if right_row is not None:
            right_payload = dict(right_row)
            if left_row is not None:
                right_payload["window"] = dict(left_row.get("window") or {})
                right_payload["_matched_window"] = dict(right_row.get("window") or {})
            paired_right[key] = right_payload
        paired_keys.append(key)
    return {
        "pairing_basis": "input_artifact_window_position",
        "pairing_rule": "relative_bucket_position",
        "series_keys": paired_keys,
        "left_series_map": paired_left,
        "right_series_map": paired_right,
        "compare_type": compare_type,
    }


def _matched_scope_from_windows(
    windows: list[tuple[str, str]], *, field: str | None
) -> dict[str, Any] | None:
    if not windows:
        return None
    return {
        "field": field or "time",
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
    left_series: list[dict[str, Any]] = _read_time_series_points(left_artifact)
    right_series: list[dict[str, Any]] = _read_time_series_points(right_artifact)

    alignment_plan = compare_type_to_alignment_plan(compare_type)
    if alignment_plan is None:
        return _relative_position_pairing_basis(
            left_series=left_series,
            right_series=right_series,
            compare_type=compare_type,
        )

    current_window = _time_scope_window(left_artifact, label="current")
    baseline_window = _time_scope_window(right_artifact, label="baseline")
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
            f"compare: NOT_COMPARABLE - compare_type '{compare_type}' produced no aligned buckets"
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


def run_compare_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute a `compare` intent: compute typed delta between two observe artifacts.

    Input: current_artifact_id + baseline_artifact_id (both committed observe artifacts).
    Output: committed compare_artifact (scalar_delta or segmented_delta).

    Empty semantics: hard-fails only on NOT_COMPARABLE (incompatible inputs); null values
    and empty segment sets produce data_incomplete issues with needs_attention status.
    """
    p = params or {}
    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "compare: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; compare accepts only AOI request fields"
        )

    current_artifact_id_raw = p.get("current_artifact_id")
    baseline_artifact_id_raw = p.get("baseline_artifact_id")
    current_artifact_id = (
        current_artifact_id_raw.strip() if isinstance(current_artifact_id_raw, str) else ""
    )
    baseline_artifact_id = (
        baseline_artifact_id_raw.strip() if isinstance(baseline_artifact_id_raw, str) else ""
    )
    left_session_id = session_id
    right_session_id = session_id
    compare_type_raw = p.get("compare_type")
    compare_type = str(compare_type_raw or "normal").strip() or "normal"
    try:
        alignment_plan = compare_type_to_alignment_plan(compare_type)
    except ValueError as exc:
        raise ValueError(f"compare: INVALID_ARGUMENT - {exc}") from exc

    if not current_artifact_id or not baseline_artifact_id:
        raise ValueError(
            "compare: INVALID_ARGUMENT - both current_artifact_id and baseline_artifact_id are required"
        )
    left_resolved = runtime.resolve_artifact_with_step_by_id(session_id, current_artifact_id)
    if left_resolved is None:
        raise ValueError(
            f"compare: ARTIFACT_NOT_FOUND - no committed artifact for current_artifact_id '{current_artifact_id}'"
        )
    left_step_id, left_artifact = left_resolved
    right_resolved = runtime.resolve_artifact_with_step_by_id(session_id, baseline_artifact_id)
    if right_resolved is None:
        raise ValueError(
            f"compare: ARTIFACT_NOT_FOUND - no committed artifact for baseline_artifact_id '{baseline_artifact_id}'"
        )
    right_step_id, right_artifact = right_resolved

    # Axes-based detection (v2.0 format)
    left_axes = read_axes_from_artifact(left_artifact)
    right_axes = read_axes_from_artifact(right_artifact)
    left_has_time = has_time_axis(left_axes)
    left_has_dim = has_dimension_axis(left_axes)
    right_has_time = has_time_axis(right_axes)
    right_has_dim = has_dimension_axis(right_axes)

    # Determine effective comparison mode from axes structure
    if left_has_time and left_has_dim:
        left_effective_type = "panel"
    elif left_has_time:
        left_effective_type = "time_series"
    elif left_has_dim:
        left_effective_type = "segmented"
    else:
        left_effective_type = "scalar"

    if right_has_time and right_has_dim:
        right_effective_type = "panel"
    elif right_has_time:
        right_effective_type = "time_series"
    elif right_has_dim:
        right_effective_type = "segmented"
    else:
        right_effective_type = "scalar"

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

    if left_effective_type != right_effective_type:
        issues.append(
            {
                "code": "observation_type_mismatch",
                "severity": "error",
                "message": f"left effective type '{left_effective_type}' != right '{right_effective_type}'",
            }
        )
        fatal = True

    if not fatal and left_has_dim:
        left_dim_names = sorted(dimension_names_from_axes(left_axes))
        right_dim_names = sorted(dimension_names_from_axes(right_axes))
        if left_dim_names != right_dim_names:
            issues.append(
                {
                    "code": "dimension_mismatch",
                    "severity": "error",
                    "message": f"left dimensions {left_dim_names} != right dimensions {right_dim_names}",
                }
            )
            fatal = True

    if not fatal and left_has_time:
        left_grain = time_grain_from_axes(left_axes)
        right_grain = time_grain_from_axes(right_axes)
        if left_grain is None or right_grain is None:
            issues.append(
                {
                    "code": "granularity_mismatch",
                    "severity": "error",
                    "message": "time_series observations must include non-null granularity",
                }
            )
            fatal = True
        elif left_grain != right_grain:
            issues.append(
                {
                    "code": "granularity_mismatch",
                    "severity": "error",
                    "message": (f"left granularity '{left_grain}' != right '{right_grain}'"),
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
        current_predicate_filter_lineage=left_artifact.get("predicate_filter_lineage"),
        baseline_predicate_filter_lineage=right_artifact.get("predicate_filter_lineage"),
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
        left_effective_type == "scalar"
        and _read_scalar_value(left_artifact) is None
        and _read_scalar_value(right_artifact) is None
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

    current_ref_out = {
        "session_id": left_session_id,
        "step_id": left_step_id,
        "step_type": "observe",
        "artifact_id": current_artifact_id,
    }
    baseline_ref_out = {
        "session_id": right_session_id,
        "step_id": right_step_id,
        "step_type": "observe",
        "artifact_id": baseline_artifact_id,
    }
    lineage: dict[str, Any] = {
        "current_source_ref": current_ref_out,
        "baseline_source_ref": baseline_ref_out,
        "observation_schema_version": left_artifact.get("schema_version"),
        "derivation_version": "1.0",
        "compare_type": compare_type,
    }
    resolved_input_summary: dict[str, Any] = {
        "current_time_scope": left_artifact.get("time_scope"),
        "baseline_time_scope": right_artifact.get("time_scope"),
        "current_scope": left_artifact.get("scope") or {},
        "baseline_scope": right_artifact.get("scope") or {},
    }
    if predicate_lineage_summary["reuse_summary"] is not None:
        resolved_input_summary["predicate_lineage"] = predicate_lineage_summary["reuse_summary"]
    analytical_metadata: dict[str, Any] = {
        "decomposition_semantics": left_am.get("decomposition_semantics", "sum"),
        "relative_delta_denominator": "baseline",
        "flat_tolerance_relative": flat_tolerance_relative,
        "current_row_count": left_am.get("row_count"),
        "baseline_row_count": right_am.get("row_count"),
        "compare_type": compare_type,
    }
    execution_metadata: dict[str, Any] = {
        "query_hash": None,
        "engine": "service",
        "executed_at": now,
    }
    base: dict[str, Any] = {
        "artifact_type": "compare_artifact",
        "schema_version": "2.0",
        "metric": metric_name,
        "current_ref": current_ref_out,
        "baseline_ref": baseline_ref_out,
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

    if left_effective_type == "scalar":
        current_value: float | None = _read_scalar_value(left_artifact)
        baseline_value: float | None = _read_scalar_value(right_artifact)
        abs_delta = _compute_absolute_delta(current_value, baseline_value)
        rel_delta = _compute_relative_delta(abs_delta, baseline_value)
        direction = _compute_direction(abs_delta, rel_delta, flat_tolerance_relative)
        scalar_axes: list[dict[str, str]] = []
        scalar_series: list[dict[str, Any]] = [
            {
                "keys": {},
                "points": [
                    {
                        "current_value": current_value,
                        "baseline_value": baseline_value,
                        "delta": abs_delta,
                        "delta_pct": rel_delta,
                        "direction": direction,
                    }
                ],
            }
        ]
        artifact = {
            **base,
            "comparison_type": "scalar_delta",
            "axes": scalar_axes,
            "series": scalar_series,
            # Top-level scalar aliases for downstream intent compatibility
            "current_value": current_value,
            "baseline_value": baseline_value,
            "absolute_delta": abs_delta,
            "relative_delta": rel_delta,
            "direction": direction,
            # Summary fields (v2.0 canonical)
            "summary_current_value": current_value,
            "summary_baseline_value": baseline_value,
            "summary_absolute_delta": abs_delta,
            "summary_relative_delta": rel_delta,
            "summary_direction": direction,
        }
        artifact_name = f"{metric_name}_compare_scalar"
        summary = (
            f"compare {metric_name} scalar: {direction} "
            f"(Δ {abs_delta if abs_delta is not None else 'n/a'})"
        )

    elif left_effective_type == "time_series":
        granularity = time_grain_from_axes(left_axes)
        left_series: list[dict[str, Any]] = _read_time_series_points(left_artifact)
        right_series: list[dict[str, Any]] = _read_time_series_points(right_artifact)
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
        matched_current_values: list[float] = []
        matched_baseline_values: list[float] = []
        matched_left_windows: list[tuple[str, str]] = []
        matched_right_windows: list[tuple[str, str]] = []

        for key in all_series_keys:
            left_row = left_series_map.get(key)
            right_row = right_series_map.get(key)
            anchor = left_row or right_row or {}
            window = dict(anchor.get("window") or {})
            current_value = _coerce_numeric_or_none(left_row.get("value")) if left_row else None
            baseline_value = _coerce_numeric_or_none(right_row.get("value")) if right_row else None
            if left_row and right_row and current_value is not None and baseline_value is not None:
                presence = "both"
                row_abs = _compute_absolute_delta(current_value, baseline_value)
                row_rel = _compute_relative_delta(row_abs, baseline_value)
                row_dir = _compute_direction(row_abs, row_rel, flat_tolerance_relative)
                matched_current_values.append(current_value)
                matched_baseline_values.append(baseline_value)
                matched_left_windows.append(_normalize_window(left_row.get("window") or {}))
                matched_right_windows.append(
                    _normalize_window(
                        right_row.get("_matched_window") or right_row.get("window") or {}
                    )
                )
            elif current_value is not None:
                presence = "current_only"
                row_abs = current_value
                row_rel = None
                row_dir = "undefined"
            elif baseline_value is not None:
                presence = "baseline_only"
                row_abs = -baseline_value if baseline_value is not None else None
                row_rel = None
                row_dir = "undefined"
            else:
                presence = "current_only" if left_row else "baseline_only"
                row_abs = None
                row_rel = None
                row_dir = "undefined"

            time_series_rows.append(
                {
                    "window": window,
                    "current_value": current_value,
                    "baseline_value": baseline_value,
                    "delta": row_abs,
                    "delta_pct": row_rel,
                    "direction": row_dir,
                    "presence": presence,
                }
            )

        summary_current_value = sum(matched_current_values) if matched_current_values else None
        summary_baseline_value = sum(matched_baseline_values) if matched_baseline_values else None
        summary_abs = _compute_absolute_delta(summary_current_value, summary_baseline_value)
        summary_rel = _compute_relative_delta(summary_abs, summary_baseline_value)
        summary_dir = _compute_direction(summary_abs, summary_rel, flat_tolerance_relative)

        left_time_scope = left_artifact.get("time_scope") or {}
        right_time_scope = right_artifact.get("time_scope") or {}
        current_time_field = str(left_time_scope.get("field") or "time").strip() or "time"
        baseline_time_field = str(right_time_scope.get("field") or "time").strip() or "time"
        matched_time_scope = _matched_scope_from_windows(
            matched_left_windows, field=current_time_field
        )
        matched_current_time_scope = _matched_scope_from_windows(
            matched_left_windows, field=current_time_field
        )
        matched_baseline_time_scope = _matched_scope_from_windows(
            matched_right_windows, field=baseline_time_field
        )

        analytical_metadata.update(
            {
                "current_bucket_count": len(left_series),
                "baseline_bucket_count": len(right_series),
                "matched_bucket_count": len(matched_current_values),
                "dropped_current_buckets": sum(
                    1 for row in left_series if _coerce_numeric_or_none(row.get("value")) is None
                )
                + max(
                    0,
                    len(left_series)
                    - len(matched_current_values)
                    - sum(
                        1
                        for row in left_series
                        if _coerce_numeric_or_none(row.get("value")) is None
                    ),
                ),
                "dropped_baseline_buckets": sum(
                    1 for row in right_series if _coerce_numeric_or_none(row.get("value")) is None
                )
                + max(
                    0,
                    len(right_series)
                    - len(matched_baseline_values)
                    - sum(
                        1
                        for row in right_series
                        if _coerce_numeric_or_none(row.get("value")) is None
                    ),
                ),
                "pairing_basis": pairing_basis["pairing_basis"],
                "pairing_rule": pairing_basis["pairing_rule"],
                "matched_time_scope": matched_time_scope,
                "matched_current_time_scope": matched_current_time_scope,
                "matched_baseline_time_scope": matched_baseline_time_scope,
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

        coverage = {
            "current": _time_series_coverage_facts(left_artifact),
            "baseline": _time_series_coverage_facts(right_artifact),
        }
        if _time_series_coverage_signature(left_artifact) != _time_series_coverage_signature(
            right_artifact
        ):
            comparability["status"] = "needs_attention"
            if not any(
                issue.get("code") == "coverage_mismatch" for issue in comparability["issues"]
            ):
                comparability["issues"].append(
                    {
                        "code": "coverage_mismatch",
                        "severity": "warning",
                        "message": "current and baseline time-series coverage differ",
                        "details": coverage,
                    }
                )

        artifact = {
            **base,
            "comparison_type": "time_series_delta",
            "axes": [{"kind": "time", "grain": granularity}],
            "series": [{"keys": {}, "points": time_series_rows}],
            "coverage": coverage,
            "summary_current_value": summary_current_value,
            "summary_baseline_value": summary_baseline_value,
            "summary_absolute_delta": summary_abs,
            "summary_relative_delta": summary_rel,
            "summary_direction": summary_dir,
        }
        artifact_name = f"{metric_name}_compare_time_series"
        summary = f"compare {metric_name} time_series: {len(time_series_rows)} bucket deltas"

    else:
        # Segmented mode
        dims: list[str] = dimension_names_from_axes(left_axes)
        left_series_entries: list[dict[str, Any]] = left_artifact.get("series") or []
        right_series_entries: list[dict[str, Any]] = right_artifact.get("series") or []

        def _seg_key(entry: dict[str, Any]) -> tuple[str, ...]:
            return tuple(str(entry.get("keys", {}).get(d)) for d in dims)

        def _seg_value(entry: dict[str, Any]) -> float | None:
            points = entry.get("points") or []
            if not points:
                return None
            return _coerce_numeric_or_none(points[0].get("value"))

        left_segment_map = {_seg_key(s): s for s in left_series_entries}
        right_segment_map = {_seg_key(s): s for s in right_series_entries}
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

        segmented_series: list[dict[str, Any]] = []
        for segment_key in sorted(all_segment_keys):
            l_entry = left_segment_map.get(segment_key)
            r_entry = right_segment_map.get(segment_key)
            if l_entry and r_entry:
                presence = "both"
                lv: float | None = _seg_value(l_entry)
                rv: float | None = _seg_value(r_entry)
                keys_dict: dict[str, Any] = l_entry.get("keys") or {}
                row_abs = _compute_absolute_delta(lv, rv)
                row_rel = _compute_relative_delta(row_abs, rv)
                row_dir = _compute_direction(row_abs, row_rel, flat_tolerance_relative)
            elif l_entry:
                presence = "current_only"
                lv = _seg_value(l_entry)
                rv = None
                keys_dict = l_entry.get("keys") or {}
                row_abs = lv  # delta = current_value per spec
                row_rel = None
                row_dir = "undefined"
            else:
                presence = "baseline_only"
                lv = None
                rv = _seg_value(r_entry or {})
                keys_dict = (r_entry or {}).get("keys") or {}
                row_abs = (-rv) if rv is not None else None  # delta = -baseline_value
                row_rel = None
                row_dir = "undefined"
            segmented_series.append(
                {
                    "keys": keys_dict,
                    "points": [
                        {
                            "current_value": lv,
                            "baseline_value": rv,
                            "delta": row_abs,
                            "delta_pct": row_rel,
                            "direction": row_dir,
                            "presence": presence,
                        }
                    ],
                }
            )

        # Compute scope summary from series values
        scope_lv: float | None = (
            sum(v for v in (_seg_value(s) for s in left_series_entries) if v is not None)
            if left_series_entries
            else None
        )
        scope_rv: float | None = (
            sum(v for v in (_seg_value(s) for s in right_series_entries) if v is not None)
            if right_series_entries
            else None
        )
        scope_abs = _compute_absolute_delta(scope_lv, scope_rv)
        scope_rel = _compute_relative_delta(scope_abs, scope_rv)
        scope_dir = _compute_direction(scope_abs, scope_rel, flat_tolerance_relative)

        seg_axes: list[dict[str, str]] = [{"kind": "dimension", "name": d} for d in dims]
        artifact = {
            **base,
            "comparison_type": "segmented_delta",
            "axes": seg_axes,
            "series": segmented_series,
            "scope_current_value": scope_lv,
            "scope_baseline_value": scope_rv,
            "scope_absolute_delta": scope_abs,
            "scope_relative_delta": scope_rel,
            "scope_direction": scope_dir,
        }
        artifact_name = f"{metric_name}_compare_segmented"
        summary = f"compare {metric_name} segmented: {len(segmented_series)} delta rows"

    provenance: dict[str, Any] = {
        "current_step_id": left_step_id,
        "baseline_step_id": right_step_id,
        "current_artifact_id": current_artifact_id,
        "baseline_artifact_id": baseline_artifact_id,
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
        reasoning=reasoning,
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
