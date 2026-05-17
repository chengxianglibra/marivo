"""Validate derived intent runner — numeric (Welch's t) only.

Orchestrates the source-type test intent with metric + slices,
then builds a validation_bundle artifact wrapping the test result.

Per AOI v0.2, validate is a derived request contract under AOI's
derived namespace. The response bundle remains Marivo-owned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.intents.test import _SIGNIFICANCE_ALPHA, run_test_intent

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

    left_filter: Any = left_raw.get("filter")
    right_filter: Any = right_raw.get("filter")

    hypothesis_raw: dict[str, Any] = p.get("hypothesis") or {}
    unexpected_hypothesis_keys = set(hypothesis_raw) - {"family", "alternative", "significance"}
    if unexpected_hypothesis_keys:
        raise ValueError(
            "validate: INVALID_ARGUMENT - unsupported hypothesis field(s): "
            f"{sorted(unexpected_hypothesis_keys)}"
        )
    family: str = str(hypothesis_raw.get("family") or "two_sample_mean").lower()
    if family != "two_sample_mean":
        raise ValueError("validate: INVALID_ARGUMENT - hypothesis.family must be 'two_sample_mean'")
    alternative: str = str(hypothesis_raw.get("alternative") or "two_sided").lower()
    significance = str(hypothesis_raw.get("significance") or "balanced").lower()
    try:
        alpha = _SIGNIFICANCE_ALPHA[significance]
    except KeyError as exc:
        raise ValueError(
            "validate: INVALID_ARGUMENT - hypothesis.significance must be one of "
            f"{sorted(_SIGNIFICANCE_ALPHA)}, got '{significance}'"
        ) from exc
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    # ── Build test params (source-type) ──────────────────────────────────
    left_test_slice: dict[str, Any] = {"time_scope": left_time_scope}
    if left_filter is not None:
        left_test_slice["filter"] = left_filter
    right_test_slice: dict[str, Any] = {"time_scope": right_time_scope}
    if right_filter is not None:
        right_test_slice["filter"] = right_filter

    test_params: dict[str, Any] = {
        "metric": metric_ref,
        "left": left_test_slice,
        "right": right_test_slice,
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": alternative,
            "significance": significance,
        },
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
        "significance": significance,
        "alpha": alpha,
    }

    bundle: dict[str, Any] = {
        "result_type": "validation_bundle",
        "intent_type": "validate",
        "step_type": "validate",
        "artifact_schema_version": "v1",
        "derivation_version": _DERIVED_LOGIC_VERSION,
        "metric": metric_ref,
        "left": {"time_scope": left_time_scope, "filter": left_filter},
        "right": {"time_scope": right_time_scope, "filter": right_filter},
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
