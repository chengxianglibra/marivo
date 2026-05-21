"""Attribute derived intent runner (Phase 3c-1).

Deterministically expands to: observe(current) + observe(baseline) + compare + decompose × N

The expansion is fixed: given the same request and system state, it always produces the same
logical DAG. No intermediate human decisions or planner involvement.

Design contract: docs/analysis/intents/derived/attribute.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.runtime.intents._helpers import aoi_filter_to_scope
from marivo.runtime.intents.compare import run_compare_intent
from marivo.runtime.intents.decompose import run_decompose_intent
from marivo.runtime.intents.derived_envelopes import (
    aoi_artifact_dump,
    build_derived_bundle_envelope,
)
from marivo.runtime.intents.metric_frame import (
    read_compare_scalar_point,
    read_decompose_rows_from_series,
)
from marivo.runtime.intents.normalization import (
    normalize_dimensions,
    normalize_metric_ref,
)
from marivo.runtime.intents.observe import run_observe_intent

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_DEFAULT_DECOMPOSITION_LIMIT = 5
_MAX_DECOMPOSITION_LIMIT = 100
_DERIVED_LOGIC_VERSION = "1.0"
_PROJECTION_VERSION = "attribute_bundle.v1"
_SHARE_SUPPRESSION_POLICY = "suppress_on_reconciliation_needs_attention"
_REQUEST_FIELDS: frozenset[str] = frozenset(
    {"metric", "current", "baseline", "dimensions", "decomposition_limit"}
)
_OPTIONAL_REQUEST_FIELDS: frozenset[str] = frozenset({"decomposition_method"})
_SLICE_FIELDS: frozenset[str] = frozenset({"time_scope", "filter"})
_TIME_SCOPE_FIELDS: frozenset[str] = frozenset({"field", "start", "end"})
_RECONCILIATION_ISSUE_CODES = frozenset(
    {
        "attribution_not_reconcilable",
        "scope_recomputation_failed",
    }
)


def run_attribute_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    *,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute an `attribute` derived intent.

    Expands to: observe(current) + observe(baseline) + compare + decompose × len(dimensions)

    Input (from generated AOI Attribute request after lowering):
      metric:               published semantic metric
      current:              { time_scope, filter? } — current / treatment side
      baseline:             { time_scope, filter? } — baseline / control side
      dimensions:           non-empty list of attribution dimensions
      decomposition_method: optional override; defaults to method derived from
                            metric aggregation_semantics (sum→delta_share,
                            ratio→ratio_decomposition, weighted_average→weighted_decomposition)
      decomposition_limit:  max driver rows per dimension (default 5, max 100)

    Output: attribute_bundle artifact with full lineage to all atomic artifacts.

    Failure semantics:
      - Fails if either observe fails
      - Fails if compare is NOT_COMPARABLE
      - Fails if any decompose is NOT_ATTRIBUTABLE
      - compare/decompose needs_attention → included in bundle with issues
    """
    p = _validate_request(params)

    # ── Input validation ───────────────────────────────────────────────────────
    try:
        metric_ref = normalize_metric_ref(p.get("metric"))
    except ValueError:
        raise ValueError("attribute: INVALID_ARGUMENT - metric is required") from None
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    current_input = _validate_slice(p["current"], label="current")
    baseline_input = _validate_slice(p["baseline"], label="baseline")
    current_time_scope = _validate_time_scope(
        current_input["time_scope"], label="current.time_scope"
    )
    baseline_time_scope = _validate_time_scope(
        baseline_input["time_scope"], label="baseline.time_scope"
    )

    try:
        current_scope = aoi_filter_to_scope(current_input.get("filter"), label="current.filter")
        baseline_scope = aoi_filter_to_scope(baseline_input.get("filter"), label="baseline.filter")
    except ValueError as exc:
        raise ValueError(f"attribute: INVALID_ARGUMENT - {exc}") from exc

    raw_dimensions = p.get("dimensions")
    if not isinstance(raw_dimensions, list):
        raise ValueError("attribute: INVALID_ARGUMENT - dimensions must be a non-empty list")
    if any(not isinstance(dimension, str) for dimension in raw_dimensions):
        raise ValueError("attribute: INVALID_ARGUMENT - dimensions must contain only strings")
    dimensions = normalize_dimensions(raw_dimensions)
    if not dimensions:
        raise ValueError("attribute: INVALID_ARGUMENT - dimensions must be a non-empty list")

    decomposition_method: str = p.get("decomposition_method") or ""

    raw_limit: Any = p.get("decomposition_limit")
    if raw_limit is None:
        decomposition_limit = _DEFAULT_DECOMPOSITION_LIMIT
    else:
        if not isinstance(raw_limit, int) or isinstance(raw_limit, bool):
            raise ValueError(
                f"attribute: INVALID_ARGUMENT - decomposition_limit must be a positive integer, "
                f"got {raw_limit!r}"
            )
        decomposition_limit = raw_limit
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

    # ── Pre-check: verify metric supports attribute (compile-time gate) ──────
    resolved_metric = runtime.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"attribute: metric '{metric_name}' not found or not published")

    # Derive decomposition_method from metric aggregation_semantics.
    # MCP/AOI callers always populate decomposition_method with the wire default
    # "delta_share", so treat that default as unset and derive unconditionally.
    _resolved_header = resolved_metric.semantic_object.get("header") or {}
    aggregation_semantics: str = _resolved_header.get("aggregation_semantics") or "sum"
    if not decomposition_method or decomposition_method == "delta_share":
        decomposition_method = _aggregation_semantics_to_decomposition_method(aggregation_semantics)

    # ── Step 1: observe current ───────────────────────────────────────────────
    try:
        current_obs = run_observe_intent(
            runtime,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": current_time_scope,
                "scope": current_scope,
                # no granularity, no dimensions → scalar mode
            },
        )
    except Exception as exc:
        raise ValueError(f"attribute: OBSERVE_FAILED - current observation failed: {exc}") from exc

    left_obs_type: str | None = current_obs.get("observation_type")
    if left_obs_type != "scalar":
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - current observe produced observation_type="
            f"'{left_obs_type}'; attribute v1 requires scalar observations"
        )

    # ── Step 2: observe baseline ──────────────────────────────────────────────
    try:
        baseline_obs = run_observe_intent(
            runtime,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": baseline_time_scope,
                "scope": baseline_scope,
            },
        )
    except Exception as exc:
        raise ValueError(f"attribute: OBSERVE_FAILED - baseline observation failed: {exc}") from exc

    right_obs_type: str | None = baseline_obs.get("observation_type")
    if right_obs_type != "scalar":
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - baseline observe produced observation_type="
            f"'{right_obs_type}'; attribute v1 requires scalar observations"
        )

    # ── Step 3: compare ───────────────────────────────────────────────────────
    left_step_id: str = current_obs["step_ref"]["step_id"]
    right_step_id: str = baseline_obs["step_ref"]["step_id"]

    try:
        compare_result = run_compare_intent(
            runtime,
            session_id,
            {
                "current_artifact_id": current_obs["artifact_id"],
                "baseline_artifact_id": baseline_obs["artifact_id"],
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
    decompose_results: list[dict[str, Any]] = []
    validation_issues: list[dict[str, Any]] = []

    for dimension in dimensions:
        try:
            decompose_result = run_decompose_intent(
                runtime,
                session_id,
                {
                    "compare_artifact_id": compare_artifact_id,
                    "dimension": dimension,
                },
            )
        except Exception as exc:
            raise ValueError(
                f"attribute: DECOMPOSE_FAILED for dimension '{dimension}': {exc}"
            ) from exc
        decompose_results.append(decompose_result)

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

        all_rows: list[dict[str, Any]] = read_decompose_rows_from_series(decompose_result)
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
    current_artifact_id: str = current_obs["artifact_id"]
    baseline_artifact_id: str = baseline_obs["artifact_id"]

    current_ref_typed: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_step_id,
        "artifact_id": current_artifact_id,
        "observation_type": "scalar",
    }
    baseline_ref_typed: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_step_id,
        "artifact_id": baseline_artifact_id,
        "observation_type": "scalar",
    }

    decompose_refs: list[dict[str, Any]] = [d["decompose_ref"] for d in drivers]

    lineage: dict[str, Any] = {
        "source_compare_ref": compare_ref,
        "source_observation_refs": [current_ref_typed, baseline_ref_typed],
        "source_decompose_refs": decompose_refs,
    }

    version: dict[str, Any] = {
        "intent_contract_version": "attribute.v1",
        "projection_version": _PROJECTION_VERSION,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
    }

    # ── Step 7: build ScalarDeltaSummary from compare_result ──────────────────
    compare_point: dict[str, Any] = read_compare_scalar_point(compare_result)
    comparison: dict[str, Any] = {
        "comparison_type": "scalar_delta",
        "current_value": compare_point.get("current_value"),
        "baseline_value": compare_point.get("baseline_value"),
        "absolute_delta": compare_point.get("delta"),
        "relative_delta": compare_point.get("delta_pct"),
        "direction": compare_point.get("direction") or "undefined",
        "comparability_status": comparability_status,
    }

    # ── Step 8: assemble payload ────────────────────────────────────────────────
    now = datetime.now(UTC).isoformat()
    left_resolved: dict[str, Any] = {
        "time_scope": current_obs.get("time_scope") or current_time_scope,
        "scope": current_scope,
    }
    right_resolved: dict[str, Any] = {
        "time_scope": baseline_obs.get("time_scope") or baseline_time_scope,
        "scope": baseline_scope,
    }

    result_payload: dict[str, Any] = {
        "metric": metric_ref,
        "current": left_resolved,
        "baseline": right_resolved,
        "dimensions": dimensions,
        "observation_refs": {
            "current_ref": current_ref_typed,
            "baseline_ref": baseline_ref_typed,
        },
        "compare_ref": compare_ref,
        "comparison": comparison,
        "drivers": drivers,
        "lineage": lineage,
    }
    product_metadata_payload: dict[str, Any] = {
        "validation": {
            "status": validation_status,
            "issues": validation_issues,
        },
        "version": version,
        "projection_metadata": {
            "decomposition_limit": decomposition_limit,
            "driver_row_order": "inherits_decompose_order",
            "dimension_order": "request_order",
            "share_suppression_policy": _SHARE_SUPPRESSION_POLICY,
            "time_boundary_constraint": {
                "scope": "frozen_compare_window",
                "time_rollup_implied": False,
            },
        },
        "execution_metadata": {
            "engine": "service",
            "executed_at": now,
        },
    }

    # ── Step 9: persist bundle as attribute_bundle artifact ───────────────────
    summary = (
        f"attribute {metric_name}: {validation_status} "
        f"({len(dimensions)} dimension(s), "
        f"Δ {comparison['absolute_delta'] if comparison['absolute_delta'] is not None else 'n/a'})"
    )
    provenance: dict[str, Any] = {
        "current_step_id": left_step_id,
        "baseline_step_id": right_step_id,
        "compare_step_id": compare_step_id,
        "decompose_step_ids": [d["decompose_ref"]["step_id"] for d in drivers],
        "dimensions": dimensions,
        "decomposition_limit": decomposition_limit,
        "decomposition_method": decomposition_method,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
        "projection_version": _PROJECTION_VERSION,
    }
    product_status = "succeeded" if validation_status == "attributable" else "needs_attention"
    return build_derived_bundle_envelope(
        runtime=runtime,
        session_id=session_id,
        step_type="attribute",
        bundle_type="attribute_bundle",
        artifact_name=f"{metric_name}_attribute_bundle",
        aoi_artifacts=[
            aoi_artifact_dump(compare_result),
            *[aoi_artifact_dump(decompose_result) for decompose_result in decompose_results],
        ],
        summary=summary,
        product_status=product_status,
        issues=validation_issues,
        provenance=provenance,
        result_payload=result_payload,
        product_metadata_payload=product_metadata_payload,
        reasoning=reasoning,
    )


