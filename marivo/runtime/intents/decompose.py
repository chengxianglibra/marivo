from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from marivo.contracts.errors import ExecutionError
from marivo.core.intent.primitives import new_step_id
from marivo.core.semantic.additivity import derive_additivity_capabilities
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_FLAT_TOLERANCE_RELATIVE = 0.01


def run_decompose_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `decompose` intent: attribute a compare delta across a dimension.

    Input:
      compare_ref: ArtifactRef pointing to a committed scalar_delta or time_series_delta artifact
      dimension:   single semantic dimension to decompose over
      method:      "delta_share" (only v1 option)

    Output: committed delta_decomposition artifact.

    Empty semantics: fails (NOT_ATTRIBUTABLE) if no contribution rows can be formed.
    """
    p = params or {}

    compare_ref_raw: dict[str, Any] = p.get("compare_ref") or {}
    compare_artifact_id_raw = p.get("compare_artifact_id")
    compare_artifact_id = (
        compare_artifact_id_raw.strip() if isinstance(compare_artifact_id_raw, str) else ""
    )
    uses_aoi_artifact_ref = bool(compare_artifact_id) or "compare_ref" not in p
    compare_step_id: str = "" if uses_aoi_artifact_ref else compare_ref_raw.get("step_id") or ""
    compare_session_id: str = (
        session_id if uses_aoi_artifact_ref else compare_ref_raw.get("session_id") or session_id
    )
    dimension: str = (p.get("dimension") or "").strip()
    method: str = p.get("method") or "delta_share"

    # ── Input validation ──────────────────────────────────────────────────────
    if uses_aoi_artifact_ref:
        if not compare_artifact_id:
            raise ValueError("decompose: INVALID_ARGUMENT - compare_artifact_id is required")
    elif not compare_step_id:
        raise ValueError("decompose: compare_ref.step_id is required")

    if not uses_aoi_artifact_ref:
        ref_step_type = compare_ref_raw.get("step_type")
        if ref_step_type is not None and ref_step_type != "compare":
            raise ValueError(
                f"decompose: INVALID_ARGUMENT - compare_ref.step_type must be 'compare', "
                f"got '{ref_step_type}'"
            )

    if not uses_aoi_artifact_ref and compare_session_id != session_id:
        raise ValueError(
            "decompose: Cross-session ref not allowed - compare_ref.session_id must match "
            "the current session"
        )

    if not dimension:
        raise ValueError("decompose: INVALID_ARGUMENT - dimension is required")

    if method != "delta_share":
        raise ValueError(
            f"decompose: UNSUPPORTED_METHOD - only 'delta_share' is supported in v1, got '{method}'"
        )

    # ── Resolve compare artifact ──────────────────────────────────────────────
    if uses_aoi_artifact_ref:
        compare_artifact = runtime.resolve_artifact_by_id(session_id, compare_artifact_id)
        if compare_artifact is None:
            raise ValueError(
                "decompose: ARTIFACT_NOT_FOUND - no committed artifact for "
                f"compare_artifact_id '{compare_artifact_id}'"
            )
    else:
        compare_artifact = runtime.resolve_artifact_for_ref(compare_session_id, compare_step_id)
        if compare_artifact is None:
            raise ValueError(
                f"decompose: STEP_NOT_FOUND - no committed artifact for step '{compare_step_id}'"
            )
        compare_artifact_id = (
            runtime.resolve_artifact_id_for_step(session_id, compare_step_id) or ""
        )

    normalized_compare = _normalize_decompose_compare_input(compare_artifact)
    comparison_type: str = normalized_compare["comparison_type"]

    # ── Extract metadata from compare artifact ────────────────────────────────
    metric_name: str = normalized_compare["metric_name"]
    unit: str | None = normalized_compare["unit"]
    scope_left_value: float | None = normalized_compare["scope_left_value"]
    scope_right_value: float | None = normalized_compare["scope_right_value"]
    scope_absolute_delta: float | None = normalized_compare["scope_absolute_delta"]
    scope_relative_delta: float | None = normalized_compare["scope_relative_delta"]
    scope_direction: str = normalized_compare["scope_direction"]
    source_observation_type: str = normalized_compare["source_observation_type"]
    source_analytical_metadata: dict[str, Any] = normalized_compare["analytical_metadata"]
    frozen_additive_dimensions: list[str] | None = normalized_compare.get(
        "frozen_additive_dimensions"
    )

    lineage_info: dict[str, Any] = compare_artifact.get("lineage") or {}
    left_source_ref: dict[str, Any] = lineage_info.get("left_source_ref") or {}
    right_source_ref: dict[str, Any] = lineage_info.get("right_source_ref") or {}
    left_obs_step_id: str = left_source_ref.get("step_id") or ""
    right_obs_step_id: str = right_source_ref.get("step_id") or ""

    if not left_obs_step_id or not right_obs_step_id:
        raise ValueError(
            "decompose: STEP_NOT_FOUND - compare artifact lineage is missing upstream "
            "observe step IDs; cannot form canonical observation refs"
        )

    resolved_input: dict[str, Any] = compare_artifact.get("resolved_input_summary") or {}
    left_time_scope: dict[str, Any] = normalized_compare["left_time_scope"]
    right_time_scope: dict[str, Any] = normalized_compare["right_time_scope"]
    left_scope: dict[str, Any] = resolved_input.get("left_scope") or {}
    right_scope: dict[str, Any] = resolved_input.get("right_scope") or {}

    # ── Validate metric and dimension ────────────────────────────────────────
    # Use frozen additive_dimensions from compare artifact lineage for idempotent retries.
    # Fallback to current metric state for older artifacts without frozen metadata.
    dims_for_gate: list[str]
    if frozen_additive_dimensions is not None:
        dims_for_gate = frozen_additive_dimensions
        gate_source = "compare_artifact_lineage"
    else:
        resolved_metric = runtime.resolve_metric(metric_name)
        if resolved_metric is None:
            raise ValueError(f"decompose: metric '{metric_name}' not found or not published")
        _metric_header = resolved_metric.semantic_object.get("header") or {}
        # Backward compat: old artifacts may have additivity_constraints dict
        dims_for_gate = _metric_header.get("additive_dimensions", [])
        if not dims_for_gate:
            legacy_constraints = _metric_header.get("additivity_constraints")
            if isinstance(legacy_constraints, dict):
                dims_for_gate = legacy_constraints.get("additive_dimensions") or []
        gate_source = "current_metric_state"

    # Resolve metric for sample_kind needed by capability derivation
    resolved_metric = runtime.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"decompose: metric '{metric_name}' not found or not published")

    _resolved_header = resolved_metric.semantic_object.get("header") or {}
    additivity_caps = derive_additivity_capabilities(
        additive_dimensions=dims_for_gate,
        sample_kind=_resolved_header.get("sample_kind") or "numeric",
    )

    # Derive time_rollup_allowed from request-level time_scope.field
    _time_scope_field = str(left_time_scope.get("field") or "").strip() if left_time_scope else None
    time_rollup_allowed = _time_scope_field in dims_for_gate if _time_scope_field else False
    if not additivity_caps.supports_decompose:
        raise ExecutionError(
            code="ADDITIVITY_CONSTRAINT",
            category="compatibility",
            message=(
                f"decompose: ADDITIVITY_CONSTRAINT - metric '{metric_name}' does not support "
                f"decomposition (additive_dimensions={additivity_caps.additive_dimensions}, "
                f"blocker='{additivity_caps.blocker}', "
                f"gate_source='{gate_source}')"
                + (
                    f"; {additivity_caps.remediation_hint}"
                    if additivity_caps.remediation_hint
                    else ""
                )
            ),
            detail={
                "compatibility_error": {
                    "code": "ADDITIVITY_CONSTRAINT",
                    "metric": metric_name,
                    "additive_dimensions": additivity_caps.additive_dimensions,
                    "time_rollup_allowed": time_rollup_allowed,
                    "blocker": additivity_caps.blocker,
                    "gate_source": gate_source,
                    "remediation_hint": additivity_caps.remediation_hint,
                },
            },
        )

    if (
        len(additivity_caps.additive_dimensions) > 0
        and dimension not in additivity_caps.additive_dimensions
    ):
        disallowed = [dimension]
        raise ExecutionError(
            code="ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED",
            category="compatibility",
            message=(
                f"decompose: ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED - metric "
                f"'{metric_name}' does not allow "
                f"decomposition on '{dimension}'. "
                f"Allowed: {sorted(additivity_caps.additive_dimensions)}, "
                f"Disallowed: {disallowed}"
                + (
                    f"; {additivity_caps.remediation_hint}"
                    if additivity_caps.remediation_hint
                    else ""
                )
            ),
            detail={
                "compatibility_error": {
                    "code": "ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED",
                    "metric": metric_name,
                    "additive_dimensions": sorted(additivity_caps.additive_dimensions),
                    "disallowed_dimensions": disallowed,
                    "requested_dimensions": [dimension],
                    "time_rollup_allowed": time_rollup_allowed,
                    "remediation_hint": additivity_caps.remediation_hint,
                },
            },
        )

    # Resolve metric for dimension validation (dimensions are runtime state, not frozen)
    # resolved_metric already loaded above; re-resolve for fresh dimension state
    resolved_metric = runtime.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"decompose: metric '{metric_name}' not found or not published")

    runtime_dimensions = runtime.resolve_metric_dimensions(metric_name) or []
    valid_dimensions = (
        runtime_dimensions or resolved_metric.allowed_dimensions or list(resolved_metric.dimensions)
    )
    if not valid_dimensions:
        raise ValueError(f"decompose: metric '{metric_name}' declares no dimensions")
    if dimension not in valid_dimensions:
        raise ValueError(
            f"decompose: UNSUPPORTED_DIMENSION - '{dimension}' is not declared for "
            f"metric '{metric_name}'. Available: {sorted(valid_dimensions)}"
        )

    all_dimensions = list(runtime_dimensions or resolved_metric.dimensions)
    compare_grain = _infer_compare_grain(
        left_time_scope=left_time_scope,
        right_time_scope=right_time_scope,
        fallback_grain=resolved_metric.grain,
    )

    table = runtime.resolve_metric_table(metric_name, session_id=session_id)
    if table is None:
        raise ValueError(f"decompose: metric '{metric_name}' has no source table mapping")

    # ── Engine resolution ─────────────────────────────────────────────────────
    engine_resolution = runtime.resolve_engine_for_session(session_id, [table])
    if not isinstance(engine_resolution, tuple) or len(engine_resolution) != 3:
        engine_resolution = runtime.resolve_engine([table])
    engine, engine_type, qualified = engine_resolution
    metric_sql = runtime.resolve_metric_sql_for_execution(metric_name, engine_type=engine_type)
    qualified_table = qualified.get(table, table)

    # ── Fetch artifact IDs for canonical refs ─────────────────────────────────
    left_obs_artifact_id: str | None = runtime.resolve_artifact_id_for_step(
        session_id, left_obs_step_id
    )
    right_obs_artifact_id: str | None = runtime.resolve_artifact_id_for_step(
        session_id, right_obs_step_id
    )

    # ── Execute segmented queries for left and right scopes ───────────────────
    left_rows, left_query_hash = _run_segmented_query(
        runtime,
        session_id,
        metric_name,
        metric_sql,
        qualified_table,
        dimension,
        all_dimensions,
        left_time_scope,
        left_scope,
        engine,
        engine_type,
        compare_grain,
    )
    right_rows, _ = _run_segmented_query(
        runtime,
        session_id,
        metric_name,
        metric_sql,
        qualified_table,
        dimension,
        all_dimensions,
        right_time_scope,
        right_scope,
        engine,
        engine_type,
        compare_grain,
    )

    now = datetime.now(UTC).isoformat()
    execution_metadata: dict[str, Any] = {
        "query_hash": left_query_hash,
        "engine": engine_type,
        "executed_at": now,
    }

    # ── Build DeltaDecompositionRow list ──────────────────────────────────────
    left_map: dict[Any, float | None] = {
        row.get(dimension): _safe_float(row.get("current_value")) for row in left_rows
    }
    right_map: dict[Any, float | None] = {
        row.get(dimension): _safe_float(row.get("current_value")) for row in right_rows
    }
    all_keys: set[Any] = set(left_map) | set(right_map)

    rows: list[dict[str, Any]] = []
    for key in sorted(all_keys, key=lambda k: "" if k is None else str(k)):
        in_left = key in left_map
        in_right = key in right_map
        lv: float | None = left_map.get(key) if in_left else None
        rv: float | None = right_map.get(key) if in_right else None

        if in_left and in_right:
            presence = "both"
            abs_contribution = _delta(lv, rv)
        elif in_left:
            presence = "left_only"
            rv = None
            # absolute_contribution = left_value (right treated as 0)
            abs_contribution = lv
        else:
            presence = "right_only"
            lv = None
            # absolute_contribution = -right_value (right side disappeared)
            abs_contribution = (-rv) if rv is not None else None

        contribution_share = _signed_share(abs_contribution, scope_absolute_delta)
        direction = _compute_direction(abs_contribution)

        rows.append(
            {
                "key": key,
                "left_value": lv,
                "right_value": rv,
                "absolute_contribution": abs_contribution,
                "contribution_share": contribution_share,
                "direction": direction,
                "presence": presence,
            }
        )

    # ── Empty semantics: fail if no contribution rows ─────────────────────────
    if not rows:
        raise ValueError(
            "decompose: NOT_ATTRIBUTABLE - no canonical contribution rows could be formed "
            "for the requested dimension"
        )

    # Sort: abs(contribution_share) desc, then abs(absolute_contribution) desc, then key
    rows.sort(
        key=lambda r: (
            -(abs(r["contribution_share"]) if r["contribution_share"] is not None else 0.0),
            -(abs(r["absolute_contribution"]) if r["absolute_contribution"] is not None else 0.0),
            "" if r["key"] is None else str(r["key"]),
        )
    )

    # ── Unexplained delta ─────────────────────────────────────────────────────
    explained = sum(
        r["absolute_contribution"] for r in rows if r["absolute_contribution"] is not None
    )
    if scope_absolute_delta is not None:
        unexplained_absolute_delta = scope_absolute_delta - explained
        unexplained_share: float | None = (
            unexplained_absolute_delta / scope_absolute_delta if scope_absolute_delta != 0 else None
        )
        # Treat rounding-level residuals as zero
        if unexplained_absolute_delta is not None and abs(unexplained_absolute_delta) < 1e-9:
            unexplained_absolute_delta = 0.0
            unexplained_share = 0.0
    else:
        unexplained_absolute_delta = None
        unexplained_share = None

    # ── Attribution status & reconciliation ──────────────────────────────────
    issues: list[dict[str, Any]] = []
    if scope_absolute_delta is None:
        issues.append(
            {
                "code": "data_incomplete",
                "severity": "warning",
                "message": "scope_absolute_delta is null; contribution_share cannot be computed",
            }
        )

    # Reconciliation check (reconciliation_expected = True)
    if (
        unexplained_absolute_delta is not None
        and unexplained_absolute_delta != 0.0
        and scope_absolute_delta is not None
        and scope_absolute_delta != 0
    ):
        relative_unexplained = abs(unexplained_absolute_delta / scope_absolute_delta)
        if relative_unexplained > _FLAT_TOLERANCE_RELATIVE:
            unexplained_reason: str | None = "scope_recomputation_failed"
            issues.append(
                {
                    "code": "attribution_not_reconcilable",
                    "severity": "error",
                    "message": (
                        f"Explained sum diverges from scope_absolute_delta by "
                        f"{relative_unexplained:.1%}; recomputed scope may differ from "
                        f"upstream compare due to grain or filter differences."
                    ),
                }
            )
        else:
            unexplained_reason = "rounding"
    else:
        unexplained_reason = None

    attribution_status = (
        "needs_attention" if any(i["severity"] == "error" for i in issues) else "attributable"
    )

    # ── Build artifact ────────────────────────────────────────────────────────
    step_id = new_step_id()

    left_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_obs_step_id,
        "artifact_id": left_obs_artifact_id,
        "observation_type": source_observation_type,
    }
    right_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": right_obs_step_id,
        "artifact_id": right_obs_artifact_id,
        "observation_type": source_observation_type,
    }
    compare_ref_out: dict[str, Any] = {
        "step_type": "compare",
        "session_id": session_id,
        "step_id": compare_step_id,
        "artifact_id": compare_artifact_id,
        "comparison_type": comparison_type,
    }

    artifact: dict[str, Any] = {
        "decomposition_type": "delta_decomposition",
        "metric": metric_name,
        "compare_ref": compare_ref_out,
        "left_ref": left_ref_out,
        "right_ref": right_ref_out,
        "dimension": dimension,
        "method": "delta_share",
        "unit": unit,
        "left_time_scope": left_time_scope,
        "right_time_scope": right_time_scope,
        "resolved_scopes": {
            "left": left_scope,
            "right": right_scope,
        },
        "scope_left_value": scope_left_value,
        "scope_right_value": scope_right_value,
        "scope_absolute_delta": scope_absolute_delta,
        "scope_relative_delta": scope_relative_delta,
        "scope_direction": scope_direction,
        "attribution": {"status": attribution_status, "issues": issues},
        "rows": rows,
        "unexplained_absolute_delta": unexplained_absolute_delta,
        "unexplained_share": unexplained_share,
        "unexplained_reason": unexplained_reason,
        "analytical_metadata": {
            "method": "delta_share",
            "aggregation_semantics": "sum",
            "additive_dimensions": dims_for_gate,
            "additive_dimensions_source": gate_source,
            "time_rollup_allowed": time_rollup_allowed,
            "reconciliation_expected": True,
            "flat_tolerance_relative": _FLAT_TOLERANCE_RELATIVE,
            "left_row_count": len(left_rows),
            "right_row_count": len(right_rows),
            **source_analytical_metadata,
            "time_boundary_constraint": {
                "scope": "frozen_compare_window",
                "time_rollup_implied": False,
            },
        },
        "version_metadata": {
            "artifact_schema_version": "1.0",
            "source_contract_version": "1.0",
            "derivation_version": "1.0",
        },
        "source_lineage": {
            "compare_artifact": compare_ref_out,
            "left_artifact": left_ref_out,
            "right_artifact": right_ref_out,
        },
        "execution_metadata": execution_metadata,
    }

    artifact_name = f"{metric_name}_decompose_{dimension}"
    summary = (
        f"decompose {metric_name} by {dimension}: "
        f"{len(rows)} contribution rows "
        f"(scope Δ {scope_absolute_delta if scope_absolute_delta is not None else 'n/a'})"
    )

    provenance: dict[str, Any] = {
        "compare_step_id": compare_step_id,
        "dimension": dimension,
        "method": method,
    }
    result = commit_step_result(
        runtime,
        session_id,
        step_id,
        "decompose",
        "delta_decomposition",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_decompose_compare_input(compare_artifact: dict[str, Any]) -> dict[str, Any]:
    comparison_type = compare_artifact.get("comparison_type")
    # Extract frozen additive_dimensions from compare artifact's analytical_metadata
    compare_am: dict[str, Any] = compare_artifact.get("analytical_metadata") or {}
    frozen_additive_dimensions: list[str] | None = compare_am.get("additive_dimensions")
    # Backward compat: old artifacts may store additivity_constraints dict
    if frozen_additive_dimensions is None and "additivity_constraints" in compare_am:
        legacy = compare_am["additivity_constraints"]
        if isinstance(legacy, dict):
            if legacy.get("dimension_policy") == "none":
                frozen_additive_dimensions = []
            else:
                frozen_additive_dimensions = legacy.get("additive_dimensions")

    if comparison_type == "scalar_delta":
        resolved_input: dict[str, Any] = compare_artifact.get("resolved_input_summary") or {}
        return {
            "comparison_type": "scalar_delta",
            "metric_name": compare_artifact.get("metric") or "",
            "unit": compare_artifact.get("unit"),
            "scope_left_value": _safe_float(compare_artifact.get("left_value")),
            "scope_right_value": _safe_float(compare_artifact.get("right_value")),
            "scope_absolute_delta": _safe_float(compare_artifact.get("absolute_delta")),
            "scope_relative_delta": _safe_float(compare_artifact.get("relative_delta")),
            "scope_direction": compare_artifact.get("direction") or "undefined",
            "source_observation_type": "scalar",
            "left_time_scope": dict(resolved_input.get("left_time_scope") or {}),
            "right_time_scope": dict(resolved_input.get("right_time_scope") or {}),
            "analytical_metadata": {"decomposition_source": "scalar_delta"},
            "frozen_additive_dimensions": frozen_additive_dimensions,
        }

    if comparison_type == "time_series_delta":
        resolved_input = compare_artifact.get("resolved_input_summary") or {}
        analytical = compare_artifact.get("analytical_metadata") or {}
        left_time_scope = dict(resolved_input.get("left_time_scope") or {})
        right_time_scope = dict(resolved_input.get("right_time_scope") or {})
        matched_left_time_scope = analytical.get("matched_left_time_scope")
        matched_right_time_scope = analytical.get("matched_right_time_scope")
        matched_time_scope = analytical.get("matched_time_scope")
        if isinstance(matched_left_time_scope, dict) and matched_left_time_scope:
            left_time_scope = dict(matched_left_time_scope)
        elif isinstance(matched_time_scope, dict) and matched_time_scope:
            left_time_scope = dict(matched_time_scope)
        if isinstance(matched_right_time_scope, dict) and matched_right_time_scope:
            right_time_scope = dict(matched_right_time_scope)
        elif isinstance(matched_time_scope, dict) and matched_time_scope:
            right_time_scope = dict(matched_time_scope)

        return {
            "comparison_type": "time_series_delta",
            "metric_name": compare_artifact.get("metric") or "",
            "unit": compare_artifact.get("unit"),
            "scope_left_value": _safe_float(compare_artifact.get("summary_left_value")),
            "scope_right_value": _safe_float(compare_artifact.get("summary_right_value")),
            "scope_absolute_delta": _safe_float(compare_artifact.get("summary_absolute_delta")),
            "scope_relative_delta": _safe_float(compare_artifact.get("summary_relative_delta")),
            "scope_direction": compare_artifact.get("summary_direction") or "undefined",
            "source_observation_type": "time_series",
            "left_time_scope": left_time_scope,
            "right_time_scope": right_time_scope,
            "analytical_metadata": {
                "decomposition_source": "time_series_summary_delta",
                "source_granularity": compare_artifact.get("granularity"),
                "source_matched_bucket_count": analytical.get("matched_bucket_count"),
                "source_dropped_left_buckets": analytical.get("dropped_left_buckets"),
                "source_dropped_right_buckets": analytical.get("dropped_right_buckets"),
                "source_pairing_basis": analytical.get("pairing_basis"),
                "source_pairing_rule": analytical.get("pairing_rule"),
            },
            "frozen_additive_dimensions": frozen_additive_dimensions,
        }

    raise ValueError(
        "decompose: INVALID_ARGUMENT - compare_ref must point to a scalar_delta or "
        f"time_series_delta artifact, got '{comparison_type}'"
    )


def _run_segmented_query(
    runtime: MarivoRuntime,
    session_id: str,
    metric_name: str,
    metric_sql: str,
    qualified_table: str,
    dimension: str,
    all_dimensions: list[str],
    time_scope: dict[str, Any],
    scope: dict[str, Any],
    engine: Any,
    engine_type: str,
    grain: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Run a single segmented metric query for one time scope.

    Returns (rows, query_hash) where query_hash is an MD5 of the translated SQL.
    """
    start_str, end_str = _extract_date_range(time_scope)

    mq_params: dict[str, Any] = {
        "table": qualified_table,
        "metric": metric_name,
        "time_scope": {
            "mode": "single_window",
            "grain": grain,
            "current": {"start": start_str, "end": end_str},
        },
        "dimensions": [dimension],
    }
    if scope:
        mq_params["scope"] = scope

    resolved = normalize_metric_query_request(mq_params)
    runtime.resolve_windowed_query_time_axis(
        resolved,
        engine_type=engine_type,
        metric_name=metric_name,
        fallback_columns=all_dimensions,
    )
    scoped_query = runtime.build_scoped_query(session_id, resolved, engine_type=engine_type)
    compiled_query = runtime.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="metric_query",
            params={
                "table": qualified_table,
                "metric": metric_name,
                "scoped_query": scoped_query,
            },
        ),
        engine_type=engine_type,
        semantic_context={"metric_sql": metric_sql, "dimensions": [dimension]},
    )
    result = execute_compiled(engine, compiled_query, session_id=session_id)
    sql = result.metadata.get("translated_sql") or ""
    query_hash: str | None = hashlib.md5(sql.encode()).hexdigest() if sql else None
    return list(result.rows), query_hash


