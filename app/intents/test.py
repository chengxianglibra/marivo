"""Test atomic intent runner (Phase 3b-5).

Evaluates a typed statistical hypothesis over two inferential-ready
observe artifacts: Welch's t-test (numeric_sample_summary) or two-proportion
z-test (rate_sample_summary).

Statistical helpers use only the standard library (math module); no external
numeric dependencies are introduced.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.intent.primitives import new_step_id
from app.intents._helpers import commit_step_result
from app.intents.calendar_alignment_metadata import resolve_calendar_alignment_reuse_for_intent
from app.intents.predicate_lineage_reuse import resolve_predicate_lineage_reuse_for_intent

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime

_VALID_OBS_TYPES: frozenset[str] = frozenset({"numeric_sample_summary", "rate_sample_summary"})
_VALID_ALTERNATIVES: frozenset[str] = frozenset({"two_sided", "greater", "less"})


# ── Statistical helpers (pure Python, no external deps) ──────────────────────


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction expansion for the regularized incomplete beta function.

    Uses Lentz's algorithm for numerical stability.
    """
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
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # Symmetry relation for numerical stability when x is large
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betai(b, a, 1.0 - x)
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta) / a
    return front * _betacf(a, b, x)


def _t_sf(t: float, df: float) -> float:
    """One-tailed survival function P(T > |t|) for Student's t with df degrees of freedom."""
    if df <= 0:
        return float("nan")
    t_abs = abs(t)
    if t_abs == 0.0:
        return 0.5
    x = df / (t_abs * t_abs + df)
    return 0.5 * _betai(df / 2.0, 0.5, x)


def _norm_sf(z: float) -> float:
    """P(Z > z) for the standard normal distribution."""
    return math.erfc(z / math.sqrt(2)) / 2.0


def _p_value_from_t(t: float, df: float, alternative: str) -> float:
    """Compute p-value for Welch's t-test given test statistic and alternative."""
    one_tail = _t_sf(t, df)  # P(T > |t|)
    if alternative == "two_sided":
        return min(1.0, 2.0 * one_tail)
    if alternative == "greater":
        # Reject H0 when t > 0 (mean1 > mean2)
        return one_tail if t >= 0 else 1.0 - one_tail
    # less: reject H0 when t < 0 (mean1 < mean2)
    return one_tail if t <= 0 else 1.0 - one_tail


def _p_value_from_z(z: float, alternative: str) -> float:
    """Compute p-value for two-proportion z-test given test statistic and alternative."""
    if alternative == "two_sided":
        return min(1.0, 2.0 * _norm_sf(abs(z)))
    if alternative == "greater":
        return _norm_sf(z)
    # less
    return 1.0 - _norm_sf(z)


# ── Runner ────────────────────────────────────────────────────────────────────


