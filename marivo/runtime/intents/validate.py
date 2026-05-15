"""Validate derived intent runner — numeric (Welch's t) only.

Orchestrates the source-type test intent with metric + slices,
then builds a validation_bundle artifact wrapping the test result.

Per AOI v0.1 §8.6, validate is a private product composition
(not part of the AOI atomic surface).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.test import run_test_intent

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_DERIVED_LOGIC_VERSION = "1.0"
_PROJECTION_VERSION = "1.0"


def run_validate_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    p = params or {}

    # ── Input extraction ──────────────────────────────────────────────────
    metric_ref: str = p.get("metric") or ""
    if not metric_ref:
        raise ValueError("validate: INVALID_ARGUMENT - metric is required")

    left_raw: dict[str, Any] = p.get("left") or {}
    right_raw: dict[str, Any] = p.get("right") or {}

    left_time_scope: dict[str, Any] = left_raw.get("time_scope") or {}
    right_time_scope: dict[str, Any] = right_raw.get("time_scope") or {}

    if not left_time_scope:
        raise ValueError("validate: INVALID_ARGUMENT - left.time_scope is required")
    if not right_time_scope:
        raise ValueError("validate: INVALID_ARGUMENT - right.time_scope is required")

    left_scope: Any = left_raw.get("scope") or left_raw.get("filter")
    right_scope: Any = right_raw.get("scope") or right_raw.get("filter")

    hypothesis_raw: dict[str, Any] = p.get("hypothesis") or {}
    alternative: str = str(hypothesis_raw.get("alternative") or "two_sided").lower()
    alpha_raw = hypothesis_raw.get("alpha")
    alpha: float = 0.05
    if alpha_raw is not None:
        try:
            alpha = float(alpha_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "validate: INVALID_ARGUMENT - hypothesis.alpha must be a number"
            ) from exc
    hyp_label: str | None = hypothesis_raw.get("label") or None

    method_raw: str = str(p.get("method") or "auto").lower()

    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    # ── Build test params (source-type) ──────────────────────────────────
    test_params: dict[str, Any] = {
        "metric": metric_ref,
        "left": {"time_scope": left_time_scope, "filter": left_scope},
        "right": {"time_scope": right_time_scope, "filter": right_scope},
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": alternative,
            "alpha": alpha,
            "label": hyp_label,
        },
        "method": method_raw,
    }

    # ── Run test ─────────────────────────────────────────────────────────
    try:
        test_result = run_test_intent(runtime, session_id, test_params)
    except Exception as exc:
        raise ValueError(f"validate: TEST_FAILED - hypothesis test failed: {exc}") from exc

    test_step_id: str = test_result["step_ref"]["step_id"]
    test_artifact_id: str = test_result["artifact_id"]
    resolved_method: str = test_result.get("method") or "welch_t"

    # ── Derive validation status from test result ────────────────────────
    assumption_notes: list[str] = test_result.get("assumption_notes") or []
    has_degenerate = any("degenerate" in note.lower() for note in assumption_notes)

    validation_status: str = "needs_attention" if has_degenerate else "validated"

    bundle_issues: list[dict[str, Any]] = []
    if validation_status == "needs_attention":
        for note in assumption_notes:
            bundle_issues.append(
                {
                    "code": "test_assumption_warning",
                    "severity": "warning",
                    "message": note,
                    "source": "test",
                }
            )

    # ── Derive inference result ──────────────────────────────────────────
    test_decision: dict[str, Any] = test_result.get("decision") or {}
    reject_null: bool | None = test_decision.get("reject_null")

    if reject_null is True:
        decision_str: str = "reject_null"
    elif reject_null is False:
        decision_str = "fail_to_reject"
    else:
        decision_str = "undetermined"

    inference_result: dict[str, Any] = {
        "decision": decision_str,
        "p_value": test_result.get("p_value"),
        "estimate": test_result.get("estimate"),
    }

    # ── Build test ref ───────────────────────────────────────────────────
    test_ref_out: dict[str, Any] = {
        "step_type": "test",
        "session_id": session_id,
        "step_id": test_step_id,
        "artifact_id": test_artifact_id,
        "result_type": "hypothesis_test",
    }

    # ── Assemble validation_bundle ───────────────────────────────────────
    now = datetime.now(UTC).isoformat()
    step_id = new_step_id()

    hypothesis_out: dict[str, Any] = {
        "family": "two_sample_mean",
        "alternative": alternative,
        "alpha": alpha,
        "label": hyp_label,
    }

    bundle: dict[str, Any] = {
        "result_type": "validation_bundle",
        "intent_type": "validate",
        "step_type": "validate",
        "artifact_schema_version": "v1",
        "derivation_version": _DERIVED_LOGIC_VERSION,
        "metric": metric_ref,
        "left": {"time_scope": left_time_scope, "scope": left_scope},
        "right": {"time_scope": right_time_scope, "scope": right_scope},
        "kind": "numeric",
        "hypothesis": hypothesis_out,
        "method": resolved_method,
        "validation": {
            "status": validation_status,
            "issues": bundle_issues,
        },
        "refs": {
            "test_ref": test_ref_out,
        },
        "result": inference_result,
        "provenance": {
            "session_id": session_id,
            "source_test_ref": test_ref_out,
            "intent_contract_version": "validate.v1",
            "derived_logic_version": _DERIVED_LOGIC_VERSION,
        },
        "version": {
            "intent_contract_version": "validate.v1",
            "projection_version": _PROJECTION_VERSION,
            "derived_logic_version": _DERIVED_LOGIC_VERSION,
        },
        "execution_metadata": {
            "engine": "service",
            "executed_at": now,
        },
    }

    artifact_name = f"{metric_name}_validation_bundle"
    decision_label = decision_str.replace("_", "-")
    summary = (
        f"validate {metric_name} [{resolved_method}] {alternative} alpha={alpha}: "
        f"{decision_label} (kind=numeric)"
    )

    provenance: dict[str, Any] = {
        "test_step_id": test_step_id,
        "kind": "numeric",
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
        "projection_version": _PROJECTION_VERSION,
    }

    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "validate",
        "validation_bundle",
        artifact_name,
        bundle,
        summary,
        provenance=provenance,
    )
    return result
