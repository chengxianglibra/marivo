"""Test atomic intent runner — Welch's t-test only (source-type per AOI v0.1).

Evaluates a typed statistical hypothesis over two data slices.
Accepts metric + left/right slices (not artifact refs) per the AOI spec,
and computes sample summaries internally without creating intermediate
observe artifacts.

Requests only support ``numeric`` kind with ``two_sample_mean`` hypotheses.
The implementation computes Welch's t-test; there is no request method selector.

Statistical helpers use only the standard library (math module); no
external numeric dependencies are introduced.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import (
    commit_step_result,
    compute_numeric_sample_summary,
    resolve_time_scope,
)
from marivo.runtime.intents.normalization import normalize_metric_ref
from marivo.runtime.intents.predicate_lineage_reuse import (
    resolve_predicate_lineage_reuse_for_intent,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_VALID_KINDS: frozenset[str] = frozenset({"numeric"})
_VALID_FAMILIES: frozenset[str] = frozenset({"two_sample_mean"})
_VALID_ALTERNATIVES: frozenset[str] = frozenset({"two_sided", "greater", "less"})
_SIGNIFICANCE_ALPHA: dict[str, float] = {
    "conservative": 0.01,
    "balanced": 0.05,
    "aggressive": 0.10,
}


# ── Statistical helpers (pure Python, no external deps) ──────────────────────


def _betacf(a: float, b: float, x: float) -> float:
    _maxit = 200
    _eps = 3e-7
    _fpmin = 1e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _fpmin:
        d = _fpmin
    d = 1.0 / d
    h = d
    for m in range(1, _maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _fpmin:
            d = _fpmin
        c = 1.0 + aa / c
        if abs(c) < _fpmin:
            c = _fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _fpmin:
            d = _fpmin
        c = 1.0 + aa / c
        if abs(c) < _fpmin:
            c = _fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) <= _eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betai(b, a, 1.0 - x)
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta) / a
    return front * _betacf(a, b, x)


def _t_sf(t: float, df: float) -> float:
    """Survival function P(T > t) for Student's t-distribution."""
    if df <= 0:
        return float("nan")
    if t == 0.0:
        return 0.5
    t_abs = abs(t)
    x = df / (t_abs * t_abs + df)
    upper = 0.5 * _betai(df / 2.0, 0.5, x)
    return upper if t > 0 else 1.0 - upper


def _p_value_from_t(t: float, df: float, alternative: str) -> float:
    sf_t = _t_sf(t, df)
    if alternative == "two_sided":
        return min(1.0, 2.0 * min(sf_t, 1.0 - sf_t))
    if alternative == "greater":
        return sf_t
    return 1.0 - sf_t


# ── Runner ────────────────────────────────────────────────────────────────────


