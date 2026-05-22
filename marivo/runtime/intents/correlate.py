from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.metric_frame import (
    MetricFrameValueSample,
    is_metric_frame_artifact,
    iter_metric_frame_value_samples,
    metric_display_name,
    read_axes_from_artifact,
    read_metric_frame_metric_ref,
    read_metric_frame_shape,
    read_metric_frame_time_scope,
    time_grain_from_axes,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_SIGNIFICANCE_LEVEL = 0.05
_DEFAULT_MIN_PAIRS = 5
_BLOCKING_ISSUE_CODES = frozenset({"granularity_mismatch", "bucket_mismatch", "insufficient_pairs"})
_AOI_PARAM_KEYS = frozenset({"left_artifact_id", "right_artifact_id", "method", "min_pairs"})


# ── Pure correlation helpers ──────────────────────────────────────────────────


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=False))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    if var_x == 0.0 or var_y == 0.0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def _rank(values: list[float]) -> list[float]:
    """Convert values to ranks with average-rank tie handling."""
    n = len(values)
    sorted_pairs = sorted(enumerate(values), key=lambda p: p[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[sorted_pairs[k][0]] = avg_rank
        i = j + 1
    return ranks


def _spearman_correlation(x: list[float], y: list[float]) -> float:
    """Compute Spearman rank correlation coefficient without scipy."""
    n = len(x)
    if n < 2:
        return 0.0
    return _pearson_correlation(_rank(x), _rank(y))


def _pairing_rule_for_shape(shape: str) -> str:
    if shape == "scalar":
        return "metric_frame_scalar_singleton"
    if shape == "time_series":
        return "metric_frame_time_bucket_intersection"
    if shape == "segmented":
        return "metric_frame_dimension_key_intersection"
    if shape == "panel":
        return "metric_frame_panel_key_time_intersection"
    raise ValueError(f"correlate: INVALID_ARGUMENT - unsupported metric_frame shape '{shape}'")


def _sample_map(
    samples: list[MetricFrameValueSample],
) -> dict[tuple[tuple[str, str], ...], MetricFrameValueSample]:
    return {sample.sample_key: sample for sample in samples}


def _matched_time_scope(
    samples: list[MetricFrameValueSample],
    matched_keys: list[tuple[tuple[str, str], ...]],
    time_scope: dict[str, Any],
) -> dict[str, Any] | None:
    if not matched_keys:
        return None
    windows = [sample.window for sample in samples if sample.sample_key in set(matched_keys)]
    usable_windows = [
        window for window in windows if window and window.get("start") and window.get("end")
    ]
    if not usable_windows:
        return None
    sorted_windows = sorted(usable_windows, key=lambda window: str(window.get("start") or ""))
    return {
        "field": str(time_scope.get("field") or "time").strip() or "time",
        "start": sorted_windows[0]["start"],
        "end": sorted_windows[-1]["end"],
    }


def run_correlate_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute a `correlate` intent: estimate pairwise statistical association.

    Input: left_artifact_id + right_artifact_id (committed metric_frame artifacts
           with the same shape, same session).
    Output: committed pairwise_time_series_association artifact (1 artifact → 1 finding).

    Empty semantics: alignment or pair count insufficient → fails, does NOT commit
    success artifact.
    """
    p = params or {}
    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "correlate: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; correlate accepts only AOI request fields"
        )

    left_artifact_id_raw = p.get("left_artifact_id")
    right_artifact_id_raw = p.get("right_artifact_id")
    left_artifact_id = left_artifact_id_raw.strip() if isinstance(left_artifact_id_raw, str) else ""
    right_artifact_id = (
        right_artifact_id_raw.strip() if isinstance(right_artifact_id_raw, str) else ""
    )

    # ── Input validation ──────────────────────────────────────────────────────
    if not left_artifact_id or not right_artifact_id:
        raise ValueError(
            "correlate: INVALID_ARGUMENT - both left_artifact_id and right_artifact_id are required"
        )

    method: str = str(p.get("method") or "spearman").lower()
    if method not in ("spearman", "pearson"):
        raise ValueError(
            f"correlate: UNSUPPORTED_METHOD - method must be 'spearman' or 'pearson' in v1, "
            f"got '{method}'"
        )

    min_pairs_raw = p.get("min_pairs")
    if min_pairs_raw is None:
        min_pairs = _DEFAULT_MIN_PAIRS
    else:
        try:
            min_pairs = int(min_pairs_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "correlate: INVALID_ARGUMENT - min_pairs must be an integer >= 1"
            ) from exc
        if min_pairs < 1:
            raise ValueError("correlate: INVALID_ARGUMENT - min_pairs must be an integer >= 1")

    # ── Resolve artifacts ─────────────────────────────────────────────────────
    left_resolved = runtime.resolve_artifact_with_step_by_id(session_id, left_artifact_id)
    if left_resolved is None:
        raise ValueError(
            "correlate: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"left_artifact_id '{left_artifact_id}'"
        )
    left_step_id, left_artifact = left_resolved
    right_resolved = runtime.resolve_artifact_with_step_by_id(session_id, right_artifact_id)
    if right_resolved is None:
        raise ValueError(
            "correlate: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"right_artifact_id '{right_artifact_id}'"
        )
    right_step_id, right_artifact = right_resolved

    if not is_metric_frame_artifact(left_artifact):
        raise ValueError(
            "correlate: INVALID_ARGUMENT - left_artifact_id must point to a metric_frame "
            "artifact with artifact_family='metric_frame'"
        )
    if not is_metric_frame_artifact(right_artifact):
        raise ValueError(
            "correlate: INVALID_ARGUMENT - right_artifact_id must point to a metric_frame "
            "artifact with artifact_family='metric_frame'"
        )

    left_shape = read_metric_frame_shape(left_artifact)
    right_shape = read_metric_frame_shape(right_artifact)
    if left_shape != right_shape:
        raise ValueError(
            "correlate: INVALID_ARGUMENT - metric_frame shape mismatch: "
            f"left='{left_shape}' right='{right_shape}'"
        )
    pairing_rule = _pairing_rule_for_shape(left_shape)

    left_granularity = time_grain_from_axes(read_axes_from_artifact(left_artifact))
    right_granularity = time_grain_from_axes(read_axes_from_artifact(right_artifact))

    # ── Alignment ─────────────────────────────────────────────────────────────
    if left_shape in {"time_series", "panel"} and left_granularity != right_granularity:
        raise ValueError(
            f"correlate: ALIGNMENT_FAILED - granularity mismatch: "
            f"left='{left_granularity}' right='{right_granularity}'"
        )

    # ── Read metric_frame shape-native samples ────────────────────────────────
    left_samples = iter_metric_frame_value_samples(left_artifact)
    right_samples = iter_metric_frame_value_samples(right_artifact)
    left_map = _sample_map(left_samples)
    right_map = _sample_map(right_samples)

    matched_keys = sorted(set(left_map) & set(right_map))

    # Build numeric pairs (skip if either value is None / non-numeric)
    xs: list[float] = []
    ys: list[float] = []
    for k in matched_keys:
        lv = left_map[k].value
        rv = right_map[k].value
        try:
            left_numeric = float(lv)
            right_numeric = float(rv)
        except (TypeError, ValueError):
            continue
        xs.append(left_numeric)
        ys.append(right_numeric)

    n_pairs = len(xs)

    # Fix 3: dropped counts relative to n_pairs so that the invariant
    # left_point_count = matched_pair_count + dropped_left_points holds.
    dropped_left = len(left_samples) - n_pairs
    dropped_right = len(right_samples) - n_pairs

    if n_pairs < min_pairs:
        raise ValueError(
            f"correlate: INSUFFICIENT_DATA - only {n_pairs} aligned numeric pairs "
            f"(minimum {min_pairs}). Ensure both metric_frame artifacts share aligned "
            "sample keys with numeric values."
        )

    # ── Constant-series detection ─────────────────────────────────────────────
    issues: list[dict[str, Any]] = []
    constant_left = len(set(xs)) <= 1
    constant_right = len(set(ys)) <= 1
    coefficient: float | None
    p_value: float | None

    if constant_left or constant_right:
        issues.append(
            {
                "code": "constant_series",
                "severity": "warning",
                "message": ("constant series detected: correlation coefficient is not defined"),
            }
        )
        coefficient = None
        p_value = None
    else:
        if method == "spearman":
            coefficient = _spearman_correlation(xs, ys)
        else:
            coefficient = _pearson_correlation(xs, ys)
        p_value = _correlation_p_value(coefficient, n_pairs)

    # ── Alignment status ──────────────────────────────────────────────────────
    has_blocking = any(i["code"] in _BLOCKING_ISSUE_CODES for i in issues)
    has_warning = any(i["severity"] == "warning" for i in issues)
    if has_blocking:
        alignment_status = "not_aligned"
    elif has_warning:
        alignment_status = "needs_attention"
    else:
        alignment_status = "aligned"

    # ── Derive sign / significance ────────────────────────────────────────────
    if coefficient is None:
        sign = "undefined"
    elif coefficient > 0:
        sign = "positive"
    elif coefficient < 0:
        sign = "negative"
    else:
        sign = "zero"

    if p_value is None:
        significance = "undefined"
    elif p_value <= _SIGNIFICANCE_LEVEL:
        significance = "significant"
    else:
        significance = "not_significant"

    # ── matched_time_scope ────────────────────────────────────────────────────
    matched_time_scope = None
    if left_shape in {"time_series", "panel"}:
        matched_time_scope = _matched_time_scope(
            list(left_map.values()),
            matched_keys,
            read_metric_frame_time_scope(left_artifact),
        )

    # ── Build artifact ────────────────────────────────────────────────────────
    now = datetime.now(UTC).isoformat()
    step_id = new_step_id()

    # Fix 2: deterministic query_hash from artifact lineage + method
    _hash_input = f"{left_artifact_id or ''}:{right_artifact_id or ''}:{method}"
    query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]

    left_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_step_id,
        "artifact_id": left_artifact_id,
        "shape": left_shape,
    }
    right_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_step_id,
        "artifact_id": right_artifact_id,
        "shape": right_shape,
    }

    left_metric = metric_display_name(read_metric_frame_metric_ref(left_artifact))
    right_metric = metric_display_name(read_metric_frame_metric_ref(right_artifact))

    artifact: dict[str, Any] = {
        "association_type": "pairwise_time_series_association",
        "left_ref": left_ref_out,
        "right_ref": right_ref_out,
        "left_metric": left_metric,
        "right_metric": right_metric,
        "left_time_scope": read_metric_frame_time_scope(left_artifact),
        "right_time_scope": read_metric_frame_time_scope(right_artifact),
        "left_filters": None,
        "right_filters": None,
        "unit_pair": {"left": None, "right": None},
        "alignment": {"status": alignment_status, "issues": issues},
        "statistic": {
            "method": method,
            "coefficient": coefficient,
            "p_value": p_value,
            "n_pairs": n_pairs,
        },
        "sign": sign,
        "significance": significance,
        "analytical_metadata": {
            "pairing_rule": pairing_rule,
            "source_shape": left_shape,
            "left_granularity": left_granularity,
            "right_granularity": right_granularity,
            "matched_time_scope": matched_time_scope,
            "significance_level": _SIGNIFICANCE_LEVEL,
            "left_point_count": len(left_samples),
            "right_point_count": len(right_samples),
            "matched_pair_count": n_pairs,
            "dropped_left_points": dropped_left,
            "dropped_right_points": dropped_right,
        },
        "version_metadata": {
            "artifact_schema_version": "1.0",
            "source_contract_version": "1.0",
            "derivation_version": "1.0",
        },
        "source_lineage": {
            "left_artifact": left_ref_out,
            "right_artifact": right_ref_out,
        },
        "execution_metadata": {
            "query_hash": query_hash,
            "engine": "service",
            "executed_at": now,
        },
    }

    artifact_name = f"{left_metric}_vs_{right_metric}_correlate"
    coef_str = f"{coefficient:.4f}" if coefficient is not None else "null"
    summary = (
        f"correlate {left_metric} × {right_metric}: "
        f"{method} ρ={coef_str}, n={n_pairs}, {significance} [{alignment_status}]"
    )

    # TODO(v1-dedup): per correlate.md §Artifact Identity, re-executing with the same
    # left/right lineage + method + schema versions should not produce a new canonical
    # artifact. v1 always creates a fresh artifact per call.
    provenance: dict[str, Any] = {
        "left_step_id": left_step_id,
        "right_step_id": right_step_id,
        "method": method,
        "n_pairs": n_pairs,
    }
    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "correlate",
        "pairwise_time_series_association",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
        reasoning=reasoning,
    )
    return result


# ── Math helpers ──────────────────────────────────────────────────────────────


def _correlation_p_value(rho: float, n: int) -> float:
    """Two-tailed p-value using the t-distribution with (n-2) degrees of freedom.

    Uses the regularized incomplete beta function identity:
        p = I_{df/(df+t²)}(df/2, 0.5)
    which is exact (not a normal approximation) and is critical for small n.
    """
    if n <= 2:
        return 1.0
    denom = 1.0 - rho * rho
    if denom <= 0.0:
        return 0.0
    t_stat = rho * math.sqrt((n - 2) / denom)
    df = float(n - 2)
    x = df / (df + t_stat * t_stat)
    return _regularized_incomplete_beta(x, df / 2.0, 0.5)


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a,b) via Lentz continued fraction.

    Provides exact two-tailed t-distribution p-values when called as
    I_{df/(df+t²)}(df/2, 0.5).  Pure-Python, no scipy dependency.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # Symmetry relation: faster convergence when x > (a+1)/(a+b+2)
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _regularized_incomplete_beta(1.0 - x, b, a)
    log_beta_ab = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - log_beta_ab) / a
    # Lentz's continued fraction algorithm
    tiny = 1e-300
    eps = 3e-7
    max_iter = 200
    f = tiny
    cf_c = f
    cf_d = 0.0
    for m in range(max_iter):
        for step in range(2):
            if step == 0:
                d = 1.0 if m == 0 else m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
            else:
                d = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
            cf_d = 1.0 + d * cf_d
            if abs(cf_d) < tiny:
                cf_d = tiny
            cf_c = 1.0 + d / cf_c
            if abs(cf_c) < tiny:
                cf_c = tiny
            cf_d = 1.0 / cf_d
            delta = cf_c * cf_d
            f *= delta
            if abs(delta - 1.0) < eps:
                return front * f
    return front * f
