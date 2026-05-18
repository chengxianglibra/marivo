"""Validate derived intent runner — numeric (Welch's t) only.

Orchestrates the source-type test intent with metric + slices,
then builds a validation_bundle artifact wrapping the test result.

Per AOI v0.2, validate is a derived request contract under AOI's
derived namespace. The response bundle remains Marivo-owned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from marivo.runtime.intents.derived_envelopes import (
    aoi_artifact_dump,
    build_derived_bundle_envelope,
)
from marivo.runtime.intents.test import _SIGNIFICANCE_ALPHA, run_test_intent
from marivo.time_scope import TimeScopeGrain

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_DERIVED_LOGIC_VERSION = "1.0"
_PROJECTION_VERSION = "validation_bundle.v1"
_REQUEST_FIELDS: frozenset[str] = frozenset(
    {"metric", "current", "baseline", "grain", "hypothesis"}
)
_SLICE_FIELDS: frozenset[str] = frozenset({"time_scope", "filter"})
_HYPOTHESIS_FIELDS: frozenset[str] = frozenset({"family", "alternative", "significance"})
_VALID_FAMILIES: frozenset[str] = frozenset({"two_sample_mean"})
_VALID_ALTERNATIVES: frozenset[str] = frozenset({"two_sided", "greater", "less"})
_VALID_GRAINS: frozenset[str] = frozenset({"hour", "day", "week", "month", "quarter", "year"})


def run_validate_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    p = _validate_request(params)

    # ── Input extraction ──────────────────────────────────────────────────
    metric_raw = p["metric"]
    metric_ref = metric_raw.strip() if isinstance(metric_raw, str) else ""
    if not metric_ref:
        raise ValueError("validate: INVALID_ARGUMENT - metric is required")

    current_raw = _validate_slice(p["current"], label="current")
    baseline_raw = _validate_slice(p["baseline"], label="baseline")
    current_time_scope: dict[str, Any] = current_raw["time_scope"]
    baseline_time_scope: dict[str, Any] = baseline_raw["time_scope"]
    current_filter: Any = current_raw.get("filter")
    baseline_filter: Any = baseline_raw.get("filter")
    grain = _validate_grain(p["grain"])

    hypothesis_raw = _validate_hypothesis(p["hypothesis"])
    family: str = hypothesis_raw["family"]
    alternative: str = hypothesis_raw["alternative"]
    significance: str = hypothesis_raw["significance"]
    alpha = _SIGNIFICANCE_ALPHA[significance]
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    # ── Build test params (source-type) ──────────────────────────────────
    current_test_slice: dict[str, Any] = {"time_scope": current_time_scope}
    if current_filter is not None:
        current_test_slice["filter"] = current_filter
    baseline_test_slice: dict[str, Any] = {"time_scope": baseline_time_scope}
    if baseline_filter is not None:
        baseline_test_slice["filter"] = baseline_filter

    test_params: dict[str, Any] = {
        "metric": metric_ref,
        "current": current_test_slice,
        "baseline": baseline_test_slice,
        "grain": grain,
        "kind": "numeric",
        "hypothesis": {
            "family": family,
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
    hypothesis_out: dict[str, Any] = {
        "family": family,
        "alternative": alternative,
        "significance": significance,
        "alpha": alpha,
    }

    result_payload: dict[str, Any] = {
        "result_type": "validation_bundle",
        "intent_type": "validate",
        "step_type": "validate",
        "artifact_schema_version": "v1",
        "derivation_version": _DERIVED_LOGIC_VERSION,
        "metric": metric_ref,
        "current": {"time_scope": current_time_scope, "filter": current_filter},
        "baseline": {"time_scope": baseline_time_scope, "filter": baseline_filter},
        "grain": grain,
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
            "executed_at": datetime.now(UTC).isoformat(),
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
        "grain": grain,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
        "projection_version": _PROJECTION_VERSION,
    }

    product_status = "needs_attention" if validation_status == "needs_attention" else "succeeded"
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
        provenance=provenance,
        result_payload=result_payload,
        product_metadata_payload={
            "validation": {
                "status": validation_status,
                "issues": bundle_issues,
            },
            "refs": {
                "test_ref": test_ref_out,
            },
        },
    )


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("validate: INVALID_ARGUMENT - params must be a validate request object")

    missing_fields = _REQUEST_FIELDS - set(value)
    if missing_fields:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - missing required field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(value) - _REQUEST_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - unsupported field(s): {sorted(unexpected_fields)}"
        )
    return value


def _validate_grain(value: Any) -> TimeScopeGrain:
    if not isinstance(value, str):
        raise ValueError("validate: INVALID_ARGUMENT - grain must be a string")
    if value not in _VALID_GRAINS:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - grain must be one of {sorted(_VALID_GRAINS)}, "
            f"got '{value}'"
        )
    return cast("TimeScopeGrain", value)


def _validate_slice(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"validate: INVALID_ARGUMENT - {label} must be an object")

    missing_fields = {"time_scope"} - set(value)
    if missing_fields:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - missing {label} field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(value) - _SLICE_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - unsupported {label} field(s): "
            f"{sorted(unexpected_fields)}"
        )

    time_scope = value["time_scope"]
    if not isinstance(time_scope, dict) or not time_scope:
        raise ValueError(f"validate: INVALID_ARGUMENT - {label}.time_scope is required")
    if "filter" in value and value["filter"] is None:
        raise ValueError(f"validate: INVALID_ARGUMENT - {label}.filter must not be null")
    return value


def _validate_hypothesis(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("validate: INVALID_ARGUMENT - hypothesis must be an object")

    missing_fields = _HYPOTHESIS_FIELDS - set(value)
    if missing_fields:
        raise ValueError(
            f"validate: INVALID_ARGUMENT - missing hypothesis field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(value) - _HYPOTHESIS_FIELDS
    if unexpected_fields:
        raise ValueError(
            "validate: INVALID_ARGUMENT - unsupported hypothesis field(s): "
            f"{sorted(unexpected_fields)}"
        )

    family = value["family"]
    if not isinstance(family, str):
        raise ValueError("validate: INVALID_ARGUMENT - hypothesis.family must be a string")
    if family not in _VALID_FAMILIES:
        raise ValueError(
            "validate: INVALID_ARGUMENT - hypothesis.family must be one of "
            f"{sorted(_VALID_FAMILIES)}, got '{family}'"
        )

    alternative = value["alternative"]
    if not isinstance(alternative, str):
        raise ValueError("validate: INVALID_ARGUMENT - hypothesis.alternative must be a string")
    if alternative not in _VALID_ALTERNATIVES:
        raise ValueError(
            "validate: INVALID_ARGUMENT - hypothesis.alternative must be one of "
            f"{sorted(_VALID_ALTERNATIVES)}, got '{alternative}'"
        )

    significance = value["significance"]
    if not isinstance(significance, str):
        raise ValueError("validate: INVALID_ARGUMENT - hypothesis.significance must be a string")
    if significance not in _SIGNIFICANCE_ALPHA:
        raise ValueError(
            "validate: INVALID_ARGUMENT - hypothesis.significance must be one of "
            f"{sorted(_SIGNIFICANCE_ALPHA)}, got '{significance}'"
        )

    return {
        "family": family,
        "alternative": alternative,
        "significance": significance,
    }
