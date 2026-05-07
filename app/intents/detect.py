from __future__ import annotations

import contextlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR
from app.core.intent.primitives import make_provenance, new_step_id
from app.core.semantic.step_metadata import build_step_semantic_metadata
from app.time_contracts import (
    TimeGrain,
    bucket_window,
    normalize_hour_boundary,
    previous_adjacent_window,
    recommended_minimum_window,
)
from app.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts

_SENSITIVITY_THRESHOLD: dict[str, float] = {
    "conservative": 2.5,
    "balanced": 2.0,
    "aggressive": 1.5,
}

_PERIOD_SHIFT_THRESHOLD: dict[str, float] = {
    "conservative": 0.30,
    "balanced": 0.20,
    "aggressive": 0.10,
}

_VALID_PROFILES: frozenset[str] = frozenset(
    {"auto", "spike_dip", "level_shift", "seasonal_residual"}
)

_MIN_POINTS_FOR_DETECTION = 3
_DEFAULT_MAX_SERIES = 20
_VALID_PATTERNS: frozenset[str] = frozenset({"point_anomaly", "period_shift"})


def _coerce_float(value: Any) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        if value is not None:
            return float(value)
    return None


def _slice_sort_key(candidate_slice: dict[str, Any] | None) -> str:
    if not candidate_slice:
        return ""
    return "|".join(f"{key}={candidate_slice[key]}" for key in sorted(candidate_slice))


def _detect_series_candidates(
    series: list[dict[str, Any]],
    *,
    candidate_slice: dict[str, Any] | None,
    threshold: float,
) -> list[dict[str, Any]]:
    numeric_values = [s["value"] for s in series if s["value"] is not None]
    n_points = len(numeric_values)
    if n_points < _MIN_POINTS_FOR_DETECTION:
        return []

    mean = sum(numeric_values) / n_points
    variance = sum((v - mean) ** 2 for v in numeric_values) / n_points
    std = math.sqrt(variance) if variance > 0 else 0.0

    candidates: list[dict[str, Any]] = []
    for bucket in series:
        val = bucket["value"]
        if val is None:
            continue
        z = (val - mean) / std if std > 0 else 0.0
        abs_z = abs(z)
        if abs_z <= threshold:
            continue

        deviation_abs: float = val - mean
        deviation_pct: float | None = (deviation_abs / abs(mean)) if mean != 0 else None

        if deviation_abs > 0:
            direction = "up"
        elif deviation_abs < 0:
            direction = "down"
        else:
            direction = "flat"

        flag_level = "high" if abs_z > 3.0 else ("medium" if abs_z > 2.0 else "low")
        candidates.append(
            {
                "candidate_type": "point_anomaly",
                "window": bucket["window"],
                "slice": dict(candidate_slice) if candidate_slice else None,
                "observed_value": val,
                "expected_value": mean,
                "deviation_abs": deviation_abs,
                "deviation_pct": deviation_pct,
                "candidate_score": abs_z,
                "flag_level": flag_level,
                "direction": direction,
            }
        )
    return candidates


def _resolve_patterns(raw_patterns: Any, *, profile: str) -> list[str]:
    if raw_patterns is None:
        return ["period_shift"] if profile == "level_shift" else ["point_anomaly"]
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise ValueError("detect: INVALID_ARGUMENT - patterns must be a non-empty list")
    patterns: list[str] = []
    for raw in raw_patterns:
        pattern = str(raw).strip()
        if pattern not in _VALID_PATTERNS:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - pattern '{pattern}' is not valid. "
                f"Must be one of: {sorted(_VALID_PATTERNS)}"
            )
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def _flag_level_for_period_shift(score: float) -> str:
    if score >= 0.50:
        return "high"
    if score >= 0.30:
        return "medium"
    return "low"


def _table_has_column(
    core: CoreEngine,
    *,
    engine: Any,
    engine_type: str,
    table_name: str,
    column_name: str,
) -> bool:
    compiled_query = core.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="profile_table_columns",
            params={"table_name": table_name},
        ),
        engine_type=engine_type,
    )
    rows = list(execute_compiled(engine, compiled_query).rows)
    return column_name in {str(row.get("column_name") or "") for row in rows}


