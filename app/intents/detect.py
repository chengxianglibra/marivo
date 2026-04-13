from __future__ import annotations

import contextlib
import math
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from typing import TYPE_CHECKING, Any

from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR
from app.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from app.service import SemanticLayerService

_SENSITIVITY_THRESHOLD: dict[str, float] = {
    "conservative": 2.5,
    "balanced": 2.0,
    "aggressive": 1.5,
}

_VALID_PROFILES: frozenset[str] = frozenset(
    {"auto", "spike_dip", "level_shift", "seasonal_residual"}
)

_MIN_POINTS_FOR_DETECTION = 3


def run_detect_intent(
    svc: SemanticLayerService, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `detect` intent: scan a metric time range for anomaly candidates.

    Applies z-score against the scan-window mean/std to flag candidate anomalies.

    time_scope must use mode="single_window" with explicit grain and current window.
    Empty semantics: total_candidate_count = 0 is a valid success outcome —
    the artifact is committed even when no candidates are found.
    """
    p = params or {}

    metric_ref: str = p.get("metric") or ""
    if not metric_ref:
        raise ValueError("detect intent requires 'metric'")
    metric_ref = svc.normalize_intent_metric_ref(metric_ref)
    metric_name = svc.metric_name_from_ref(metric_ref)

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("detect intent requires 'time_scope'")

    # ── Validate and parse time_scope (mode/grain/current schema) ─────────────
    mode = time_scope_raw.get("mode")
    if mode != "single_window":
        raise ValueError(
            f"detect: INVALID_ARGUMENT - time_scope.mode must be 'single_window', got '{mode}'"
        )

    grain: str = str(time_scope_raw.get("grain") or "").lower()
    if grain not in {"hour", "day", "week", "month"}:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - time_scope.grain must be one of "
            f"'hour', 'day', 'week', 'month', got '{grain}'"
        )

    current = time_scope_raw.get("current")
    if not isinstance(current, dict):
        raise ValueError("detect: INVALID_ARGUMENT - time_scope.current is required")
    start_str: str = str(current.get("start") or "").strip()
    end_str: str = str(current.get("end") or "").strip()
    if not start_str or not end_str:
        raise ValueError("detect: time_scope.current requires 'start' and 'end'")
    if start_str >= end_str:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - time_scope.current.start ('{start_str}') "
            f"must be before end ('{end_str}')"
        )

    resolved_time_scope: dict[str, Any] = {
        "mode": "single_window",
        "grain": grain,
        "current": {"start": start_str, "end": end_str},
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

    # ── Read split_by (echo-only in v1; multi-series scanning not yet implemented) ─
    split_by_raw = p.get("split_by")
    split_by: str | None = None
    if isinstance(split_by_raw, str):
        split_by = split_by_raw.strip() or None

    # ── Read and validate max_series ──────────────────────────────────────────
    max_series_raw = p.get("max_series")
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
    execution_context = svc._resolve_metric_execution_context(metric_ref)
    table = execution_context.table_name

    metric_sql = svc.resolve_metric_sql_for_execution(metric_ref, execution_context)
    all_dimensions = svc.resolve_metric_dimensions(metric_ref)
    if metric_sql is None or all_dimensions is None:
        raise ValueError(f"Metric '{metric_name}' not found or not published")

    # ── Build time-series query ────────────────────────────────────────────────
    engine, engine_type, qualified = svc._resolve_engine([table])

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

    resolved = normalize_metric_query_request(mq_params)
    svc._resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_ref,
        fallback_columns=all_dimensions,
    )
    scoped_query = svc._build_scoped_query(session_id, resolved)
    qualified_table = qualified.get(table, table)

    time_col = resolved.resolved_time_axis.analysis_time_expr
    bucket_expr = f"DATE_TRUNC('{grain}', {time_col})"

    step_id = svc._new_step_id()
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
                "group_by": ["bucket_start"],
                "order_by": "bucket_start",
                "scoped_query": scoped_query,
                "limit": 10000,
            },
        ),
        engine_type=engine_type,
        semantic_context={},
    )

    now = datetime.now(UTC).isoformat()
    rows = list(execute_compiled(engine, compiled_query).rows)
    provenance = svc._make_provenance(
        compiled_query.sql, compiled_query.params, engine_type=engine_type
    )

    # ── Build series from query rows ───────────────────────────────────────────
    series: list[dict[str, Any]] = []
    for row in rows:
        bucket_raw = row.get("bucket_start")
        raw_value = row.get("value")
        val: float | None = None
        with contextlib.suppress(TypeError, ValueError):
            if raw_value is not None:
                val = float(raw_value)
        if bucket_raw is None:
            continue
        bucket_str = str(bucket_raw)[:10]
        try:
            bucket_date = _date.fromisoformat(bucket_str)
            if grain == "hour":
                bucket_end = (
                    datetime.fromisoformat(str(bucket_raw)) + timedelta(hours=1)
                ).isoformat()
            elif grain == "day":
                bucket_end = (bucket_date + timedelta(days=1)).isoformat()
            elif grain == "week":
                bucket_end = (bucket_date + timedelta(weeks=1)).isoformat()
            elif grain == "month":
                if bucket_date.month == 12:
                    bucket_end = bucket_date.replace(
                        year=bucket_date.year + 1, month=1, day=1
                    ).isoformat()
                else:
                    bucket_end = bucket_date.replace(month=bucket_date.month + 1, day=1).isoformat()
            else:
                bucket_end = (bucket_date + timedelta(days=1)).isoformat()
        except (ValueError, TypeError):
            bucket_end = bucket_str
        series.append({"window": {"start": bucket_str, "end": bucket_end}, "value": val})

    # ── Detectability assessment ───────────────────────────────────────────────
    detectability_issues: list[dict[str, Any]] = []
    numeric_values = [s["value"] for s in series if s["value"] is not None]
    n_points = len(numeric_values)

    if n_points < _MIN_POINTS_FOR_DETECTION:
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
        detectability_status = "detectable"

    # ── Z-score detection ──────────────────────────────────────────────────────
    _raw_candidates: list[dict[str, Any]] = []
    threshold = _SENSITIVITY_THRESHOLD[sensitivity]

    if n_points >= _MIN_POINTS_FOR_DETECTION:
        mean = sum(numeric_values) / n_points
        variance = sum((v - mean) ** 2 for v in numeric_values) / n_points
        std = math.sqrt(variance) if variance > 0 else 0.0

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

            _raw_candidates.append(
                {
                    "window": bucket["window"],
                    "slice": None,
                    "observed_value": val,
                    "expected_value": mean,
                    "deviation_abs": deviation_abs,
                    "deviation_pct": deviation_pct,
                    "candidate_score": abs_z,
                    "flag_level": flag_level,
                    "direction": direction,
                }
            )

    # ── Canonical ranking ──────────────────────────────────────────────────────
    _raw_candidates.sort(
        key=lambda c: (
            -c["candidate_score"],
            -(abs(c["deviation_abs"]) if c["deviation_abs"] is not None else 0.0),
            c["window"]["start"],
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
        "scope": scope_raw,
        "split_by": split_by,
        "profile": profile,
        "sensitivity": sensitivity,
        "detectability": {
            "status": detectability_status,
            "issues": detectability_issues,
        },
        "scan_summary": {
            "scanned_series_count": 1,
            "eligible_series_count": 1,
            "excluded_series_count": 0,
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
            "baseline_method": "zscore",
        },
        "provenance": {
            "artifact_ref": {
                "session_id": session_id,
                "step_id": step_id,
                "step_type": "detect",
                "artifact_id": None,  # patched after _insert_artifact
            },
            "source_metric_ref": metric_ref,
            "detector_version": "1.0",
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

    artifact_id = svc._commit_artifact_with_extraction(
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

    svc._insert_step(
        step_id,
        session_id,
        "detect",
        summary,
        result,
        provenance=provenance,
        semantic_metadata=svc.build_step_semantic_metadata(compiled_query),
    )
    return result