def run_test_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `test` intent: evaluate a typed statistical hypothesis.

    Consumes two inferential-ready observe artifacts (numeric_sample_summary
    or rate_sample_summary from the same session) and performs Welch's t-test
    or two-proportion z-test.

    Empty semantics: test must produce a valid result (non-empty success).
    If validation is invalid, raises ValueError and does not commit an artifact.
    """
    p = params or {}
    now = datetime.now(UTC).isoformat()

    left_ref_raw: dict[str, Any] = p.get("left_ref") or {}
    right_ref_raw: dict[str, Any] = p.get("right_ref") or {}

    left_step_id: str = left_ref_raw.get("step_id") or ""
    right_step_id: str = right_ref_raw.get("step_id") or ""
    left_session_id: str = left_ref_raw.get("session_id") or session_id
    right_session_id: str = right_ref_raw.get("session_id") or session_id

    # ── Input validation ───────────────────────────────────────────────────────
    if not left_step_id or not right_step_id:
        raise ValueError("test: both left_ref.step_id and right_ref.step_id are required")

    for _side, _ref in (("left", left_ref_raw), ("right", right_ref_raw)):
        _st = _ref.get("step_type")
        if _st != "observe":
            raise ValueError(
                f"test: INVALID_ARGUMENT - {_side}_ref.step_type must be 'observe', got {_st!r}"
            )

    if left_session_id != session_id:
        raise ValueError(
            "test: CROSS_SESSION_NOT_ALLOWED - left_ref.session_id must match the current session"
        )
    if right_session_id != session_id:
        raise ValueError(
            "test: CROSS_SESSION_NOT_ALLOWED - right_ref.session_id must match the current session"
        )

    # ── Hypothesis extraction & normalisation ─────────────────────────────────
    hypothesis_raw: dict[str, Any] = p.get("hypothesis") or {}
    family: str = str(hypothesis_raw.get("family") or "difference").lower()
    if family != "difference":
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.family must be 'difference' in v1, got '{family}'"
        )

    alternative: str = str(hypothesis_raw.get("alternative") or "two_sided").lower()
    if alternative not in _VALID_ALTERNATIVES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.alternative must be one of "
            f"{sorted(_VALID_ALTERNATIVES)}, got '{alternative}'"
        )

    alpha_raw = hypothesis_raw.get("alpha")
    alpha: float = 0.05
    if alpha_raw is not None:
        try:
            alpha = float(alpha_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("test: INVALID_ARGUMENT - hypothesis.alpha must be a number") from exc
    if not (0.0 < alpha < 1.0):
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.alpha must be in (0, 1), got {alpha}"
        )

    hyp_label: str | None = hypothesis_raw.get("label") or None

    method_raw: str = str(p.get("method") or "auto").lower()
    if method_raw not in {"auto", "welch_t", "two_proportion_z"}:
        raise ValueError(
            f"test: INVALID_ARGUMENT - method must be 'auto', 'welch_t', or 'two_proportion_z', "
            f"got '{method_raw}'"
        )

    # ── Resolve upstream artifacts ─────────────────────────────────────────────
    left_result = runtime.resolve_artifact_with_id(session_id, left_step_id)
    if left_result is None:
        raise ValueError(
            f"test: STEP_NOT_FOUND - no committed artifact for left_ref step '{left_step_id}'"
        )
    left_artifact_id, left_artifact = left_result

    right_result = runtime.resolve_artifact_with_id(session_id, right_step_id)
    if right_result is None:
        raise ValueError(
            f"test: STEP_NOT_FOUND - no committed artifact for right_ref step '{right_step_id}'"
        )
    right_artifact_id, right_artifact = right_result

    left_obs_type: str | None = left_artifact.get("observation_type")
    right_obs_type: str | None = right_artifact.get("observation_type")
    left_ref_obs_type: str | None = left_ref_raw.get("observation_type")
    right_ref_obs_type: str | None = right_ref_raw.get("observation_type")

    if left_ref_raw.get("artifact_id") != left_artifact_id:
        raise ValueError(
            "test: INVALID_ARGUMENT - left_ref.artifact_id must match the committed observe artifact"
        )
    if right_ref_raw.get("artifact_id") != right_artifact_id:
        raise ValueError(
            "test: INVALID_ARGUMENT - right_ref.artifact_id must match the committed observe artifact"
        )

    if left_obs_type not in _VALID_OBS_TYPES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - left_ref must point to a numeric_sample_summary or "
            f"rate_sample_summary artifact, got observation_type='{left_obs_type}'"
        )
    if right_obs_type not in _VALID_OBS_TYPES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - right_ref must point to a numeric_sample_summary or "
            f"rate_sample_summary artifact, got observation_type='{right_obs_type}'"
        )
    if left_obs_type != right_obs_type:
        raise ValueError(
            f"test: NOT_COMPARABLE - observation_type mismatch: "
            f"left='{left_obs_type}' vs right='{right_obs_type}'"
        )
    if left_ref_obs_type != left_obs_type:
        raise ValueError(
            "test: INVALID_ARGUMENT - left_ref.observation_type must match the committed "
            f"observe artifact (expected '{left_obs_type}', got '{left_ref_obs_type}')"
        )
    if right_ref_obs_type != right_obs_type:
        raise ValueError(
            "test: INVALID_ARGUMENT - right_ref.observation_type must match the committed "
            f"observe artifact (expected '{right_obs_type}', got '{right_ref_obs_type}')"
        )

    # ── Method resolution (deterministic) ─────────────────────────────────────
    if method_raw == "auto":
        resolved_method = (
            "welch_t" if left_obs_type == "numeric_sample_summary" else "two_proportion_z"
        )
    elif method_raw == "welch_t":
        if left_obs_type != "numeric_sample_summary":
            raise ValueError(
                "test: NOT_COMPARABLE - method='welch_t' requires numeric_sample_summary artifacts"
            )
        resolved_method = "welch_t"
    else:
        if left_obs_type != "rate_sample_summary":
            raise ValueError(
                "test: NOT_COMPARABLE - "
                "method='two_proportion_z' requires rate_sample_summary artifacts"
            )
        resolved_method = "two_proportion_z"

    # ── Metric compatibility check ─────────────────────────────────────────────
    left_metric: str = left_artifact.get("metric") or ""
    right_metric: str = right_artifact.get("metric") or ""

    # ── Statistical computation ────────────────────────────────────────────────
    issues: list[dict[str, Any]] = []

    if left_metric and right_metric and left_metric != right_metric:
        issues.append(
            {
                "code": "metric_mismatch",
                "severity": "warning",
                "message": (
                    f"left_ref metric '{left_metric}' and right_ref metric '{right_metric}' differ. "
                    "Ensure both observations measure the same concept before interpreting results."
                ),
            }
        )

    calendar_alignment_summary = resolve_calendar_alignment_reuse_for_intent(
        intent_name="test",
        left_resolved_policy_summary=left_artifact.get("resolved_policy_summary"),
        right_resolved_policy_summary=right_artifact.get("resolved_policy_summary"),
    )
    issues.extend(calendar_alignment_summary["issues"])
    if calendar_alignment_summary["fatal_message"] is not None:
        raise ValueError(f"test: NOT_COMPARABLE - {calendar_alignment_summary['fatal_message']}")

    predicate_lineage_summary = resolve_predicate_lineage_reuse_for_intent(
        intent_name="test",
        left_predicate_filter_lineage=left_artifact.get("predicate_filter_lineage"),
        right_predicate_filter_lineage=right_artifact.get("predicate_filter_lineage"),
    )
    issues.extend(predicate_lineage_summary["issues"])
    if predicate_lineage_summary["fatal_message"] is not None:
        raise ValueError(f"test: NOT_COMPARABLE - {predicate_lineage_summary['fatal_message']}")

    # distribution_check: "unchecked" when applicable but not performed (welch_t);
    # None when the current method makes it not applicable (two_proportion_z).
    distribution_check: str | None = "unchecked" if resolved_method == "welch_t" else None

    sample_kind: str
    stat_name: str
    estimate_estimand: str
    variance_model: str | None = None
    estimate_value: float | None = None
    stat_value: float | None = None
    p_value: float | None = None
    reject_null: bool | None = None

    if resolved_method == "welch_t":
        sample_kind = "numeric"
        stat_name = "t"
        estimate_estimand = "mean_diff"
        variance_model = "welch"

        left_ss = left_artifact.get("sample_summary") or {}
        right_ss = right_artifact.get("sample_summary") or {}

        n1_raw = left_ss.get("n")
        n2_raw = right_ss.get("n")
        mean1_raw = left_ss.get("mean")
        mean2_raw = right_ss.get("mean")
        std1_raw = left_ss.get("standard_deviation")
        std2_raw = right_ss.get("standard_deviation")

        if any(v is None for v in (n1_raw, n2_raw, mean1_raw, mean2_raw, std1_raw, std2_raw)):
            raise ValueError(
                "test: INSUFFICIENT_DATA - required summary stats (n, mean, standard_deviation) "
                "missing from one or both input artifacts"
            )

        n1 = int(n1_raw)  # type: ignore[arg-type]
        n2 = int(n2_raw)  # type: ignore[arg-type]
        mean1 = float(mean1_raw)  # type: ignore[arg-type]
        mean2 = float(mean2_raw)  # type: ignore[arg-type]
        std1 = float(std1_raw)  # type: ignore[arg-type]
        std2 = float(std2_raw)  # type: ignore[arg-type]

        if n1 < 2 or n2 < 2:
            raise ValueError(
                f"test: INSUFFICIENT_DATA - both groups require n >= 2 for Welch's t-test "
                f"(got n1={n1}, n2={n2})"
            )

        if std1 == 0.0 and std2 == 0.0:
            issues.append(
                {
                    "code": "assumption_warning",
                    "severity": "warning",
                    "message": "Both groups have zero variance; t-test result may be degenerate.",
                }
            )

        var1 = std1**2
        var2 = std2**2
        se1_sq = var1 / n1
        se2_sq = var2 / n2
        se = math.sqrt(se1_sq + se2_sq)

        if se == 0.0:
            raise ValueError(
                "test: INSUFFICIENT_DATA - combined standard error is zero; "
                "cannot compute test statistic"
            )

        t_stat = (mean1 - mean2) / se
        denom = se1_sq**2 / max(n1 - 1, 1) + se2_sq**2 / max(n2 - 1, 1)
        df = (se1_sq + se2_sq) ** 2 / denom if denom > 0 else 1.0

        p_value = _p_value_from_t(t_stat, df, alternative)
        reject_null = p_value <= alpha
        stat_value = t_stat
        estimate_value = mean1 - mean2

    else:  # two_proportion_z
        sample_kind = "rate"
        stat_name = "z"
        estimate_estimand = "rate_diff"

        left_ss = left_artifact.get("sample_summary") or {}
        right_ss = right_artifact.get("sample_summary") or {}

        k1_raw = left_ss.get("successes")
        k2_raw = right_ss.get("successes")
        n1_raw = left_ss.get("trials")
        n2_raw = right_ss.get("trials")

        if any(v is None for v in (k1_raw, k2_raw, n1_raw, n2_raw)):
            raise ValueError(
                "test: INSUFFICIENT_DATA - required summary stats (successes, trials) "
                "missing from one or both input artifacts"
            )

        k1 = round(float(k1_raw))  # type: ignore[arg-type]
        k2 = round(float(k2_raw))  # type: ignore[arg-type]
        n1_rate = int(n1_raw)  # type: ignore[arg-type]
        n2_rate = int(n2_raw)  # type: ignore[arg-type]

        if n1_rate < 1 or n2_rate < 1:
            raise ValueError(
                f"test: INSUFFICIENT_DATA - both groups require n >= 1 "
                f"(got n1={n1_rate}, n2={n2_rate})"
            )

        p1 = k1 / n1_rate
        p2 = k2 / n2_rate
        pooled = (k1 + k2) / (n1_rate + n2_rate)
        se_sq = pooled * (1.0 - pooled) * (1.0 / n1_rate + 1.0 / n2_rate)

        if se_sq <= 0.0:
            raise ValueError(
                "test: INSUFFICIENT_DATA - pooled standard error is zero; "
                "cannot compute test statistic (check that pooled rate is not 0 or 1)"
            )

        z_stat = (p1 - p2) / math.sqrt(se_sq)
        p_value = _p_value_from_z(z_stat, alternative)
        reject_null = p_value <= alpha
        stat_value = z_stat
        estimate_value = p1 - p2

    # ── Determine validation status ────────────────────────────────────────────
    has_error = any(i["severity"] == "error" for i in issues)
    has_warning = any(i["severity"] == "warning" for i in issues)
    if has_error:
        validation_status = "invalid"
    elif has_warning:
        validation_status = "needs_attention"
    else:
        validation_status = "valid"

    # Non-empty semantics: must produce a valid result
    if validation_status == "invalid":
        error_msgs = [i["message"] for i in issues if i["severity"] == "error"]
        raise ValueError(f"test: validation failed: {'; '.join(error_msgs)}")

    # ── Build artifact ─────────────────────────────────────────────────────────
    step_id = new_step_id()

    # Deterministic query_hash from input identities (no actual SQL)
    _hash_input = (
        f"{left_artifact_id}:{right_artifact_id}:{resolved_method}:{family}:{alternative}:{alpha}"
    )
    query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]

    left_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_step_id,
        "artifact_id": left_artifact_id,
        "observation_type": left_obs_type,
    }
    right_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_step_id,
        "artifact_id": right_artifact_id,
        "observation_type": right_obs_type,
    }

    source_lineage: dict[str, Any] = {
        "left_source": {
            "step_ref": dict(left_ref_out),
            "source_schema_version": str(left_artifact.get("schema_version") or "1.0"),
        },
        "right_source": {
            "step_ref": dict(right_ref_out),
            "source_schema_version": str(right_artifact.get("schema_version") or "1.0"),
        },
    }
    if calendar_alignment_summary["reuse_summary"] is not None:
        source_lineage["calendar_alignment"] = calendar_alignment_summary["reuse_summary"]
    if predicate_lineage_summary["reuse_summary"] is not None:
        source_lineage["predicate_lineage"] = predicate_lineage_summary["reuse_summary"]

    artifact: dict[str, Any] = {
        "artifact_schema_version": "v1",
        "result_type": "hypothesis_test",
        "derivation_version": "1.0",
        "schema_version": "1.0",
        "left_ref": left_ref_out,
        "right_ref": right_ref_out,
        "source_lineage": source_lineage,
        "sample_kind": sample_kind,
        "hypothesis": {
            "family": family,
            "alternative": alternative,
            "alpha": alpha,
            "label": hyp_label,
        },
        "method": resolved_method,
        "estimate": {
            "estimand": estimate_estimand,
            "value": estimate_value,
            "confidence_interval": None,  # v1: not computed
        },
        "statistic": {
            "name": stat_name,
            "value": stat_value,
        },
        "p_value": p_value,
        "decision": {
            "reject_null": reject_null,
        },
        "assumptions": {
            "independence": "assumed",
            "distribution_check": distribution_check,
            "variance_model": variance_model,
        },
        "validation": {
            "status": validation_status,
            "issues": issues,
        },
        "execution_metadata": {
            "query_hash": query_hash,
            "engine": "none",
            "executed_at": now,
        },
    }

    metrics_label = left_metric if left_metric == right_metric else f"{left_metric}|{right_metric}"
    artifact_name = f"{metrics_label}_hypothesis_test"
    if reject_null is not None:
        decision_label = "reject" if reject_null else "fail-to-reject"
        summary = (
            f"test {metrics_label} [{resolved_method}] alternative={alternative} α={alpha}: "
            f"{decision_label} null (p={p_value:.4g})"
        )
    else:
        summary = f"test {metrics_label} [{resolved_method}]: validation={validation_status}"

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
        "test",
        "hypothesis_test",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
    )
    return result
