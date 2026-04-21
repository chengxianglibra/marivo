"""Attribute derived intent runner (Phase 3c-1).

Deterministically expands to: observe(left) + observe(right) + compare + decompose × N

The expansion is fixed: given the same request and system state, it always produces the same
logical DAG. No intermediate human decisions or planner involvement.

Design contract: docs/analysis/intents/derived/attribute.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.analysis_core.additivity_capabilities import derive_additivity_capabilities
from app.intents.compare import run_compare_intent
from app.intents.decompose import run_decompose_intent
from app.intents.observe import run_observe_intent

if TYPE_CHECKING:
    from app.service import SemanticLayerService

_DEFAULT_DECOMPOSITION_LIMIT = 5
_MAX_DECOMPOSITION_LIMIT = 100
_DERIVED_LOGIC_VERSION = "1.0"
_PROJECTION_VERSION = "attribute_bundle.v1"
_SHARE_SUPPRESSION_POLICY = "suppress_on_reconciliation_needs_attention"
_RECONCILIATION_ISSUE_CODES = frozenset(
    {
        "attribution_not_reconcilable",
        "scope_recomputation_failed",
    }
)


def run_attribute_intent(
    svc: SemanticLayerService, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute an `attribute` derived intent.

    Expands to: observe(left) + observe(right) + compare + decompose × len(dimensions)

    Input (from AttributeRequest):
      metric:               published semantic metric
      left:                 { time_scope, calendar_policy_ref?, scope? } — current / treatment side
      right:                { time_scope, calendar_policy_ref?, scope? } — baseline / control side
      dimensions:           non-empty list of attribution dimensions
      decomposition_method: "delta_share" (only v1 option, default)
      decomposition_limit:  max driver rows per dimension (default 5, max 100)

    Output: attribute_bundle artifact with full lineage to all atomic artifacts.

    Failure semantics:
      - Fails if either observe fails
      - Fails if compare is NOT_COMPARABLE
      - Fails if any decompose is NOT_ATTRIBUTABLE
      - compare/decompose needs_attention → included in bundle with issues
    """
    p = params or {}

    # ── Input validation ───────────────────────────────────────────────────────
    metric_ref: str = (p.get("metric") or "").strip()
    if not metric_ref:
        raise ValueError("attribute: INVALID_ARGUMENT - metric is required")
    metric_ref = svc.normalize_intent_metric_ref(metric_ref)
    metric_name = svc.metric_name_from_ref(metric_ref)

    left_input: dict[str, Any] = p.get("left") or {}
    right_input: dict[str, Any] = p.get("right") or {}

    current_time_scope: dict[str, Any] | None = left_input.get("time_scope")
    if not isinstance(current_time_scope, dict) or not current_time_scope.get("kind"):
        raise ValueError(
            "attribute: INVALID_ARGUMENT - left.time_scope is required with a valid 'kind'"
        )

    baseline_time_scope: dict[str, Any] | None = right_input.get("time_scope")
    if not isinstance(baseline_time_scope, dict) or not baseline_time_scope.get("kind"):
        raise ValueError(
            "attribute: INVALID_ARGUMENT - right.time_scope is required with a valid 'kind'"
        )

    left_scope: dict[str, Any] | None = left_input.get("scope")
    right_scope: dict[str, Any] | None = right_input.get("scope")
    left_calendar_policy_ref: str | None = left_input.get("calendar_policy_ref")
    right_calendar_policy_ref: str | None = right_input.get("calendar_policy_ref")

    raw_dimensions: list[Any] = p.get("dimensions") or []
    if not raw_dimensions:
        raise ValueError("attribute: INVALID_ARGUMENT - dimensions must be a non-empty list")
    dimensions: list[str] = []
    for d in raw_dimensions:
        d_str = str(d).strip()
        if not d_str:
            raise ValueError(
                "attribute: INVALID_ARGUMENT - dimensions must not contain blank strings"
            )
        dimensions.append(d_str)
    # Deduplicate while preserving order (per design doc: dedup then check non-empty)
    seen: set[str] = set()
    deduped_dims: list[str] = []
    for d in dimensions:
        if d not in seen:
            seen.add(d)
            deduped_dims.append(d)
    if not deduped_dims:
        raise ValueError("attribute: INVALID_ARGUMENT - dimensions is empty after deduplication")
    dimensions = deduped_dims

    decomposition_method: str = p.get("decomposition_method") or "delta_share"
    if decomposition_method != "delta_share":
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - only 'delta_share' is supported in v1, "
            f"got '{decomposition_method}'"
        )

    raw_limit: Any = p.get("decomposition_limit")
    if raw_limit is None:
        decomposition_limit = _DEFAULT_DECOMPOSITION_LIMIT
    else:
        try:
            decomposition_limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"attribute: INVALID_ARGUMENT - decomposition_limit must be a positive integer, "
                f"got {raw_limit!r}"
            ) from exc
        if decomposition_limit <= 0:
            raise ValueError(
                f"attribute: INVALID_ARGUMENT - decomposition_limit must be > 0, "
                f"got {decomposition_limit}"
            )
        if decomposition_limit > _MAX_DECOMPOSITION_LIMIT:
            raise ValueError(
                f"attribute: INVALID_ARGUMENT - decomposition_limit exceeds max allowed "
                f"({_MAX_DECOMPOSITION_LIMIT}), got {decomposition_limit}"
            )

    # ── Pre-check: verify metric supports attribute ──────────────────────────
    resolved_metric = svc.semantic_repository.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"attribute: metric '{metric_name}' not found or not published")

    additivity_caps = derive_additivity_capabilities(
        header={
            "additivity": resolved_metric.additivity,
            "primary_time_ref": resolved_metric.primary_time_ref,
            "sample_kind": resolved_metric.sample_kind,
        },
    )
    if not additivity_caps.supports_attribute:
        raise ValueError(
            f"attribute: ADDITIVITY_CONSTRAINT - metric '{metric_name}' does not support "
            f"attribution (additivity='{resolved_metric.additivity}', "
            f"dimension_policy='{additivity_caps.dimension_policy}', "
            f"time_axis_policy='{additivity_caps.time_axis_policy}')"
            + (f"; {additivity_caps.remediation_hint}" if additivity_caps.remediation_hint else "")
        )

    # Dimension-level gate: check each requested dimension against the policy
    if additivity_caps.dimension_policy == "none":
        raise ValueError(
            f"attribute: ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED - metric "
            f"'{metric_name}' has dimension_policy='none'; no dimensions can be "
            f"used for attribution. Requested dimensions: {dimensions}"
        )

    # ── Step 1: observe left (current) ────────────────────────────────────────
    try:
        left_obs = run_observe_intent(
            svc,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": current_time_scope,
                "calendar_policy_ref": left_calendar_policy_ref,
                "scope": left_scope,
                # no granularity, no dimensions → scalar mode
            },
        )
    except Exception as exc:
        raise ValueError(f"attribute: OBSERVE_FAILED - current observation failed: {exc}") from exc

    left_obs_type: str | None = left_obs.get("observation_type")
    if left_obs_type != "scalar":
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - left observe produced observation_type="
            f"'{left_obs_type}'; attribute v1 requires scalar observations"
        )

    # ── Step 2: observe right (baseline) ──────────────────────────────────────
    try:
        right_obs = run_observe_intent(
            svc,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": baseline_time_scope,
                "calendar_policy_ref": right_calendar_policy_ref,
                "scope": right_scope,
            },
        )
    except Exception as exc:
        raise ValueError(f"attribute: OBSERVE_FAILED - baseline observation failed: {exc}") from exc

    right_obs_type: str | None = right_obs.get("observation_type")
    if right_obs_type != "scalar":
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - right observe produced observation_type="
            f"'{right_obs_type}'; attribute v1 requires scalar observations"
        )

    # ── Step 3: compare ───────────────────────────────────────────────────────
    left_step_id: str = left_obs["step_ref"]["step_id"]
    right_step_id: str = right_obs["step_ref"]["step_id"]

    try:
        compare_result = run_compare_intent(
            svc,
            session_id,
            {
                "left_ref": {
                    "step_id": left_step_id,
                    "session_id": session_id,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": right_step_id,
                    "session_id": session_id,
                    "step_type": "observe",
                },
                "mode": "scalar",
            },
        )
    except Exception as exc:
        raise ValueError(f"attribute: COMPARE_FAILED - comparison failed: {exc}") from exc

    compare_step_id: str = compare_result["step_ref"]["step_id"]
    compare_artifact_id: str = compare_result["artifact_id"]
    comparability: dict[str, Any] = compare_result.get("comparability") or {}
    comparability_status: str = comparability.get("status") or "needs_attention"

    compare_ref: dict[str, Any] = {
        "step_type": "compare",
        "session_id": session_id,
        "step_id": compare_step_id,
        "artifact_id": compare_artifact_id,
        "comparison_type": "scalar_delta",
    }

    # ── Step 4: decompose × N ─────────────────────────────────────────────────
    drivers: list[dict[str, Any]] = []
    validation_issues: list[dict[str, Any]] = []

    for dimension in dimensions:
        try:
            decompose_result = run_decompose_intent(
                svc,
                session_id,
                {
                    "compare_ref": {
                        "step_id": compare_step_id,
                        "session_id": session_id,
                        "step_type": "compare",
                    },
                    "dimension": dimension,
                    "method": decomposition_method,
                },
            )
        except Exception as exc:
            raise ValueError(
                f"attribute: DECOMPOSE_FAILED for dimension '{dimension}': {exc}"
            ) from exc

        decompose_step_id: str = decompose_result["step_ref"]["step_id"]
        decompose_artifact_id: str = decompose_result["artifact_id"]
        decompose_ref: dict[str, Any] = {
            "step_type": "decompose",
            "session_id": session_id,
            "step_id": decompose_step_id,
            "artifact_id": decompose_artifact_id,
            "decomposition_type": "delta_decomposition",
        }

        attribution: dict[str, Any] = decompose_result.get("attribution") or {}
        attribution_status: str = attribution.get("status") or "needs_attention"
        # Remap decompose issue codes to AttributeIssue schema codes
        raw_decompose_issues: list[dict[str, Any]] = attribution.get("issues") or []
        remapped_decompose_issues: list[dict[str, Any]] = [
            _remap_decompose_issue(iss, dimension) for iss in raw_decompose_issues
        ]

        # Propagate dimension-level issues into bundle validation issues
        for issue in remapped_decompose_issues:
            validation_issues.append(issue)

        all_rows: list[dict[str, Any]] = decompose_result.get("rows") or []
        total_row_count: int = len(all_rows)
        # Truncate to decomposition_limit (decompose runner does not apply this limit)
        returned_rows: list[dict[str, Any]] = [dict(row) for row in all_rows[:decomposition_limit]]
        returned_row_count: int = len(returned_rows)
        is_truncated: bool = total_row_count > returned_row_count
        share_suppressed = _should_suppress_contribution_shares(
            attribution_status=attribution_status,
            raw_issues=raw_decompose_issues,
            unexplained_reason=decompose_result.get("unexplained_reason"),
        )
        interpretation = "directional_only" if share_suppressed else "quantitative"
        if share_suppressed:
            for row in returned_rows:
                row["contribution_share"] = None

        # Build others aggregation if truncated
        others_abs: float | None = None
        others_share: float | None = None
        if is_truncated:
            tail_rows = all_rows[decomposition_limit:]
            tail_abs_sum: float = 0.0
            all_have_abs = True
            for r in tail_rows:
                rv = r.get("absolute_contribution")
                if rv is None:
                    all_have_abs = False
                    break
                tail_abs_sum += rv
            if all_have_abs:
                others_abs = tail_abs_sum
                scope_delta = decompose_result.get("scope_absolute_delta")
                if scope_delta is not None and scope_delta != 0:
                    others_share = others_abs / scope_delta
                else:
                    others_share = None
        if share_suppressed:
            others_share = None

        driver_issues: list[dict[str, Any]] = list(remapped_decompose_issues)
        if share_suppressed:
            share_suppression_issue = _share_suppression_issue(dimension)
            driver_issues.append(share_suppression_issue)
            validation_issues.append(share_suppression_issue)
        if is_truncated:
            driver_issues.append(
                {
                    "code": "driver_truncated",
                    "severity": "warning",
                    "message": (
                        f"Dimension '{dimension}': {total_row_count} rows available, "
                        f"returning top {returned_row_count} (decomposition_limit={decomposition_limit})."
                    ),
                    "dimension": dimension,
                }
            )

        driver: dict[str, Any] = {
            "dimension": dimension,
            "decompose_ref": decompose_ref,
            "attribution_status": attribution_status,
            "interpretation": interpretation,
            "share_suppressed": share_suppressed,
            "rows": returned_rows,
            "returned_row_count": returned_row_count,
            "total_row_count": total_row_count,
            "is_truncated": is_truncated,
            "others_absolute_contribution": others_abs if is_truncated else None,
            "others_contribution_share": others_share if is_truncated else None,
            "unexplained_absolute_delta": decompose_result.get("unexplained_absolute_delta"),
            "unexplained_share": decompose_result.get("unexplained_share"),
            "unexplained_reason": decompose_result.get("unexplained_reason"),
            "issues": driver_issues,
        }
        drivers.append(driver)

    # ── Step 5: derive validation status ──────────────────────────────────────
    all_attributable = all(d["attribution_status"] == "attributable" for d in drivers)
    if comparability_status == "comparable" and all_attributable:
        validation_status = "attributable"
    else:
        validation_status = "needs_attention"

    # Propagate compare-level issues (remap codes to AttributeIssue schema)
    for issue in comparability.get("issues") or []:
        validation_issues.append(_remap_compare_issue(issue))

    # ── Step 6: build typed refs ───────────────────────────────────────────────
    left_artifact_id: str = left_obs["artifact_id"]
    right_artifact_id: str = right_obs["artifact_id"]

    left_ref_typed: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_step_id,
        "artifact_id": left_artifact_id,
        "observation_type": "scalar",
    }
    right_ref_typed: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_step_id,
        "artifact_id": right_artifact_id,
        "observation_type": "scalar",
    }

    decompose_refs: list[dict[str, Any]] = [d["decompose_ref"] for d in drivers]

    lineage: dict[str, Any] = {
        "source_compare_ref": compare_ref,
        "source_observation_refs": [left_ref_typed, right_ref_typed],
        "source_decompose_refs": decompose_refs,
    }

    version: dict[str, Any] = {
        "intent_contract_version": "attribute.v1",
        "projection_version": _PROJECTION_VERSION,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
    }

    # ── Step 7: build ScalarDeltaSummary from compare_result ──────────────────
    comparison: dict[str, Any] = {
        "comparison_type": "scalar_delta",
        "left_value": compare_result.get("left_value"),
        "right_value": compare_result.get("right_value"),
        "absolute_delta": compare_result.get("absolute_delta"),
        "relative_delta": compare_result.get("relative_delta"),
        "direction": compare_result.get("direction") or "undefined",
        "comparability_status": comparability_status,
    }

    # ── Step 8: assemble bundle ────────────────────────────────────────────────
    now = datetime.now(UTC).isoformat()
    left_resolved: dict[str, Any] = {
        "time_scope": left_obs.get("time_scope") or current_time_scope,
        "scope": left_scope,
    }
    right_resolved: dict[str, Any] = {
        "time_scope": right_obs.get("time_scope") or baseline_time_scope,
        "scope": right_scope,
    }

    bundle: dict[str, Any] = {
        "result_type": "attribute_bundle",
        "intent_type": "attribute",
        "step_type": "attribute",
        "metric": metric_ref,
        "left": left_resolved,
        "right": right_resolved,
        "dimensions": dimensions,
        "validation": {
            "status": validation_status,
            "issues": validation_issues,
        },
        "observation_refs": {
            "left_ref": left_ref_typed,
            "right_ref": right_ref_typed,
        },
        "compare_ref": compare_ref,
        "comparison": comparison,
        "drivers": drivers,
        "lineage": lineage,
        "version": version,
        "projection_metadata": {
            "decomposition_limit": decomposition_limit,
            "driver_row_order": "inherits_decompose_order",
            "dimension_order": "request_order",
            "share_suppression_policy": _SHARE_SUPPRESSION_POLICY,
        },
        "execution_metadata": {
            "engine": "service",
            "executed_at": now,
        },
    }

    # ── Step 9: persist bundle as attribute_bundle artifact ───────────────────
    step_id = svc._new_step_id()
    artifact_id = svc._insert_artifact(
        session_id, step_id, "attribute_bundle", f"{metric_name}_attribute_bundle", bundle
    )
    bundle["step_ref"] = {
        "session_id": session_id,
        "step_id": step_id,
        "step_type": "attribute",
    }
    bundle["artifact_id"] = artifact_id

    summary = (
        f"attribute {metric_name}: {validation_status} "
        f"({len(dimensions)} dimension(s), "
        f"Δ {compare_result.get('absolute_delta') if compare_result.get('absolute_delta') is not None else 'n/a'})"
    )
    provenance: dict[str, Any] = {
        "left_step_id": left_step_id,
        "right_step_id": right_step_id,
        "compare_step_id": compare_step_id,
        "decompose_step_ids": [d["decompose_ref"]["step_id"] for d in drivers],
        "dimensions": dimensions,
        "decomposition_limit": decomposition_limit,
        "decomposition_method": decomposition_method,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
        "projection_version": _PROJECTION_VERSION,
    }
    svc._insert_step(step_id, session_id, "attribute", summary, bundle, provenance=provenance)
    return bundle