def _infer_compare_grain(
    *,
    left_time_scope: dict[str, Any],
    right_time_scope: dict[str, Any],
    fallback_grain: str | None,
) -> str:
    """Infer the effective compare grain from upstream observation windows.

    Decompose must preserve the actual compare/observe window semantics instead of
    blindly falling back to the metric contract grain. This avoids collapsing
    hour-level ranges like 2026-04-09T14:00:00..15:00:00 into a day-level empty
    window during follow-up metric_query normalization.
    """

    explicit_grain = _time_scope_grain(left_time_scope) or _time_scope_grain(right_time_scope)
    if explicit_grain is not None:
        return explicit_grain

    if _time_scope_has_datetime_boundary(left_time_scope) or _time_scope_has_datetime_boundary(
        right_time_scope
    ):
        return "hour"

    if fallback_grain in {"hour", "day", "week", "month"}:
        return fallback_grain

    return "day"


def _time_scope_grain(time_scope: dict[str, Any]) -> str | None:
    grain = time_scope.get("grain")
    if grain in {"hour", "day", "week", "month"}:
        return str(grain)
    return None


def _time_scope_has_datetime_boundary(time_scope: dict[str, Any]) -> bool:
    for key in ("start", "end", "at"):
        value = time_scope.get(key)
        if isinstance(value, str) and ("T" in value or " " in value):
            return True
    return False