# ── Request shape validation ──────────────────────────────────────────────────


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("attribute: INVALID_ARGUMENT - params must be an attribute request object")

    missing_fields = {"metric", "current", "baseline", "dimensions"} - set(value)
    if missing_fields:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - missing required field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(value) - (_REQUEST_FIELDS | _OPTIONAL_REQUEST_FIELDS)
    if unexpected_fields:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - unsupported field(s): {sorted(unexpected_fields)}"
        )
    return value


def _validate_slice(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"attribute: INVALID_ARGUMENT - {label} must be an object")

    missing_fields = {"time_scope"} - set(value)
    if missing_fields:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - missing {label} field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(value) - _SLICE_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - unsupported {label} field(s): "
            f"{sorted(unexpected_fields)}"
        )
    if "filter" in value and value["filter"] is None:
        raise ValueError(f"attribute: INVALID_ARGUMENT - {label}.filter must not be null")
    return value


def _validate_time_scope(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"attribute: INVALID_ARGUMENT - {label} must be an object")

    missing_fields = _TIME_SCOPE_FIELDS - set(value)
    if missing_fields:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - missing {label} field(s): {sorted(missing_fields)}"
        )
    unexpected_fields = set(value) - _TIME_SCOPE_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - unsupported {label} field(s): "
            f"{sorted(unexpected_fields)}"
        )
    for field in ("field", "start", "end"):
        raw_value = value[field]
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"attribute: INVALID_ARGUMENT - {label}.{field} is required")
    return value


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


_AGGREGATION_TO_DECOMPOSITION_METHOD: dict[str, str] = {
    "sum": "delta_share",
    "ratio": "ratio_decomposition",
    "weighted_average": "weighted_decomposition",
}


def _aggregation_semantics_to_decomposition_method(aggregation_semantics: str) -> str:
    """Map a metric's aggregation_semantics to its decomposition method."""
    method = _AGGREGATION_TO_DECOMPOSITION_METHOD.get(aggregation_semantics)
    if method is None:
        raise ValueError(
            f"attribute: INVALID_ARGUMENT - unsupported aggregation_semantics "
            f"'{aggregation_semantics}', expected one of "
            f"{sorted(_AGGREGATION_TO_DECOMPOSITION_METHOD)}"
        )
    return method
