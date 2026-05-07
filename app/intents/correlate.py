from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.intent.primitives import new_step_id
from app.intents._helpers import commit_step_result

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime

_SIGNIFICANCE_LEVEL = 0.05
_DEFAULT_MIN_PAIRS = 5
_BLOCKING_ISSUE_CODES = frozenset({"granularity_mismatch", "bucket_mismatch", "insufficient_pairs"})


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


def run_correlate_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `correlate` intent: estimate pairwise statistical association.

    Input: left_ref + right_ref (both ObservationArtifactRef pointing to committed
           time_series observe artifacts, same session).
    Output: committed pairwise_time_series_association artifact (1 artifact → 1 finding).

    Empty semantics: alignment or pair count insufficient → fails, does NOT commit
    success artifact.
    """
    p = params or {}

    left_ref_raw: dict[str, Any] = p.get("left_ref") or {}
    right_ref_raw: dict[str, Any] = p.get("right_ref") or {}
    left_step_id: str = left_ref_raw.get("step_id") or ""
    right_step_id: str = right_ref_raw.get("step_id") or ""
    left_session_id: str = left_ref_raw.get("session_id") or session_id
    right_session_id: str = right_ref_raw.get("session_id") or session_id

    # ── Input validation ──────────────────────────────────────────────────────
    if not left_step_id or not right_step_id:
        raise ValueError("correlate: both left_ref.step_id and right_ref.step_id are required")

    for _side, _ref_raw in (("left", left_ref_raw), ("right", right_ref_raw)):
        _ref_step_type = _ref_raw.get("step_type")
        if _ref_step_type is not None and _ref_step_type != "observe":
            raise ValueError(
                f"correlate: INVALID_ARGUMENT - {_side}_ref.step_type must be 'observe', "
                f"got '{_ref_step_type}'"
            )

    if left_session_id != session_id:
        raise ValueError(
            "correlate: Cross-session ref not allowed - left_ref.session_id must match "
            "the current session"
        )
    if right_session_id != session_id:
        raise ValueError(
            "correlate: Cross-session ref not allowed - right_ref.session_id must match "
            "the current session"
        )

    method: str = str(p.get("method") or "spearman").lower()
    if method not in ("spearman", "pearson"):
        raise ValueError(
            f"correlate: UNSUPPORTED_METHOD - method must be 'spearman' or 'pearson' in v1, "
            f"got '{method}'"
        )

    min_pairs: int = int(p.get("min_pairs") or _DEFAULT_MIN_PAIRS)

    # ── Resolve artifacts ─────────────────────────────────────────────────────
    left_artifact = runtime.resolve_artifact_for_ref(session_id, left_step_id)
    if left_artifact is None:
        raise ValueError(
            f"correlate: STEP_NOT_FOUND - no committed artifact for left_ref step '{left_step_id}'"
        )
    right_artifact = runtime.resolve_artifact_for_ref(session_id, right_step_id)
    if right_artifact is None:
        raise ValueError(
            f"correlate: STEP_NOT_FOUND - no committed artifact for right_ref step '{right_step_id}'"
        )

    left_obs_type: str | None = left_artifact.get("observation_type")
    right_obs_type: str | None = right_artifact.get("observation_type")
    if left_obs_type != "time_series":
        raise ValueError(
            f"correlate: INVALID_ARGUMENT - left_ref must be a time_series artifact, "
            f"got observation_type='{left_obs_type}'"
        )
    if right_obs_type != "time_series":
        raise ValueError(
            f"correlate: INVALID_ARGUMENT - right_ref must be a time_series artifact, "
            f"got observation_type='{right_obs_type}'"
        )

    left_granularity: str | None = left_artifact.get("granularity")
    right_granularity: str | None = right_artifact.get("granularity")

    # ── Alignment ─────────────────────────────────────────────────────────────
    if left_granularity != right_granularity:
        raise ValueError(
            f"correlate: ALIGNMENT_FAILED - granularity mismatch: "
            f"left='{left_granularity}' right='{right_granularity}'"
        )

    left_series: list[dict[str, Any]] = left_artifact.get("series") or []
    right_series: list[dict[str, Any]] = right_artifact.get("series") or []

    left_map: dict[str, Any] = {}
    for item in left_series:
        w = item.get("window") or {}
        key = w.get("start") or ""
        if key:
            left_map[key] = item.get("value")

    right_map: dict[str, Any] = {}
    for item in right_series:
        w = item.get("window") or {}
        key = w.get("start") or ""
        if key:
            right_map[key] = item.get("value")

    matched_keys = sorted(set(left_map) & set(right_map))

    # Build numeric pairs (skip if either value is None / non-numeric)
    xs: list[float] = []
    ys: list[float] = []
    for k in matched_keys:
        lv = left_map[k]
        rv = right_map[k]
        try:
            xs.append(float(lv))
            ys.append(float(rv))
        except (TypeError, ValueError):
            continue

    n_pairs = len(xs)

    # Fix 3: dropped counts relative to n_pairs so that the invariant
    # left_point_count = matched_pair_count + dropped_left_points holds.
    dropped_left = len(left_map) - n_pairs
    dropped_right = len(right_map) - n_pairs

    if n_pairs < min_pairs:
        raise ValueError(
            f"correlate: INSUFFICIENT_DATA - only {n_pairs} aligned numeric pairs "
            f"(minimum {min_pairs}). Ensure both series share time buckets with numeric values."
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
    matched_time_scope: dict[str, Any] | None = None
    if matched_keys:
        # Find corresponding end from left series for the last bucket
        left_end_map: dict[str, str] = {}
        for item in left_series:
            w = item.get("window") or {}
            k = w.get("start") or ""
            if k:
                left_end_map[k] = w.get("end") or k
        matched_time_scope = {
            "kind": "range",
            "start": matched_keys[0],
            "end": left_end_map.get(matched_keys[-1], matched_keys[-1]),
        }

    # ── Build artifact ────────────────────────────────────────────────────────
    now = datetime.now(UTC).isoformat()
    step_id = new_step_id()

    left_artifact_id: str | None = runtime.resolve_artifact_id_for_step(session_id, left_step_id)
    right_artifact_id: str | None = runtime.resolve_artifact_id_for_step(session_id, right_step_id)

    # Fix 2: deterministic query_hash from artifact lineage + method
    _hash_input = f"{left_artifact_id or ''}:{right_artifact_id or ''}:{method}"
    query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]

    left_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_step_id,
        "artifact_id": left_artifact_id,
        "observation_type": "time_series",
    }
    right_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_step_id,
        "artifact_id": right_artifact_id,
        "observation_type": "time_series",
    }

    left_metric: str = left_artifact.get("metric") or ""
    right_metric: str = right_artifact.get("metric") or ""

    artifact: dict[str, Any] = {
        "association_type": "pairwise_time_series_association",
        "left_ref": left_ref_out,
        "right_ref": right_ref_out,
        "left_metric": left_metric,
        "right_metric": right_metric,
        "left_time_scope": left_artifact.get("time_scope"),
        "right_time_scope": right_artifact.get("time_scope"),
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
            "pairing_rule": "intersection_by_time_bucket",
            "left_granularity": left_granularity,
            "right_granularity": right_granularity,
            "matched_time_scope": matched_time_scope,
            "significance_level": _SIGNIFICANCE_LEVEL,
            "left_point_count": len(left_map),
            "right_point_count": len(right_map),
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