def _extract_date_range(time_scope: dict[str, Any]) -> tuple[str, str]:
    """Extract (start_str, end_str) from any resolved time_scope dict."""
    kind = time_scope.get("kind")
    if kind == "range":
        return time_scope["start"], time_scope["end"]
    if kind == "snapshot_now":
        d = time_scope.get("observed_at") or datetime.now(UTC).date().isoformat()
        return d, _next_day(d)
    if kind == "latest_available":
        d = time_scope.get("data_as_of") or datetime.now(UTC).date().isoformat()
        return d, _next_day(d)
    if kind == "as_of":
        d = time_scope.get("at") or datetime.now(UTC).date().isoformat()
        d = d[:10]  # trim to date if datetime string
        return d, _next_day(d)
    # Fallback: treat as range if keys present
    if "start" in time_scope and "end" in time_scope:
        return time_scope["start"], time_scope["end"]
    raise ValueError(f"decompose: cannot extract date range from time_scope with kind='{kind}'")


def _next_day(date_str: str) -> str:
    return (date.fromisoformat(date_str[:10]) + timedelta(days=1)).isoformat()


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    with contextlib.suppress(TypeError, ValueError):
        return float(v)
    return None


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _signed_share(
    absolute_contribution: float | None, scope_absolute_delta: float | None
) -> float | None:
    """Signed contribution share = absolute_contribution / scope_absolute_delta."""
    if absolute_contribution is None or scope_absolute_delta is None or scope_absolute_delta == 0:
        return None
    return absolute_contribution / scope_absolute_delta


def _compute_direction(absolute_contribution: float | None) -> str:
    if absolute_contribution is None:
        return "undefined"
    if absolute_contribution == 0:
        return "flat"
    return "increase" if absolute_contribution > 0 else "decrease"