def _query_scalar_window_values(
    core: CoreEngine,
    *,
    session_id: str,
    engine: Any,
    engine_type: str,
    table: str,
    qualified_table: str,
    metric_ref: str,
    metric_sql: str,
    all_dimensions: list[str],
    execution_context: Any,
    scope_raw: Any,
    start: str,
    end: str,
    granularity: str,
    split_by: str | None,
    split_by_expr: str | None,
) -> dict[str, dict[str, Any]]:
    mq_params: dict[str, Any] = {
        "table": table,
        "metric": metric_ref,
        "time_scope": {
            "mode": "single_window",
            "grain": granularity,
            "current": {"start": start, "end": end},
        },
    }
    if scope_raw:
        mq_params["scope"] = scope_raw
    resolved = normalize_metric_query_request(mq_params)
    core.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_ref,
        fallback_columns=all_dimensions,
    )
    scoped_query = core.build_scoped_query(session_id, resolved, engine_type=engine_type)

    select_exprs = [f"{metric_sql} AS value"]
    group_by: list[str] = []
    order_by: str | None = None
    if split_by is not None:
        if split_by_expr is None:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - split_by '{split_by}' did not resolve to a physical column"
            )
        select_exprs.insert(0, f"{split_by_expr} AS split_value")
        group_by.append("split_value")
        order_by = "split_value"

    compiled_query = core.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="aggregate_query",
            params={
                "table": qualified_table,
                "select": select_exprs,
                "group_by": group_by,
                "order_by": order_by,
                "scoped_query": scoped_query,
                "limit": 10000,
            },
        ),
        engine_type=engine_type,
        semantic_context={"metric_execution_context": execution_context},
    )
    result: dict[str, dict[str, Any]] = {}
    for row in execute_compiled(engine, compiled_query).rows:
        value = _coerce_float(row.get("value"))
        if split_by is None:
            result["__overall__"] = {"slice": None, "value": value}
        else:
            split_value = row.get("split_value")
            result[str(split_value)] = {
                "slice": {split_by: split_value},
                "value": value,
            }
    if split_by is None and "__overall__" not in result:
        result["__overall__"] = {"slice": None, "value": None}
    return result