def run_test_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Source-type test intent per AOI v0.1.

    Computes sample summaries internally and returns a hypothesis_test
    artifact conforming to the AOI hypothesis_test_result contract.
    """
    p = params or {}
    now = datetime.now(UTC).isoformat()

    # ── Input validation ─────────────────────────────────────────────────
    if "method" in p:
        raise ValueError("test: INVALID_ARGUMENT - method is not a supported test parameter")

    metric_ref: str = normalize_metric_ref(p.get("metric") or "")
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    if not metric_ref:
        raise ValueError("test: INVALID_ARGUMENT - metric is required")

    kind: str = str(p.get("kind") or "numeric").lower()
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"test: INVALID_ARGUMENT - kind must be one of {sorted(_VALID_KINDS)}, got '{kind}'"
        )

    left_raw: dict[str, Any] = p.get("left") or {}
    right_raw: dict[str, Any] = p.get("right") or {}

    left_time_scope: dict[str, Any] = left_raw.get("time_scope") or {}
    right_time_scope: dict[str, Any] = right_raw.get("time_scope") or {}

    if not left_time_scope:
        raise ValueError("test: INVALID_ARGUMENT - left.time_scope is required")
    if not right_time_scope:
        raise ValueError("test: INVALID_ARGUMENT - right.time_scope is required")

    left_scope: Any = left_raw.get("scope") or left_raw.get("filter")
    right_scope: Any = right_raw.get("scope") or right_raw.get("filter")

    # ── Hypothesis validation ────────────────────────────────────────────
    hypothesis_raw: dict[str, Any] = p.get("hypothesis") or {}
    unexpected_hypothesis_keys = set(hypothesis_raw) - {"family", "alternative", "significance"}
    if unexpected_hypothesis_keys:
        raise ValueError(
            "test: INVALID_ARGUMENT - unsupported hypothesis field(s): "
            f"{sorted(unexpected_hypothesis_keys)}"
        )
    family: str = str(hypothesis_raw.get("family") or "two_sample_mean").lower()
    if family not in _VALID_FAMILIES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.family must be one of "
            f"{sorted(_VALID_FAMILIES)}, got '{family}'"
        )
    alternative: str = str(hypothesis_raw.get("alternative") or "two_sided").lower()
    if alternative not in _VALID_ALTERNATIVES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.alternative must be one of "
            f"{sorted(_VALID_ALTERNATIVES)}, got '{alternative}'"
        )

    significance = str(hypothesis_raw.get("significance") or "balanced").lower()
    try:
        alpha = _SIGNIFICANCE_ALPHA[significance]
    except KeyError as exc:
        raise ValueError(
            "test: INVALID_ARGUMENT - hypothesis.significance must be one of "
            f"{sorted(_SIGNIFICANCE_ALPHA)}, got '{significance}'"
        ) from exc

    # ── Compute sample summaries (internal, no intermediate artifacts) ──
    left_ss = compute_numeric_sample_summary(
        runtime, session_id, metric_ref, left_time_scope, scope_raw=left_scope
    )
    right_ss = compute_numeric_sample_summary(
        runtime, session_id, metric_ref, right_time_scope, scope_raw=right_scope
    )

    # ── Predicate lineage comparison ─────────────────────────────────────
    issues: list[dict[str, Any]] = []
    predicate_lineage_summary = resolve_predicate_lineage_reuse_for_intent(
        intent_name="test",
        left_predicate_filter_lineage=left_ss.predicate_filter_lineage,
        right_predicate_filter_lineage=right_ss.predicate_filter_lineage,
    )
    issues.extend(predicate_lineage_summary["issues"])
    if predicate_lineage_summary["fatal_message"] is not None:
        raise ValueError(f"test: NOT_COMPARABLE - {predicate_lineage_summary['fatal_message']}")

    # ── Welch's t-test computation ──────────────────────────────────────
    n1 = left_ss.n
    n2 = right_ss.n
    mean1 = left_ss.mean
    mean2 = right_ss.mean
    std1 = left_ss.standard_deviation
    std2 = right_ss.standard_deviation

    if any(v is None for v in (n1, n2, mean1, mean2, std1, std2)):
        raise ValueError(
            "test: INSUFFICIENT_DATA - required summary stats (n, mean, standard_deviation) "
            "missing from one or both slices"
        )

    # Type narrowing: after the None-check above, all values are guaranteed non-None.
    n1_: int = n1  # type: ignore[assignment]
    n2_: int = n2  # type: ignore[assignment]
    mean1_: float = mean1  # type: ignore[assignment]
    mean2_: float = mean2  # type: ignore[assignment]
    std1_: float = std1  # type: ignore[assignment]
    std2_: float = std2  # type: ignore[assignment]

    if n1_ < 2 or n2_ < 2:
        raise ValueError(
            f"test: INSUFFICIENT_DATA - both groups require n >= 2 for Welch's t-test "
            f"(got n1={n1_}, n2={n2_})"
        )

    if std1_ == 0.0 and std2_ == 0.0:
        issues.append(
            {
                "code": "assumption_warning",
                "severity": "warning",
                "message": "Both groups have zero variance; t-test result may be degenerate.",
            }
        )

    var1 = std1_**2
    var2 = std2_**2
    se1_sq = var1 / n1_
    se2_sq = var2 / n2_
    se = math.sqrt(se1_sq + se2_sq)

    if se == 0.0:
        raise ValueError(
            "test: INSUFFICIENT_DATA - combined standard error is zero; "
            "cannot compute test statistic"
        )

    t_stat = (mean1_ - mean2_) / se
    denom = se1_sq**2 / max(n1_ - 1, 1) + se2_sq**2 / max(n2_ - 1, 1)
    df = (se1_sq + se2_sq) ** 2 / denom if denom > 0 else 1.0

    p_value_raw: float = _p_value_from_t(t_stat, df, alternative)
    # Guard against NaN / out-of-range p-values (can arise from
    # edge-case inputs that produce pathological df values).
    p_value: float | None
    if math.isnan(p_value_raw) or math.isinf(p_value_raw):
        p_value = None
        reject_null = None
    else:
        p_value = max(0.0, min(1.0, p_value_raw))
        reject_null = p_value <= alpha
    estimate_value = mean1_ - mean2_

    # ── Build assumption_notes (AOI spec §4.2.3, §8.9) ──────────────────
    assumption_notes: list[str] = [
        "normality not assessed",
        "equal variance not assumed (Welch's t-test)",
    ]
    if std1 == 0.0 and std2 == 0.0:
        assumption_notes.append("both groups have zero variance; result may be degenerate")

    # ── Build provenance hash ────────────────────────────────────────────
    left_start, left_end, _ = resolve_time_scope(left_time_scope)
    right_start, right_end, _ = resolve_time_scope(right_time_scope)
    _hash_input = (
        f"{metric_ref}:welch_t:{family}:{alternative}:{alpha}"
        f":left[{left_start},{left_end}]:right[{right_start},{right_end}]"
    )
    query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]

    # ── Source lineage ────────────────────────────────────────────────────
    source_lineage: dict[str, Any] = {
        "left": {
            "time_scope": {"kind": "range", "start": left_start, "end": left_end},
            "filter": left_scope,
        },
        "right": {
            "time_scope": {"kind": "range", "start": right_start, "end": right_end},
            "filter": right_scope,
        },
    }
    if predicate_lineage_summary["reuse_summary"] is not None:
        source_lineage["predicate_lineage"] = predicate_lineage_summary["reuse_summary"]

    # ── Build AOI-conforming artifact ────────────────────────────────────
    step_id = new_step_id()

    artifact: dict[str, Any] = {
        # AOI hypothesis_test_result fields (§4.2.3)
        "result_type": "hypothesis_test",
        "kind": kind,
        "hypothesis": {
            "family": family,
            "alternative": alternative,
            "significance": significance,
            "alpha": alpha,
        },
        "statistic": t_stat,
        "p_value": p_value,
        "decision": {"reject_null": reject_null},
        "assumption_notes": assumption_notes,
        # Implementation extensions (experimental tier, §6.2)
        "method": "welch_t",
        "estimate": {"estimand": "mean_diff", "value": estimate_value},
        "source_lineage": source_lineage,
        "execution_metadata": {
            "query_hash": query_hash,
            "engine": "service",
            "executed_at": now,
        },
    }

    metrics_label = metric_name
    decision_label = "reject" if reject_null else "fail-to-reject"
    summary = (
        f"test {metrics_label} [welch_t] alternative={alternative} alpha={alpha}: "
        f"{decision_label} null (p={p_value:.4g})"
    )

    provenance: dict[str, Any] = {
        "query_hash": query_hash,
        "engine": "service",
        "timestamp": now,
        "param_count": 0,
    }
    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "test",
        "hypothesis_test",
        f"{metrics_label}_hypothesis_test",
        artifact,
        summary,
        provenance=provenance,
    )
    return result
