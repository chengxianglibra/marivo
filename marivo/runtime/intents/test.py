"""Test atomic intent runner.

The generated AOI Test contract consumes sample-frame artifact refs produced by
the sample_summary transform.

Requests only support ``two_sample_mean`` hypotheses.
The implementation computes Welch's t-test; there is no request method selector.

Statistical helpers use only the standard library (math module); no
external numeric dependencies are introduced.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from marivo.contracts.generated.aoi import SampleFrameArtifact
from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.predicate_lineage_reuse import (
    resolve_predicate_lineage_reuse_for_intent,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_VALID_FAMILIES: frozenset[str] = frozenset({"two_sample_mean"})
_VALID_ALTERNATIVES: frozenset[str] = frozenset({"two_sided", "greater", "less"})
_VALID_SAMPLE_GRAINS: frozenset[str] = frozenset(
    {"hour", "day", "week", "month", "quarter", "year"}
)
_SIGNIFICANCE_ALPHA: dict[str, float] = {
    "conservative": 0.01,
    "balanced": 0.05,
    "aggressive": 0.10,
}
_REQUEST_FIELDS: frozenset[str] = frozenset(
    {"current_sample_artifact_id", "baseline_sample_artifact_id", "hypothesis"}
)
_HYPOTHESIS_FIELDS: frozenset[str] = frozenset({"family", "alternative", "significance"})


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
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ValueError("test: INVALID_ARGUMENT - params must be a test request object")
    p = params
    now = datetime.now(UTC).isoformat()

    # ── Input validation ─────────────────────────────────────────────────
    missing_fields = _REQUEST_FIELDS - set(p)
    if missing_fields:
        raise ValueError(
            f"test: INVALID_ARGUMENT - missing required field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(p) - _REQUEST_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"test: INVALID_ARGUMENT - unsupported field(s): {sorted(unexpected_fields)}"
        )

    # ── Hypothesis validation ────────────────────────────────────────────
    family, alternative, significance, alpha = _validate_hypothesis(p["hypothesis"])

    current_sample_artifact_id = p["current_sample_artifact_id"]
    baseline_sample_artifact_id = p["baseline_sample_artifact_id"]
    _validate_sample_artifact_id(current_sample_artifact_id, label="current")
    _validate_sample_artifact_id(baseline_sample_artifact_id, label="baseline")
    current_sample = _resolve_sample_frame(
        runtime,
        session_id,
        current_sample_artifact_id,
        label="current",
    )
    baseline_sample = _resolve_sample_frame(
        runtime,
        session_id,
        baseline_sample_artifact_id,
        label="baseline",
    )

    current_axis = _sample_axis(current_sample)
    baseline_axis = _sample_axis(baseline_sample)
    if current_axis != baseline_axis:
        raise ValueError("test: NOT_COMPARABLE - sample axis must match")

    current_metric_ref = _metric_ref(current_sample)
    baseline_metric_ref = _metric_ref(baseline_sample)
    if current_metric_ref != baseline_metric_ref:
        raise ValueError("test: NOT_COMPARABLE - sample metric_ref must match")

    _validate_sample_quality(current_sample, label="current")
    _validate_sample_quality(baseline_sample, label="baseline")
    n1, mean1, std1 = _sample_stats(current_sample)
    n2, mean2, std2 = _sample_stats(baseline_sample)
    metric_name = current_metric_ref

    # ── Predicate lineage comparison ─────────────────────────────────────
    issues: list[dict[str, Any]] = []
    predicate_lineage_summary = resolve_predicate_lineage_reuse_for_intent(
        intent_name="test",
        current_predicate_filter_lineage=_predicate_filter_lineage(current_sample),
        baseline_predicate_filter_lineage=_predicate_filter_lineage(baseline_sample),
    )
    issues.extend(predicate_lineage_summary["issues"])
    if predicate_lineage_summary["fatal_message"] is not None:
        raise ValueError(f"test: NOT_COMPARABLE - {predicate_lineage_summary['fatal_message']}")

    # ── Welch's t-test computation ──────────────────────────────────────
    if any(v is None for v in (n1, n2, mean1, mean2, std1, std2)):
        raise ValueError(
            "test: INSUFFICIENT_DATA - required summary stats (n, mean, standard_deviation) "
            "missing from one or both sample frames"
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
    if std1 == 0.0 or std2 == 0.0:
        assumption_notes.append("one or both groups have zero variance; result may be degenerate")

    # ── Build provenance hash ────────────────────────────────────────────
    _hash_input = (
        f"{current_sample_artifact_id}:{baseline_sample_artifact_id}:"
        f"welch_t:{family}:{alternative}:{alpha}"
    )
    query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]

    # ── Source lineage ────────────────────────────────────────────────────
    source_lineage: dict[str, Any] = {
        "current_sample_artifact_id": current_sample_artifact_id,
        "baseline_sample_artifact_id": baseline_sample_artifact_id,
        "current_source_artifact_id": _source_artifact_id(current_sample),
        "baseline_source_artifact_id": _source_artifact_id(baseline_sample),
        "sample_axis": current_axis,
    }
    if predicate_lineage_summary["reuse_summary"] is not None:
        source_lineage["predicate_lineage"] = predicate_lineage_summary["reuse_summary"]

    # ── Build AOI-conforming artifact ────────────────────────────────────
    step_id = new_step_id()

    artifact: dict[str, Any] = {
        # AOI hypothesis_test_result fields (§4.2.3)
        "result_type": "hypothesis_test",
        "kind": "numeric",
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
        reasoning=reasoning,
        sql_texts=None,
    )
    return result


def _validate_sample_artifact_id(artifact_id: Any, *, label: str) -> None:
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise ValueError(f"test: INVALID_ARGUMENT - {label}_sample_artifact_id is required")


def _resolve_sample_frame(
    runtime: MarivoRuntime,
    session_id: str,
    artifact_id: Any,
    *,
    label: str,
) -> dict[str, Any]:
    _validate_sample_artifact_id(artifact_id, label=label)
    artifact = runtime.resolve_artifact_by_id(session_id, artifact_id)
    if not isinstance(artifact, dict):
        raise ValueError(f"test: INVALID_ARGUMENT - {label}_sample_artifact_id was not found")
    if artifact.get("artifact_family") != "sample_frame":
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label}_sample_artifact_id must point to a sample_frame"
        )
    if artifact.get("shape") != "numeric_summary":
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label}_sample_artifact_id must be numeric_summary"
        )
    _validate_sample_frame_generated_contract(artifact, label=label)
    _validate_sample_frame_contract(artifact, label=label)
    return artifact


def _validate_sample_frame_generated_contract(artifact: dict[str, Any], *, label: str) -> None:
    try:
        SampleFrameArtifact.model_validate(artifact)
    except PydanticValidationError as exc:
        error_summary = "; ".join(
            f"{_format_validation_loc(error.get('loc', ()))}: {error.get('msg')}"
            for error in exc.errors(include_url=False)
        )
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label}_sample_artifact_id is not a valid sample_frame: "
            f"{error_summary}"
        ) from exc


def _format_validation_loc(loc: Any) -> str:
    path = ".".join(str(part) for part in loc)
    if path.startswith("axes."):
        return path.replace("axes.0", "sample axis", 1)
    return path


def _validate_sample_frame_contract(artifact: dict[str, Any], *, label: str) -> None:
    subject = artifact.get("subject")
    if not isinstance(subject, dict):
        raise ValueError(f"test: INVALID_ARGUMENT - {label} sample_frame subject is required")
    if subject.get("kind") != "sample_summary":
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label} sample_frame subject.kind must be sample_summary"
        )

    subject_source = subject.get("source_artifact_id")
    if not isinstance(subject_source, str) or not subject_source.strip():
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label} sample_frame subject.source_artifact_id is required"
        )

    lineage = artifact.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError(f"test: INVALID_ARGUMENT - {label} sample_frame lineage is required")
    if lineage.get("operation") != "sample_summary":
        raise ValueError(
            "test: INVALID_ARGUMENT - "
            f"{label} sample_frame lineage.operation must be sample_summary"
        )
    source_ids = lineage.get("source_artifact_ids")
    if not isinstance(source_ids, list) or not source_ids:
        raise ValueError(
            "test: INVALID_ARGUMENT - "
            f"{label} sample_frame lineage.source_artifact_ids must be non-empty"
        )
    if any(not isinstance(source_id, str) or not source_id.strip() for source_id in source_ids):
        raise ValueError(
            "test: INVALID_ARGUMENT - "
            f"{label} sample_frame lineage.source_artifact_ids must contain strings"
        )
    if subject_source not in source_ids:
        raise ValueError(
            "test: INVALID_ARGUMENT - "
            f"{label} sample_frame subject.source_artifact_id must match lineage.source_artifact_ids"
        )


def _sample_stats(artifact: dict[str, Any]) -> tuple[int | None, float | None, float | None]:
    payload = artifact.get("payload")
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        raise ValueError("test: INVALID_ARGUMENT - sample_frame payload.summary is required")
    return (
        _coerce_int(summary.get("n"), field="n"),
        _coerce_float(summary.get("mean"), field="mean"),
        _coerce_float(summary.get("standard_deviation"), field="standard_deviation"),
    )


def _validate_sample_quality(artifact: dict[str, Any], *, label: str) -> None:
    payload = artifact.get("payload")
    quality = payload.get("quality") if isinstance(payload, dict) else None
    status = quality.get("status") if isinstance(quality, dict) else None
    if status != "test_ready":
        raise ValueError(
            "test: INVALID_ARGUMENT - "
            f"{label} sample_frame payload.quality.status must be test_ready"
        )


def _sample_axis(artifact: dict[str, Any]) -> dict[str, Any]:
    axes = artifact.get("axes")
    if not isinstance(axes, list):
        raise ValueError("test: INVALID_ARGUMENT - sample_frame axes are required")
    for axis in axes:
        if not isinstance(axis, dict):
            continue
        if axis.get("kind") != "sample":
            raise ValueError("test: INVALID_ARGUMENT - sample axis kind must be sample")
        source_axis = axis.get("source_axis")
        grain = axis.get("grain")
        if source_axis != "time":
            raise ValueError("test: INVALID_ARGUMENT - sample axis source_axis must be time")
        if not isinstance(grain, str) or not grain:
            raise ValueError("test: INVALID_ARGUMENT - sample axis grain is required")
        if grain not in _VALID_SAMPLE_GRAINS:
            raise ValueError(
                "test: INVALID_ARGUMENT - sample axis grain must be one of "
                f"{sorted(_VALID_SAMPLE_GRAINS)}, got '{grain}'"
            )
        return {"source_axis": source_axis, "grain": grain}
    raise ValueError("test: INVALID_ARGUMENT - sample_frame sample axis is required")


def _metric_ref(artifact: dict[str, Any]) -> str:
    subject = artifact.get("subject")
    if not isinstance(subject, dict):
        raise ValueError("test: INVALID_ARGUMENT - sample_frame subject.metric_ref is required")
    metric_ref = subject.get("metric_ref")
    if not isinstance(metric_ref, str) or not metric_ref.strip():
        raise ValueError("test: INVALID_ARGUMENT - sample_frame subject.metric_ref is required")
    return metric_ref


def _source_artifact_id(artifact: dict[str, Any]) -> str:
    subject = artifact.get("subject")
    subject_source = subject.get("source_artifact_id") if isinstance(subject, dict) else None
    if not isinstance(subject_source, str) or not subject_source.strip():
        raise ValueError(
            "test: INVALID_ARGUMENT - sample_frame subject.source_artifact_id is required"
        )
    return subject_source


def _predicate_filter_lineage(artifact: dict[str, Any]) -> dict[str, Any] | None:
    payload = artifact.get("payload")
    if isinstance(payload, dict):
        lineage = payload.get("predicate_filter_lineage")
        if lineage is None:
            summary = payload.get("summary")
            lineage = summary.get("predicate_filter_lineage") if isinstance(summary, dict) else None
        if lineage is not None:
            if not isinstance(lineage, dict):
                raise ValueError("test: INVALID_ARGUMENT - malformed predicate filter lineage")
            return lineage
    return None


def _coerce_float(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"test: INVALID_ARGUMENT - sample summary {field} must be numeric")
    if isinstance(value, int | float):
        result = float(value)
    else:
        raise ValueError(f"test: INVALID_ARGUMENT - sample summary {field} must be numeric")
    if math.isnan(result) or math.isinf(result):
        raise ValueError(f"test: INVALID_ARGUMENT - sample summary {field} must be finite")
    if field == "standard_deviation" and result < 0:
        raise ValueError(
            "test: INVALID_ARGUMENT - sample summary standard_deviation must be non-negative"
        )
    return result


def _coerce_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"test: INVALID_ARGUMENT - sample summary {field} must be an integer")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("test: INVALID_ARGUMENT - sample summary n must be non-negative")
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"test: INVALID_ARGUMENT - sample summary {field} must be an integer")
        result = int(value)
        if result < 0:
            raise ValueError("test: INVALID_ARGUMENT - sample summary n must be non-negative")
        return result
    raise ValueError(f"test: INVALID_ARGUMENT - sample summary {field} must be an integer")


def _validate_hypothesis(value: Any) -> tuple[str, str, str, float]:
    if not isinstance(value, dict):
        raise ValueError("test: INVALID_ARGUMENT - hypothesis must be an object")
    missing_hypothesis_keys = _HYPOTHESIS_FIELDS - set(value)
    if missing_hypothesis_keys:
        raise ValueError(
            "test: INVALID_ARGUMENT - missing hypothesis field(s): "
            f"{sorted(missing_hypothesis_keys)}"
        )
    unexpected_hypothesis_keys = set(value) - _HYPOTHESIS_FIELDS
    if unexpected_hypothesis_keys:
        raise ValueError(
            "test: INVALID_ARGUMENT - unsupported hypothesis field(s): "
            f"{sorted(unexpected_hypothesis_keys)}"
        )
    family_raw = value["family"]
    if not isinstance(family_raw, str):
        raise ValueError("test: INVALID_ARGUMENT - hypothesis.family must be a string")
    family = family_raw
    if family not in _VALID_FAMILIES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.family must be one of "
            f"{sorted(_VALID_FAMILIES)}, got '{family}'"
        )
    alternative_raw = value["alternative"]
    if not isinstance(alternative_raw, str):
        raise ValueError("test: INVALID_ARGUMENT - hypothesis.alternative must be a string")
    alternative = alternative_raw
    if alternative not in _VALID_ALTERNATIVES:
        raise ValueError(
            f"test: INVALID_ARGUMENT - hypothesis.alternative must be one of "
            f"{sorted(_VALID_ALTERNATIVES)}, got '{alternative}'"
        )

    significance_raw = value["significance"]
    if not isinstance(significance_raw, str):
        raise ValueError("test: INVALID_ARGUMENT - hypothesis.significance must be a string")
    significance = significance_raw
    try:
        alpha = _SIGNIFICANCE_ALPHA[significance]
    except KeyError as exc:
        raise ValueError(
            "test: INVALID_ARGUMENT - hypothesis.significance must be one of "
            f"{sorted(_SIGNIFICANCE_ALPHA)}, got '{significance}'"
        ) from exc
    return family, alternative, significance, alpha