def _detect_period_shift_candidates(
    *,
    current_values: dict[str, dict[str, Any]],
    baseline_values: dict[str, dict[str, Any]],
    current_window: dict[str, str],
    baseline_window: dict[str, str],
    threshold: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key, current_item in current_values.items():
        current_value = current_item.get("value")
        baseline_item = baseline_values.get(key)
        if baseline_item is None:
            continue
        baseline_value = baseline_item.get("value")
        if current_value is None or baseline_value is None:
            continue
        deviation_abs = current_value - baseline_value
        if baseline_value != 0:
            deviation_pct_value = deviation_abs / abs(baseline_value)
            deviation_pct: float | None = deviation_pct_value
            candidate_score = abs(deviation_pct_value)
        else:
            deviation_pct = None
            candidate_score = abs(deviation_abs)
        if candidate_score < threshold:
            continue
        if deviation_abs > 0:
            direction = "up"
        elif deviation_abs < 0:
            direction = "down"
        else:
            direction = "flat"
        candidates.append(
            {
                "candidate_type": "period_shift",
                "window": dict(current_window),
                "baseline_window": dict(baseline_window),
                "slice": current_item.get("slice"),
                "observed_value": current_value,
                "expected_value": baseline_value,
                "deviation_abs": deviation_abs,
                "deviation_pct": deviation_pct,
                "candidate_score": candidate_score,
                "flag_level": _flag_level_for_period_shift(candidate_score),
                "direction": direction,
            }
        )
    return candidates


def run_detect_intent(
    core: CoreEngine, ports: RuntimePorts, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `detect` intent: scan a metric time range for anomaly candidates.

    Applies requested candidate patterns to flag anomaly candidates.

    time_scope must use the observe-aligned range shape plus top-level granularity.
    Empty semantics: total_candidate_count = 0 is a valid success outcome —
    the artifact is committed even when no candidates are found.
    """
    p = params or {}

    metric_ref: str = p.get("metric") or ""
    if not metric_ref:
        raise ValueError("detect intent requires 'metric'")
    metric_ref = core.normalize_intent_metric_ref(metric_ref)
    metric_name = core.metric_name_from_ref(metric_ref)

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("detect intent requires 'time_scope'")

    # ── Validate and parse time_scope (range schema) ─────────────────────────
    kind = time_scope_raw.get("kind")
    if kind != "range":
        raise ValueError(
            f"detect: INVALID_ARGUMENT - time_scope.kind must be 'range', got '{kind}'"
        )

    granularity: str = str(p.get("granularity") or "").lower()
    if granularity not in {"hour", "day", "week", "month"}:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - granularity must be one of "
            f"'hour', 'day', 'week', 'month', got '{granularity}'"
        )
    granularity_typed = cast("TimeGrain", granularity)

    start_str: str = str(time_scope_raw.get("start") or "").strip()
    end_str: str = str(time_scope_raw.get("end") or "").strip()
    if not start_str or not end_str:
        raise ValueError("detect: time_scope requires 'start' and 'end'")
    if granularity_typed == "hour":
        start_str = normalize_hour_boundary(start_str, label="time_scope.start")
        end_str = normalize_hour_boundary(end_str, label="time_scope.end")
        if datetime.fromisoformat(start_str) >= datetime.fromisoformat(end_str):
            raise ValueError(
                f"detect: INVALID_ARGUMENT - time_scope.start ('{start_str}') "
                f"must be before end ('{end_str}')"
            )
    elif start_str >= end_str:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - time_scope.start ('{start_str}') "
            f"must be before end ('{end_str}')"
        )

    resolved_time_scope: dict[str, Any] = {
        "kind": "range",
        "start": start_str,
        "end": end_str,
    }

    # ── Validate sensitivity ───────────────────────────────────────────────────
    sensitivity: str = str(p.get("sensitivity") or "balanced").lower()
    if sensitivity not in _SENSITIVITY_THRESHOLD:
        raise ValueError(
            f"detect sensitivity='{sensitivity}' is not valid. "
            f"Must be one of: {sorted(_SENSITIVITY_THRESHOLD)}"
        )

    # ── Validate and normalise profile ────────────────────────────────────────
    profile: str = str(p.get("profile") or "auto").lower()
    if profile not in _VALID_PROFILES:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - profile='{profile}' is not valid. "
            f"Must be one of: {sorted(_VALID_PROFILES)}"
        )

    patterns = _resolve_patterns(p.get("patterns"), profile=profile)

    # ── Read split_by ─────────────────────────────────────────────────────────
    split_by_raw = p.get("split_by")
    split_by: str | None = None
    if split_by_raw is not None and not isinstance(split_by_raw, str):
        raise ValueError("detect: INVALID_ARGUMENT - split_by must be a string")
    if isinstance(split_by_raw, str):
        split_by = split_by_raw.strip() or None

    # ── Read and validate max_series ──────────────────────────────────────────
    max_series_raw = p.get("max_series")
    max_series: int | None = _DEFAULT_MAX_SERIES if split_by is not None else None
    if max_series_raw is not None:
        max_series = int(max_series_raw)
        if max_series <= 0:
            raise ValueError("detect: INVALID_ARGUMENT - max_series must be > 0")

    # ── Read and validate limit ───────────────────────────────────────────────
    limit_raw = p.get("limit")
    limit: int | None = None
    if limit_raw is not None:
        limit = int(limit_raw)
        if limit <= 0:
            raise ValueError("detect: INVALID_ARGUMENT - limit must be > 0")

    # ── Scope passthrough ─────────────────────────────────────────────────────
    scope_raw = p.get("scope") or None

    # ── Resolve metric ─────────────────────────────────────────────────────────
    execution_context = core.resolve_metric_execution_context(metric_ref, session_id=session_id)
    table = execution_context.table_name

    all_dimensions = core.resolve_metric_dimensions(metric_ref)
    engine_resolution = core.resolve_engine_for_session(session_id, [table])
    if not isinstance(engine_resolution, tuple) or len(engine_resolution) != 3:
        engine_resolution = core.resolve_engine([table])
    engine, engine_type, qualified = engine_resolution
    metric_sql = core.resolve_metric_sql_for_execution(
        metric_ref,
        execution_context,
        engine_type=engine_type,
    )
    if all_dimensions is None:
        raise ValueError(f"Metric '{metric_name}' not found or not published")
    split_by_expr: str | None = None
    if split_by is not None:
        if split_by not in all_dimensions:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - split_by '{split_by}' is not available "
                f"for metric '{metric_name}'"
            )
        try:
            split_by_expr = core.resolve_scope_constraint_column(
                split_by,
                metric_ref=metric_ref,
                table_name=table,
            )
        except ValueError:
            fallback_column = split_by.removeprefix("dimension.")
            if not _table_has_column(
                core,
                engine=engine,
                engine_type=engine_type,
                table_name=qualified.get(table, table),
                column_name=fallback_column,
            ):
                raise ValueError(
                    f"detect: INVALID_ARGUMENT - split_by '{split_by}' did not resolve "
                    "to an executable physical column"
                ) from None
            split_by_expr = fallback_column

    # ── Build time-series query ────────────────────────────────────────────────
    mq_params: dict[str, Any] = {
        "table": table,
        "metric": metric_ref,
        "time_scope": {
            "mode": "single_window",
            "grain": granularity,
            "current": {"start": start_str, "end": end_str},
        },
    }
    if scope_raw:
        mq_params["scope"] = scope_raw

    resolved = normalize_metric_query_request(mq_params)
    core.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_ref,
        fallback_columns=all_dimensions,
    )
    scoped_query = core.build_scoped_query(session_id, resolved, engine_type=engine_type)
    qualified_table = qualified.get(table, table)

    time_col = resolved.resolved_time_axis.analysis_time_expr
    bucket_expr = f"DATE_TRUNC('{granularity}', {time_col})"
    select_exprs = [
        f"{bucket_expr} AS bucket_start",
        f"{metric_sql} AS value",
    ]
    group_by = ["bucket_start"]
    order_by = "bucket_start"
    if split_by is not None:
        if split_by_expr is None:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - split_by '{split_by}' did not resolve to a physical column"
            )
        select_exprs.insert(1, f"{split_by_expr} AS split_value")
        group_by.append("split_value")
        order_by = "split_value, bucket_start"

    step_id = new_step_id()
    compiled_query = core.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="aggregate_query",
            params={
                "table": qualified_table,
                "select": select_exprs,
                "group_by": group_by,
                "order_by": order_by,
                "scoped_query": scoped_query,
                "limit": 10000,
            },
        ),
        engine_type=engine_type,
        semantic_context={
            "metric_execution_context": execution_context,
        },
    )

    now = datetime.now(UTC).isoformat()
    rows = list(execute_compiled(engine, compiled_query).rows)
    provenance = make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)

    # ── Build one or more series from query rows ──────────────────────────────
    series_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket_raw = row.get("bucket_start")
        val = _coerce_float(row.get("value"))
        if bucket_raw is None:
            continue
        try:
            window = bucket_window(bucket_raw, granularity_typed)
        except (ValueError, TypeError):
            bucket_str = str(bucket_raw)
            window = {"start": bucket_str, "end": bucket_str}
        if split_by is None:
            series_key = "__overall__"
            candidate_slice = None
        else:
            split_value = row.get("split_value")
            series_key = str(split_value)
            candidate_slice = {split_by: split_value}
        series_entry = series_by_key.setdefault(
            series_key,
            {"slice": candidate_slice, "series": [], "numeric_count": 0},
        )
        series_entry["series"].append({"window": window, "value": val})
        if val is not None:
            series_entry["numeric_count"] += 1

    if split_by is None:
        selected_series = list(series_by_key.values()) or [
            {"slice": None, "series": [], "numeric_count": 0}
        ]
        eligible_series_count = 1
        scanned_series_count = 1
    else:
        eligible_series = sorted(
            series_by_key.values(),
            key=lambda item: (
                -int(item["numeric_count"]),
                _slice_sort_key(cast("dict[str, Any] | None", item["slice"])),
            ),
        )
        eligible_series_count = len(eligible_series)
        assert max_series is not None
        selected_series = eligible_series[:max_series]
        scanned_series_count = len(selected_series)
    excluded_series_count = max(eligible_series_count - scanned_series_count, 0)

    # ── Detectability assessment ───────────────────────────────────────────────
    detectability_issues: list[dict[str, Any]] = []
    scanned_numeric_counts = [int(item["numeric_count"]) for item in selected_series]
    n_points = max(scanned_numeric_counts, default=0)

    if n_points < _MIN_POINTS_FOR_DETECTION:
        guidance = {
            "reason": "insufficient_points",
            "observed_points": n_points,
            "minimum_points_required": _MIN_POINTS_FOR_DETECTION,
            "minimum_window_buckets": _MIN_POINTS_FOR_DETECTION,
            "recommended_next_action": "expand_scan_window",
            "recommended_current_window": recommended_minimum_window(
                end_str, grain=granularity_typed, bucket_count=_MIN_POINTS_FOR_DETECTION
            ),
            "fallback_path": {
                "kind": "compare_plus_decompose",
                "message": (
                    "If you already have a suspected abnormal window, run observe(current) + "
                    "observe(previous_adjacent_equal_length) + compare + decompose."
                ),
            },
        }
        detectability_issues.append(
            {
                "code": "insufficient_points",
                "severity": "warning",
                "message": (
                    f"Only {n_points} numeric point(s) found in the scan window; "
                    f"minimum {_MIN_POINTS_FOR_DETECTION} required for reliable detection."
                ),
            }
        )
        detectability_status = "needs_attention"
    else:
        guidance = None
        detectability_status = "detectable"
    if excluded_series_count > 0:
        detectability_issues.append(
            {
                "code": "series_limit_applied",
                "severity": "warning",
                "message": (
                    f"Scanned {scanned_series_count} of {eligible_series_count} eligible "
                    f"series because max_series={max_series}."
                ),
            }
        )

    # ── Candidate detection ───────────────────────────────────────────────────
    _raw_candidates: list[dict[str, Any]] = []
    threshold = _SENSITIVITY_THRESHOLD[sensitivity]

    if "point_anomaly" in patterns:
        for item in selected_series:
            _raw_candidates.extend(
                _detect_series_candidates(
                    cast("list[dict[str, Any]]", item["series"]),
                    candidate_slice=cast("dict[str, Any] | None", item["slice"]),
                    threshold=threshold,
                )
            )

    if "period_shift" in patterns:
        baseline_window = previous_adjacent_window(start_str, end_str, grain=granularity_typed)
        current_window = {"start": start_str, "end": end_str}
        current_values = _query_scalar_window_values(
            core,
            session_id=session_id,
            engine=engine,
            engine_type=engine_type,
            table=table,
            qualified_table=qualified_table,
            metric_ref=metric_ref,
            metric_sql=metric_sql,
            all_dimensions=all_dimensions,
            execution_context=execution_context,
            scope_raw=scope_raw,
            start=start_str,
            end=end_str,
            granularity=granularity,
            split_by=split_by,
            split_by_expr=split_by_expr,
        )
        baseline_values = _query_scalar_window_values(
            core,
            session_id=session_id,
            engine=engine,
            engine_type=engine_type,
            table=table,
            qualified_table=qualified_table,
            metric_ref=metric_ref,
            metric_sql=metric_sql,
            all_dimensions=all_dimensions,
            execution_context=execution_context,
            scope_raw=scope_raw,
            start=baseline_window["start"],
            end=baseline_window["end"],
            granularity=granularity,
            split_by=split_by,
            split_by_expr=split_by_expr,
        )
        _raw_candidates.extend(
            _detect_period_shift_candidates(
                current_values=current_values,
                baseline_values=baseline_values,
                current_window=current_window,
                baseline_window=baseline_window,
                threshold=_PERIOD_SHIFT_THRESHOLD[sensitivity],
            )
        )

    # ── Canonical ranking ──────────────────────────────────────────────────────
    _raw_candidates.sort(
        key=lambda c: (
            -c["candidate_score"],
            -(abs(c["deviation_abs"]) if c["deviation_abs"] is not None else 0.0),
            c["window"]["start"],
            c.get("candidate_type") or "",
            _slice_sort_key(c["slice"]),
        )
    )

    # ── Assign candidate_refs ──────────────────────────────────────────────────
    # (artifact_id filled post-insert; step_id is stable)
    all_candidates: list[dict[str, Any]] = []
    for i, c in enumerate(_raw_candidates):
        all_candidates.append(
            {
                "candidate_ref": {
                    "artifact_ref": {
                        "session_id": session_id,
                        "step_id": step_id,
                        "step_type": "detect",
                        "artifact_id": None,  # patched after _insert_artifact
                    },
                    "item_ref": {"collection": "candidates", "index": i, "key": None},
                },
                **{k: c[k] for k in c if k != "candidate_ref"},
            }
        )

    total_candidate_count = len(all_candidates)

    # ── Apply limit truncation ─────────────────────────────────────────────────
    if limit is not None and total_candidate_count > limit:
        returned_candidates = all_candidates[:limit]
        truncated = True
    else:
        returned_candidates = all_candidates
        truncated = False
    returned_candidate_count = len(returned_candidates)

    # ── Build artifact ─────────────────────────────────────────────────────────
    artifact: dict[str, Any] = {
        "artifact_type": "anomaly_candidates",
        "artifact_schema_version": "v1",
        "metric": metric_name,
        "time_scope": resolved_time_scope,
        "granularity": granularity,
        "scope": scope_raw,
        "split_by": split_by,
        "profile": profile,
        "sensitivity": sensitivity,
        "patterns": patterns,
        "detectability": {
            "status": detectability_status,
            "issues": detectability_issues,
            "guidance": guidance,
        },
        "scan_summary": {
            "scanned_series_count": scanned_series_count,
            "eligible_series_count": eligible_series_count,
            "excluded_series_count": excluded_series_count,
            "total_candidate_count": total_candidate_count,
        },
        "candidates": returned_candidates,
        "truncation": {
            "returned_candidate_count": returned_candidate_count,
            "total_candidate_count": total_candidate_count,
            "truncated": truncated,
        },
        "analytical_metadata": {
            "timezone": None,
            "data_complete": None,
            "baseline_method": {
                "patterns": patterns,
                "methods": {
                    "point_anomaly": "scan_window_zscore",
                    "period_shift": "previous_adjacent_equal_length",
                },
            },
        },
        "provenance": {
            "artifact_ref": {
                "session_id": session_id,
                "step_id": step_id,
                "step_type": "detect",
                "artifact_id": None,  # patched after _insert_artifact
            },
            "source_metric_ref": metric_ref,
            "detector_version": "1.2",
            "projection_ref": None,
        },
        "execution_metadata": {
            "query_hash": provenance.get("query_hash", ""),
            "engine": engine_type,
            "executed_at": now,
        },
    }

    artifact_name = f"{metric_name}_detect_candidates"
    candidate_noun = "candidate" if total_candidate_count == 1 else "candidates"
    summary = (
        f"detect {metric_name} [{start_str} → {end_str}] "
        f"sensitivity={sensitivity}: {total_candidate_count} {candidate_noun}"
    )

    artifact_id = core.commit_artifact_with_extraction(
        session_id,
        step_id,
        "anomaly_candidates",
        artifact_name,
        artifact,
        step_type="detect",
    )

    # Patch artifact_id now that it is known
    artifact["provenance"]["artifact_ref"]["artifact_id"] = artifact_id
    for c in artifact["candidates"]:
        c["candidate_ref"]["artifact_ref"]["artifact_id"] = artifact_id

    result: dict[str, Any] = {
        "intent_type": "detect",
        "step_type": "detect",
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": "detect",
        },
        "artifact_id": artifact_id,
        **artifact,
    }

    core.insert_step(
        step_id,
        session_id,
        "detect",
        summary,
        result,
        provenance=provenance,
        semantic_metadata=build_step_semantic_metadata(compiled_query),
    )
    return result
