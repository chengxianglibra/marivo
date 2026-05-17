"""Forecast atomic intent runner (Phase 3b-6).

Projects a single time-series observe artifact into future buckets using
a v1 algorithm (level carry-forward or OLS linear trend).

No ML or external numeric dependencies are introduced; all computation
uses the standard library (math module).
"""

from __future__ import annotations

import hashlib
import itertools
import math
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_step_result

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_VALID_GRANULARITIES: frozenset[str] = frozenset({"hour", "day", "week", "month"})
_MAX_HORIZON = 90
_AOI_PARAM_KEYS: frozenset[str] = frozenset({"source_artifact_id", "horizon"})
_DEFAULT_INTERVAL_LEVEL = 0.95

# Minimum usable history points required per profile (v1).
_MIN_POINTS: dict[str, int] = {
    "level": 1,
    "trend": 3,
}

# Long-horizon warning threshold: horizon > usable_points * 2
_LONG_HORIZON_MULTIPLIER = 2

# z-value lookup for symmetric normal prediction intervals (level → z)
_Z_LEVEL_TABLE: dict[float, float] = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}

# z-value for 95% prediction interval (normal approximation)
_Z_95 = 1.96


# ── Pure statistical helpers ──────────────────────────────────────────────────


def _ols_fit(values: list[float]) -> tuple[float, float]:
    """Return (intercept a, slope b) for OLS fit: y_hat = a + b*x, x=0..n-1."""
    n = len(values)
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))
    ss_xy = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    b = ss_xy / ss_xx if ss_xx > 0 else 0.0
    a = y_mean - b * x_mean
    return a, b


def _ols_residual_std(values: list[float], a: float, b: float) -> float:
    """Unbiased residual standard deviation from OLS fit."""
    n = len(values)
    if n <= 2:
        return 0.0
    ss_res = sum((v - (a + b * i)) ** 2 for i, v in enumerate(values))
    return math.sqrt(ss_res / (n - 2))


# ── Bucket window advancement ─────────────────────────────────────────────────


def _advance_bucket(start_str: str, granularity: str) -> tuple[str, str]:
    """Given a bucket start string, return (start, end) for the next bucket."""
    try:
        if granularity == "hour":
            dt = datetime.fromisoformat(start_str)
            return dt.isoformat(), (dt + timedelta(hours=1)).isoformat()
        else:
            d = _date.fromisoformat(start_str[:10])
            if granularity == "day":
                end_d = d + timedelta(days=1)
            elif granularity == "week":
                end_d = d + timedelta(weeks=1)
            else:  # month
                if d.month == 12:
                    end_d = d.replace(year=d.year + 1, month=1, day=1)
                else:
                    end_d = d.replace(month=d.month + 1, day=1)
            return d.isoformat(), end_d.isoformat()
    except (ValueError, TypeError):
        return start_str, start_str


def _generate_future_windows(
    last_observed_end: str, granularity: str, horizon: int
) -> list[tuple[str, str]]:
    """Return list of (start, end) strings for `horizon` future buckets."""
    windows: list[tuple[str, str]] = []
    current_start = last_observed_end
    for _ in range(horizon):
        start, end = _advance_bucket(current_start, granularity)
        windows.append((start, end))
        current_start = end
    return windows


# ── Runner ────────────────────────────────────────────────────────────────────