# ── Issue code remapping ───────────────────────────────────────────────────────


def _remap_decompose_issue(issue: dict[str, Any], dimension: str) -> dict[str, Any]:
    """Remap a raw decompose attribution issue to the AttributeIssue schema code."""
    return {
        "code": "decompose_needs_attention",
        "severity": issue.get("severity", "warning"),
        "message": issue.get("message", ""),
        "dimension": dimension,
    }


def _remap_compare_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Remap a raw compare comparability issue to the AttributeIssue schema code."""
    return {
        "code": "compare_needs_attention",
        "severity": issue.get("severity", "warning"),
        "message": issue.get("message", ""),
    }


def _should_suppress_contribution_shares(
    *,
    attribution_status: str,
    raw_issues: list[dict[str, Any]],
    unexplained_reason: Any,
) -> bool:
    """Return whether attribute should hide share ratios for a decompose projection."""
    if attribution_status != "needs_attention":
        return False
    if unexplained_reason == "scope_recomputation_failed":
        return True
    return any(issue.get("code") in _RECONCILIATION_ISSUE_CODES for issue in raw_issues)


def _share_suppression_issue(dimension: str) -> dict[str, Any]:
    return {
        "code": "decompose_needs_attention",
        "severity": "error",
        "message": (
            f"Dimension '{dimension}': contribution_share values were suppressed because "
            "the decomposition did not reconcile with the compare delta; rows are "
            "directional only and must not be read as precise attribution shares."
        ),
        "dimension": dimension,
    }
