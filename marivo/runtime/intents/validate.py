"""Validate derived intent runner (Phase 3c-3).

Deterministically expands to:
  observe(metric, left.time_scope, left.scope, result_mode=inferred_mode)
  + observe(metric, right.time_scope, right.scope, result_mode=inferred_mode)
  + test(left_obs_ref, right_obs_ref, hypothesis, method)

sample_kind controls which inferential summary mode the internal observes produce:
  "numeric" -> numeric_sample_summary
  "rate"    -> rate_sample_summary
  "auto"    -> fails SAMPLE_KIND_AMBIGUOUS in v1 (metric capability hints not yet in schema)

Design contract: docs/analysis/intents/derived/validate.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.runtime.intents.derived_envelopes import (
    aoi_artifact_dump,
    build_derived_bundle_envelope,
    build_failed_derived_bundle_envelope,
)
from marivo.runtime.intents.normalization import normalize_metric_ref
from marivo.runtime.intents.observe import run_observe_intent
from marivo.runtime.intents.test import run_test_intent

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_DERIVED_LOGIC_VERSION = "1.0"
_PROJECTION_VERSION = "validation_bundle.v1"

_VALID_METHODS: frozenset[str] = frozenset({"auto", "welch_t", "two_proportion_z"})
_VALID_ALTERNATIVES: frozenset[str] = frozenset({"two_sided", "greater", "less"})

_SAMPLE_KIND_TO_RESULT_MODE: dict[str, str] = {
    "numeric": "numeric_sample_summary",
    "rate": "rate_sample_summary",
}


def run_validate_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `validate` derived intent.

    Expands to: observe(left) + observe(right) + test(left_ref, right_ref)

    Input (from ValidateRequest):
      metric:      published semantic metric
      left:        { time_scope, scope? } — primary / treatment side
      right:       { time_scope, scope? } — comparison / control side
      sample_kind: "numeric" | "rate" | "auto" (auto fails in v1)
      hypothesis:  { family?, alternative?, alpha?, label? }
      method:      "auto" | "welch_t" | "two_proportion_z" (passed through to test)

    Failure semantics:
      - sample_kind="auto" → SAMPLE_KIND_AMBIGUOUS (v1 limitation)
      - observe failures → hard fail
      - observation_type mismatch → hard fail
      - test failures → hard fail
      - test needs_attention → validation.status="needs_attention", result still returned
    """
    p = params or {}

    # ── Input validation ───────────────────────────────────────────────────────
    try:
        metric_ref = normalize_metric_ref(p.get("metric"))
    except ValueError:
        raise ValueError("validate: INVALID_ARGUMENT - metric is required") from None
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    left_input: dict[str, Any] = p.get("left") or {}
    right_input: dict[str, Any] = p.get("right") or {}

    left_time_scope: dict[str, Any] | None = left_input.get("time_scope")
    if (
        not isinstance(left_time_scope, dict)
        or not left_time_scope.get("start")
        or not left_time_scope.get("end")
    ):
        raise ValueError(
            "validate: INVALID_ARGUMENT - left.time_scope is required with 'start' and 'end'"
        )

    right_time_scope: dict[str, Any] | None = right_input.get("time_scope")
    if (
        not isinstance(right_time_scope, dict)
        or not right_time_scope.get("start")
        or not right_time_scope.get("end")
    ):
        raise ValueError(
            "validate: INVALID_ARGUMENT - right.time_scope is required with 'start' and 'end'"
        )

    left_scope: dict[str, Any] | None = left_input.get("scope") or None
    right_scope: dict[str, Any] | None = right_input.get("scope") or None

    raw_sample_kind: str = str(p.get("sample_kind") or "auto").lower()
    if raw_sample_kind == "auto":
        raise ValueError(
            "validate: SAMPLE_KIND_AMBIGUOUS - sample_kind='auto' cannot be uniquely resolved "
            "in v1. Specify 'numeric' or 'rate' explicitly."
        )
    if raw_sample_kind not in {"numeric", "rate"}:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - sample_kind must be 'numeric' or 'rate', "
            f"got '{raw_sample_kind}'"
        )

    result_mode: str = _SAMPLE_KIND_TO_RESULT_MODE[raw_sample_kind]
    resolved_sample_kind: str = raw_sample_kind  # "numeric" or "rate"

    # Hypothesis parsing and validation
    hypothesis_raw: dict[str, Any] = p.get("hypothesis") or {}
    family: str = str(hypothesis_raw.get("family") or "difference").lower()
    if family != "difference":
        raise ValueError(
            f"validate: INVALID_ARGUMENT - hypothesis.family must be 'difference' in v1, "
            f"got '{family}'"
        )

    alternative: str = str(hypothesis_raw.get("alternative") or "two_sided").lower()
    if alternative not in _VALID_ALTERNATIVES:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - hypothesis.alternative must be one of "
            f"{sorted(_VALID_ALTERNATIVES)}, got '{alternative}'"
        )

    alpha_raw = hypothesis_raw.get("alpha")
    alpha: float = 0.05
    if alpha_raw is not None:
        try:
            alpha = float(alpha_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "validate: INVALID_ARGUMENT - hypothesis.alpha must be a number"
            ) from exc
    if not (0.0 < alpha < 1.0):
        raise ValueError(
            f"validate: INVALID_ARGUMENT - hypothesis.alpha must be in (0, 1), got {alpha}"
        )

    hyp_label: str | None = hypothesis_raw.get("label") or None

    raw_method: str = str(p.get("method") or "auto").lower()
    if raw_method not in _VALID_METHODS:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - method must be one of "
            f"{sorted(_VALID_METHODS)}, got '{raw_method}'"
        )

    # ── Step 1: observe left ───────────────────────────────────────────────────
    try:
        left_obs = run_observe_intent(
            runtime,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": left_time_scope,
                "scope": left_scope,
                "result_mode": result_mode,
                # granularity=null, dimensions=null → inferential summary mode
            },
        )
    except Exception as exc:
        raise ValueError(f"validate: OBSERVE_FAILED - left observation failed: {exc}") from exc

    left_step_id: str = left_obs["step_ref"]["step_id"]
    left_artifact_id: str = left_obs["artifact_id"]
    left_obs_type: str = left_obs.get("observation_type") or ""

    # ── Step 2: observe right ──────────────────────────────────────────────────
    try:
        right_obs = run_observe_intent(
            runtime,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": right_time_scope,
                "scope": right_scope,
                "result_mode": result_mode,
            },
        )
    except Exception as exc:
        raise ValueError(f"validate: OBSERVE_FAILED - right observation failed: {exc}") from exc

    right_step_id: str = right_obs["step_ref"]["step_id"]
    right_artifact_id: str = right_obs["artifact_id"]
    right_obs_type: str = right_obs.get("observation_type") or ""

    # ── Step 2b: observation type consistency check ────────────────────────────
    if left_obs_type != right_obs_type:
        raise ValueError(
            f"validate: OBSERVATION_TYPE_MISMATCH - "
            f"left observation_type='{left_obs_type}' differs from "
            f"right observation_type='{right_obs_type}'"
        )

    # ── Build typed obs refs (reused for test input and bundle output) ────────
    left_obs_ref: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_step_id,
        "artifact_id": left_artifact_id,
        "observation_type": left_obs_type,
    }
    right_obs_ref: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_step_id,
        "artifact_id": right_artifact_id,
        "observation_type": right_obs_type,
    }

    # ── Step 3: test ──────────────────────────────────────────────────────────
    try:
        test_result = run_test_intent(
            runtime,
            session_id,
            {
                "left_ref": left_obs_ref,
                "right_ref": right_obs_ref,
                "hypothesis": {
                    "family": family,
                    "alternative": alternative,
                    "alpha": alpha,
                    "label": hyp_label,
                },
                "method": raw_method,
            },
        )
    except Exception as exc:
        return build_failed_derived_bundle_envelope(
            runtime=runtime,
            session_id=session_id,
            step_type="validate",
            bundle_type="validation_bundle",
            artifact_name=f"{metric_name}_validation_bundle",
            exc=exc,
        )

    test_step_id: str = test_result["step_ref"]["step_id"]
    test_artifact_id: str = test_result["artifact_id"]
    resolved_method: str = test_result.get("method") or ""
    if resolved_method not in {"welch_t", "two_proportion_z"}:
        raise ValueError(
            f"validate: TEST_METHOD_UNRESOLVED - test did not return a concrete method "
            f"(got '{resolved_method}'). This is an internal error."
        )

    # ── Step 4: derive validation status from test result ─────────────────────
    test_validation: dict[str, Any] = test_result.get("validation") or {}
    test_validation_status: str = test_validation.get("status") or "valid"

    bundle_issues: list[dict[str, Any]] = []
    if test_validation_status == "needs_attention":
        for iss in test_validation.get("issues") or []:
            bundle_issues.append(
                {
                    "code": "test_needs_attention",
                    "severity": iss.get("severity", "warning"),
                    "message": iss.get("message", "test returned needs_attention"),
                }
            )

    validation_status: str = (
        "needs_attention" if test_validation_status == "needs_attention" else "validated"
    )

    # ── Step 5: derive inference result ──────────────────────────────────────
    test_decision: dict[str, Any] = test_result.get("decision") or {}
    reject_null: bool | None = test_decision.get("reject_null")

    if reject_null is True:
        decision_str: str = "reject_null"
    elif reject_null is False:
        decision_str = "fail_to_reject"
    else:
        decision_str = "undetermined"

    inference_result: dict[str, Any] | None = {
        "decision": decision_str,
        "p_value": test_result.get("p_value"),
        "estimate": test_result.get("estimate"),
    }

    # ── Step 6: build test ref ────────────────────────────────────────────────
    test_ref_out: dict[str, Any] = {
        "step_type": "test",
        "session_id": session_id,
        "step_id": test_step_id,
        "artifact_id": test_artifact_id,
        "result_type": "hypothesis_test",
    }

    # ── Step 7: assemble validation_bundle ────────────────────────────────────
    now = datetime.now(UTC).isoformat()

    left_resolved: dict[str, Any] = {
        "time_scope": left_obs.get("time_scope") or left_time_scope,
        "scope": left_scope,
    }
    right_resolved: dict[str, Any] = {
        "time_scope": right_obs.get("time_scope") or right_time_scope,
        "scope": right_scope,
    }

    hypothesis_out: dict[str, Any] = {
        "family": family,
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
        "left": left_resolved,
        "right": right_resolved,
        "sample_kind": resolved_sample_kind,
        "hypothesis": hypothesis_out,
        "method": resolved_method,
        "validation": {
            "status": validation_status,
            "issues": bundle_issues,
        },
        "refs": {
            "left_observation_ref": left_obs_ref,
            "right_observation_ref": right_obs_ref,
            "test_ref": test_ref_out,
        },
        "result": inference_result,
        "provenance": {
            "session_id": session_id,
            "source_observation_refs": [left_obs_ref, right_obs_ref],
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

    # ── Step 8: persist bundle ────────────────────────────────────────────────
    artifact_name = f"{metric_name}_validation_bundle"
    decision_label = decision_str.replace("_", "-")
    summary = (
        f"validate {metric_name} [{resolved_method}] {alternative} α={alpha}: "
        f"{decision_label} (sample_kind={resolved_sample_kind})"
    )

    provenance: dict[str, Any] = {
        "left_step_id": left_step_id,
        "right_step_id": right_step_id,
        "test_step_id": test_step_id,
        "sample_kind": resolved_sample_kind,
        "result_mode": result_mode,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
        "projection_version": _PROJECTION_VERSION,
    }
    product_status = "succeeded" if validation_status == "validated" else "needs_attention"
    return build_derived_bundle_envelope(
        runtime=runtime,
        session_id=session_id,
        step_type="validate",
        bundle_type="validation_bundle",
        artifact_name=artifact_name,
        aoi_artifacts=[aoi_artifact_dump(test_result)],
        summary=summary,
        product_status=product_status,
        issues=bundle_issues,
        legacy_bundle=bundle,
        provenance=provenance,
    )