def run_forecast_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `forecast` intent: project a time-series into future buckets.

    Consumes a single `time_series` observe artifact from the same session and
    applies a v1 forecast profile selected by available history.

    Empty semantics: forecast must produce ≥1 bucket (non-empty success).
    Insufficient history raises ValueError without committing an artifact.
    """
    p = params or {}
    now = datetime.now(UTC).isoformat()

    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "forecast: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; forecast accepts only AOI request fields"
        )

    source_artifact_id_raw = p.get("source_artifact_id")
    source_artifact_id = (
        source_artifact_id_raw.strip() if isinstance(source_artifact_id_raw, str) else ""
    )

    # ── Request parameter validation ───────────────────────────────────────────
    horizon_raw = p.get("horizon")
    if horizon_raw is None:
        raise ValueError("forecast: INVALID_ARGUMENT - 'horizon' is required")
    try:
        horizon = int(horizon_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("forecast: INVALID_ARGUMENT - 'horizon' must be an integer") from exc
    if not (1 <= horizon <= _MAX_HORIZON):
        raise ValueError(
            f"forecast: INVALID_ARGUMENT - horizon must be in [1, {_MAX_HORIZON}], got {horizon}"
        )

    interval_level = _DEFAULT_INTERVAL_LEVEL

    # ── Resolve source artifact ────────────────────────────────────────────────
    if not source_artifact_id:
        raise ValueError("forecast: INVALID_ARGUMENT - source_artifact_id is required")
    resolved = runtime.resolve_artifact_with_step_by_id(session_id, source_artifact_id)
    if resolved is None:
        raise ValueError(
            "forecast: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"source_artifact_id '{source_artifact_id}'"
        )
    src_step_id, source_artifact = resolved
    resolved_artifact_id = source_artifact_id

    # ── Validate observation_type ─────────────────────────────────────────────
    artifact_obs_type: str | None = source_artifact.get("observation_type")
    if artifact_obs_type != "time_series":
        raise ValueError(
            f"forecast: INVALID_ARGUMENT - source_artifact_id must point to a 'time_series' observe "
            f"artifact, got observation_type='{artifact_obs_type}'"
        )
    # ── Derive granularity from source artifact ───────────────────────────────
    granularity: str = str(source_artifact.get("granularity") or "").lower()
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            f"forecast: UNSUPPORTED_OPERATION - source artifact granularity '{granularity}' "
            f"is not supported; must be one of {sorted(_VALID_GRANULARITIES)}"
        )

    # ── Extract and validate series ───────────────────────────────────────────
    raw_series: list[dict[str, Any]] = source_artifact.get("series") or []
    observed_points = len(raw_series)

    usable: list[tuple[str, str, float]] = []  # (start, end, value)
    for bucket in raw_series:
        w = bucket.get("window") or {}
        val_raw = bucket.get("value")
        if val_raw is None:
            continue
        try:
            val = float(val_raw)
        except (TypeError, ValueError):
            continue
        usable.append((str(w.get("start") or ""), str(w.get("end") or ""), val))

    usable_count = len(usable)
    dropped_points = observed_points - usable_count

    # Resolve a concrete profile based on available history.
    resolved_profile = "trend" if usable_count >= _MIN_POINTS["trend"] else "level"

    min_required = _MIN_POINTS.get(resolved_profile, 1)
    if usable_count < min_required:
        raise ValueError(
            f"forecast: INSUFFICIENT_HISTORY - profile='{resolved_profile}' requires at least "
            f"{min_required} usable history point(s), but only {usable_count} available"
        )

    # Last observed window (derived from last usable bucket)
    last_obs_start = usable[-1][0]
    last_obs_end = usable[-1][1]
    last_observed_window = {"start": last_obs_start, "end": last_obs_end}

    # ── Forecastability assessment ─────────────────────────────────────────────
    forecastability_issues: list[dict[str, Any]] = []

    if horizon > usable_count * _LONG_HORIZON_MULTIPLIER:
        forecastability_issues.append(
            {
                "code": "long_horizon_warning",
                "severity": "warning",
                "message": (
                    f"horizon={horizon} is more than {_LONG_HORIZON_MULTIPLIER}× the number of "
                    f"usable history points ({usable_count}); forecast reliability may be low."
                ),
            }
        )

    has_error = any(i["severity"] == "error" for i in forecastability_issues)
    has_warning = any(i["severity"] == "warning" for i in forecastability_issues)
    if has_error:
        forecastability_status = "not_forecastable"
    elif has_warning:
        forecastability_status = "needs_attention"
    else:
        forecastability_status = "forecastable"

    if forecastability_status == "not_forecastable":
        error_msgs = [i["message"] for i in forecastability_issues if i["severity"] == "error"]
        raise ValueError(f"forecast: not_forecastable: {'; '.join(error_msgs)}")

    # ── Compute forecast ───────────────────────────────────────────────────────
    values = [u[2] for u in usable]
    future_windows = _generate_future_windows(last_obs_end, granularity, horizon)

    trend_assumption: str
    seasonality_assumption: str
    model_family: str
    forecast_buckets: list[dict[str, Any]] = []

    if resolved_profile == "level":
        # Carry-forward: last observed value for all future buckets
        last_value = values[-1]
        trend_assumption = "none"
        seasonality_assumption = "not_applicable"
        model_family = "level"

        for idx, (w_start, w_end) in enumerate(future_windows, start=1):
            forecast_buckets.append(
                {
                    "bucket_index": idx,
                    "window": {"start": w_start, "end": w_end},
                    "point_forecast": last_value,
                    "prediction_interval": None,
                }
            )

    else:
        # trend → OLS linear trend with residual interval
        a, b = _ols_fit(values)
        resid_std = _ols_residual_std(values, a, b)
        n = len(values)

        trend_assumption = "included"
        seasonality_assumption = "none"
        model_family = "ols_linear"

        z = _z_for_level(interval_level)

        for idx, (w_start, w_end) in enumerate(future_windows, start=1):
            x_future = n + idx - 1  # extrapolate at position n, n+1, ...
            point = a + b * x_future

            if resid_std > 0:
                lower = point - z * resid_std
                upper = point + z * resid_std
                prediction_interval: dict[str, Any] | None = {
                    "level": interval_level,
                    "lower": lower,
                    "upper": upper,
                }
            else:
                prediction_interval = None

            forecast_buckets.append(
                {
                    "bucket_index": idx,
                    "window": {"start": w_start, "end": w_end},
                    "point_forecast": point,
                    "prediction_interval": prediction_interval,
                }
            )

    # Non-empty semantics guard (should always pass given horizon >= 1)
    if not forecast_buckets:
        raise ValueError("forecast: no forecast buckets produced; cannot commit empty artifact")

    # ── Build artifact ─────────────────────────────────────────────────────────
    step_id = new_step_id()

    _hash_input = f"{resolved_artifact_id}:{resolved_profile}:{granularity}:{horizon}"
    query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]

    metric_name: str = source_artifact.get("metric") or ""
    source_time_scope: dict[str, Any] = source_artifact.get("time_scope") or {}

    source_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": src_step_id,
        "artifact_id": resolved_artifact_id,
        "observation_type": "time_series",
    }

    artifact: dict[str, Any] = {
        "observation_type": "forecast_series",
        "artifact_schema_version": "v1",
        "derivation_version": "1.0",
        "metric": metric_name,
        "source_ref": source_ref_out,
        "source_granularity": granularity,
        "source_time_scope": source_time_scope,
        "profile": resolved_profile,
        "interval_level": interval_level,
        "forecastability": {
            "status": forecastability_status,
            "issues": forecastability_issues,
        },
        "history_summary": {
            "observed_points": observed_points,
            "usable_points": usable_count,
            "dropped_points": dropped_points,
            "last_observed_window": last_observed_window,
        },
        "forecast": forecast_buckets,
        "source_lineage": {
            "source_artifact_ref": source_ref_out,
            "source_schema_version": str(source_artifact.get("schema_version") or "1.0"),
            "source_metric_contract_version": source_artifact.get("metric_contract_version"),
        },
        "analytical_metadata": {
            "timezone": (source_artifact.get("analytical_metadata") or {}).get("timezone"),
            "data_complete": (source_artifact.get("analytical_metadata") or {}).get(
                "data_complete"
            ),
            "trend_assumption": trend_assumption,
            "seasonality_assumption": seasonality_assumption,
        },
        "execution_metadata": {
            "engine": "none",
            "executed_at": now,
            "model_family": model_family,
        },
    }

    artifact_name = f"{metric_name}_forecast_series"
    summary = (
        f"forecast {metric_name} [{resolved_profile}] granularity={granularity} "
        f"horizon={horizon}: {len(forecast_buckets)} future bucket(s)"
    )

    provenance: dict[str, Any] = {
        "query_hash": query_hash,
        "engine": "none",
        "timestamp": now,
        "param_count": 0,
    }
    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "forecast",
        "forecast_series",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
    )
    return result


def _z_for_level(level: float) -> float:
    """Approximate z-value for a symmetric normal confidence interval.

    Uses a simple lookup for common levels; falls back to _Z_95 for others.
    This is intentionally coarse — v1 does not require high-precision quantiles.
    """
    for target, z in _Z_LEVEL_TABLE.items():
        if abs(level - target) < 1e-9:
            return z
    # Linear interpolation between nearest bracketing entries as a fallback
    sorted_levels = sorted(_Z_LEVEL_TABLE)
    if level <= sorted_levels[0]:
        return _Z_LEVEL_TABLE[sorted_levels[0]]
    if level >= sorted_levels[-1]:
        return _Z_LEVEL_TABLE[sorted_levels[-1]]
    for lo, hi in itertools.pairwise(sorted_levels):
        if lo <= level <= hi:
            t = (level - lo) / (hi - lo)
            return _Z_LEVEL_TABLE[lo] + t * (_Z_LEVEL_TABLE[hi] - _Z_LEVEL_TABLE[lo])
    return _Z_95
