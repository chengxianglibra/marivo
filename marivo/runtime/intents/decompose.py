from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.contracts.errors import ExecutionError
from marivo.core.intent.primitives import new_step_id
from marivo.core.semantic.additivity import (
    additive_dimension_allows,
    additive_time_rollup_allowed,
    derive_additivity_capabilities,
)
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.runtime.intents._helpers import commit_step_result
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_scope import normalize_metric_query_request

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_FLAT_TOLERANCE_RELATIVE = 0.01
_AOI_PARAM_KEYS: frozenset[str] = frozenset({"compare_artifact_id", "dimension", "limit"})


def run_decompose_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute a `decompose` intent: attribute a compare delta across a dimension.

    Input:
      compare_artifact_id: committed scalar_delta or time_series_delta compare artifact id
      dimension:           single semantic dimension to decompose over
      limit:               optional max returned contribution rows

    Output: committed delta_decomposition artifact.

    Empty semantics: fails (NOT_ATTRIBUTABLE) if no contribution rows can be formed.
    """
    p = params or {}
    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; decompose accepts only AOI request fields"
        )

    compare_artifact_id_raw = p.get("compare_artifact_id")
    compare_artifact_id = (
        compare_artifact_id_raw.strip() if isinstance(compare_artifact_id_raw, str) else ""
    )
    compare_step_id: str = ""
    dimension: str = (p.get("dimension") or "").strip()
    limit_raw = p.get("limit")
    limit: int | None = None
    if limit_raw is not None:
        limit = int(limit_raw)
        if limit <= 0:
            raise ValueError("decompose: INVALID_ARGUMENT - limit must be > 0")

    # ── Input validation ──────────────────────────────────────────────────────
    if not compare_artifact_id:
        raise ValueError("decompose: INVALID_ARGUMENT - compare_artifact_id is required")

    if not dimension:
        raise ValueError("decompose: INVALID_ARGUMENT - dimension is required")

    # ── Resolve compare artifact ──────────────────────────────────────────────
    compare_artifact = runtime.resolve_artifact_by_id(session_id, compare_artifact_id)
    if compare_artifact is None:
        raise ValueError(
            "decompose: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"compare_artifact_id '{compare_artifact_id}'"
        )

    normalized_compare = _normalize_decompose_compare_input(compare_artifact)
    comparison_type: str = normalized_compare["comparison_type"]

    # ── Extract metadata from compare artifact ────────────────────────────────
    metric_name: str = normalized_compare["metric_name"]
    unit: str | None = normalized_compare["unit"]
    scope_current_value: float | None = normalized_compare["scope_current_value"]
    scope_baseline_value: float | None = normalized_compare["scope_baseline_value"]
    scope_absolute_delta: float | None = normalized_compare["scope_absolute_delta"]
    scope_relative_delta: float | None = normalized_compare["scope_relative_delta"]
    scope_direction: str = normalized_compare["scope_direction"]
    source_observation_type: str = normalized_compare["source_observation_type"]
    source_analytical_metadata: dict[str, Any] = normalized_compare["analytical_metadata"]
    frozen_additive_dimensions: list[str] | None = normalized_compare.get(
        "frozen_additive_dimensions"
    )

    lineage_info: dict[str, Any] = compare_artifact.get("lineage") or {}
    current_source_ref: dict[str, Any] = lineage_info.get("current_source_ref") or {}
    baseline_source_ref: dict[str, Any] = lineage_info.get("baseline_source_ref") or {}
    left_obs_step_id: str = current_source_ref.get("step_id") or ""
    right_obs_step_id: str = baseline_source_ref.get("step_id") or ""

    if not left_obs_step_id or not right_obs_step_id:
        raise ValueError(
            "decompose: STEP_NOT_FOUND - compare artifact lineage is missing upstream "
            "observe step IDs; cannot form canonical observation refs"
        )

    resolved_input: dict[str, Any] = compare_artifact.get("resolved_input_summary") or {}
    current_time_scope: dict[str, Any] = normalized_compare["current_time_scope"]
    baseline_time_scope: dict[str, Any] = normalized_compare["baseline_time_scope"]
    current_scope: dict[str, Any] = resolved_input.get("current_scope") or {}
    baseline_scope: dict[str, Any] = resolved_input.get("baseline_scope") or {}

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
        dims_for_gate = _metric_header.get("additive_dimensions") or []
        gate_source = "current_metric_state"

    # Resolve metric for aggregation_semantics needed by capability derivation
    resolved_metric = runtime.resolve_metric(metric_name)
    if resolved_metric is None:
        raise ValueError(f"decompose: metric '{metric_name}' not found or not published")

    _resolved_header = resolved_metric.semantic_object.get("header") or {}
    additivity_caps = derive_additivity_capabilities(
        additive_dimensions=dims_for_gate,
    )
    metric_aggregation_semantics = _resolved_header.get("aggregation_semantics") or "sum"

    # Derive time_rollup_allowed from request-level time_scope.field
    _time_scope_field = (
        str(current_time_scope.get("field") or "").strip() if current_time_scope else None
    )
    time_rollup_allowed = additive_time_rollup_allowed(dims_for_gate, _time_scope_field)
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

    if len(additivity_caps.additive_dimensions) > 0 and not additive_dimension_allows(
        additivity_caps.additive_dimensions, dimension
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
    left_rows, left_sql, left_query_hash, left_elapsed_ms = _run_segmented_query(
        runtime,
        session_id,
        metric_name,
        metric_sql,
        qualified_table,
        dimension,
        all_dimensions,
        current_time_scope,
        current_scope,
        engine,
        engine_type,
        table_name=table,
    )
    right_rows, right_sql, _, right_elapsed_ms = _run_segmented_query(
        runtime,
        session_id,
        metric_name,
        metric_sql,
        qualified_table,
        dimension,
        all_dimensions,
        baseline_time_scope,
        baseline_scope,
        engine,
        engine_type,
        table_name=table,
    )

    now = datetime.now(UTC).isoformat()
    execution_metadata: dict[str, Any] = {
        "query_hash": left_query_hash,
        "engine": engine_type,
        "executed_at": now,
    }

    _sql_texts: list[dict[str, str | float]] = []
    if left_elapsed_ms is not None:
        _sql_texts.append(
            {
                "sql": left_sql or "(current segmented query)",
                "engine_type": engine_type,
                "label": "current_query",
                "elapsed_ms": left_elapsed_ms,
            }
        )
    if right_elapsed_ms is not None:
        _sql_texts.append(
            {
                "sql": right_sql or "(baseline segmented query)",
                "engine_type": engine_type,
                "label": "baseline_query",
                "elapsed_ms": right_elapsed_ms,
            }
        )

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
            presence = "current_only"
            rv = None
            # absolute_contribution = current_value (right treated as 0)
            abs_contribution = lv
        else:
            presence = "baseline_only"
            lv = None
            # absolute_contribution = -baseline_value (right side disappeared)
            abs_contribution = (-rv) if rv is not None else None

        contribution_share = _signed_share(abs_contribution, scope_absolute_delta)
        direction = _compute_direction(abs_contribution)

        rows.append(
            {
                "key": key,
                "current_value": lv,
                "baseline_value": rv,
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
    returned_rows = rows[:limit] if limit is not None else rows

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

    # Reconciliation check: only for additive (sum) metrics
    reconciliation_expected = metric_aggregation_semantics == "sum" and len(dims_for_gate) > 0
    if (
        reconciliation_expected
        and unexplained_absolute_delta is not None
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
    elif not reconciliation_expected and unexplained_absolute_delta is not None:
        unexplained_reason = "non_additive_aggregation"
    else:
        unexplained_reason = None

    attribution_status = (
        "needs_attention" if any(i["severity"] == "error" for i in issues) else "attributable"
    )

    # ── Build artifact ────────────────────────────────────────────────────────
    step_id = new_step_id()

    current_ref_out: dict[str, Any] = {
        "step_type": "observe",
        "session_id": session_id,
        "step_id": left_obs_step_id,
        "artifact_id": left_obs_artifact_id,
        "observation_type": source_observation_type,
    }
    baseline_ref_out: dict[str, Any] = {
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
        "current_ref": current_ref_out,
        "baseline_ref": baseline_ref_out,
        "dimension": dimension,
        "method": "delta_share",
        "unit": unit,
        "current_time_scope": current_time_scope,
        "baseline_time_scope": baseline_time_scope,
        "resolved_scopes": {
            "current": current_scope,
            "baseline": baseline_scope,
        },
        "scope_current_value": scope_current_value,
        "scope_baseline_value": scope_baseline_value,
        "scope_absolute_delta": scope_absolute_delta,
        "scope_relative_delta": scope_relative_delta,
        "scope_direction": scope_direction,
        "attribution": {"status": attribution_status, "issues": issues},
        "rows": returned_rows,
        "unexplained_absolute_delta": unexplained_absolute_delta,
        "unexplained_share": unexplained_share,
        "unexplained_reason": unexplained_reason,
        "analytical_metadata": {
            "method": "delta_share",
            "aggregation_semantics": metric_aggregation_semantics,
            "additive_dimensions": dims_for_gate,
            "additive_dimensions_source": gate_source,
            "time_rollup_allowed": time_rollup_allowed,
            "reconciliation_expected": reconciliation_expected,
            "flat_tolerance_relative": _FLAT_TOLERANCE_RELATIVE,
            "current_row_count": len(left_rows),
            "baseline_row_count": len(right_rows),
            "returned_row_count": len(returned_rows),
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
            "current_artifact": current_ref_out,
            "baseline_artifact": baseline_ref_out,
        },
        "execution_metadata": execution_metadata,
    }

    artifact_name = f"{metric_name}_decompose_{dimension}"
    summary = (
        f"decompose {metric_name} by {dimension}: "
        f"{len(returned_rows)} contribution rows "
        f"(scope Δ {scope_absolute_delta if scope_absolute_delta is not None else 'n/a'})"
    )

    provenance: dict[str, Any] = {
        "compare_artifact_id": compare_artifact_id,
        "dimension": dimension,
        "limit": limit,
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
        reasoning=reasoning,
        sql_texts=_sql_texts or None,
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_decompose_compare_input(compare_artifact: dict[str, Any]) -> dict[str, Any]:
    comparison_type = compare_artifact.get("comparison_type")
    # Extract frozen additive_dimensions from compare artifact's analytical_metadata
    compare_am: dict[str, Any] = compare_artifact.get("analytical_metadata") or {}
    frozen_additive_dimensions: list[str] | None = compare_am.get("additive_dimensions")

    if comparison_type == "scalar_delta":
        resolved_input: dict[str, Any] = compare_artifact.get("resolved_input_summary") or {}
        return {
            "comparison_type": "scalar_delta",
            "metric_name": compare_artifact.get("metric") or "",
            "unit": compare_artifact.get("unit"),
            "scope_current_value": _safe_float(compare_artifact.get("current_value")),
            "scope_baseline_value": _safe_float(compare_artifact.get("baseline_value")),
            "scope_absolute_delta": _safe_float(compare_artifact.get("absolute_delta")),
            "scope_relative_delta": _safe_float(compare_artifact.get("relative_delta")),
            "scope_direction": compare_artifact.get("direction") or "undefined",
            "source_observation_type": "scalar",
            "current_time_scope": dict(resolved_input.get("current_time_scope") or {}),
            "baseline_time_scope": dict(resolved_input.get("baseline_time_scope") or {}),
            "analytical_metadata": {"decomposition_source": "scalar_delta"},
            "frozen_additive_dimensions": frozen_additive_dimensions,
        }

    if comparison_type == "time_series_delta":
        resolved_input = compare_artifact.get("resolved_input_summary") or {}
        analytical = compare_artifact.get("analytical_metadata") or {}
        current_time_scope = dict(resolved_input.get("current_time_scope") or {})
        baseline_time_scope = dict(resolved_input.get("baseline_time_scope") or {})
        matched_current_time_scope = analytical.get("matched_current_time_scope")
        matched_baseline_time_scope = analytical.get("matched_baseline_time_scope")
        matched_time_scope = analytical.get("matched_time_scope")
        if isinstance(matched_current_time_scope, dict) and matched_current_time_scope:
            current_time_scope = dict(matched_current_time_scope)
        elif isinstance(matched_time_scope, dict) and matched_time_scope:
            current_time_scope = dict(matched_time_scope)
        if isinstance(matched_baseline_time_scope, dict) and matched_baseline_time_scope:
            baseline_time_scope = dict(matched_baseline_time_scope)
        elif isinstance(matched_time_scope, dict) and matched_time_scope:
            baseline_time_scope = dict(matched_time_scope)

        return {
            "comparison_type": "time_series_delta",
            "metric_name": compare_artifact.get("metric") or "",
            "unit": compare_artifact.get("unit"),
            "scope_current_value": _safe_float(compare_artifact.get("summary_current_value")),
            "scope_baseline_value": _safe_float(compare_artifact.get("summary_baseline_value")),
            "scope_absolute_delta": _safe_float(compare_artifact.get("summary_absolute_delta")),
            "scope_relative_delta": _safe_float(compare_artifact.get("summary_relative_delta")),
            "scope_direction": compare_artifact.get("summary_direction") or "undefined",
            "source_observation_type": "time_series",
            "current_time_scope": current_time_scope,
            "baseline_time_scope": baseline_time_scope,
            "analytical_metadata": {
                "decomposition_source": "time_series_summary_delta",
                "source_granularity": compare_artifact.get("granularity"),
                "source_matched_bucket_count": analytical.get("matched_bucket_count"),
                "source_dropped_current_buckets": analytical.get("dropped_current_buckets"),
                "source_dropped_baseline_buckets": analytical.get("dropped_baseline_buckets"),
                "source_pairing_basis": analytical.get("pairing_basis"),
                "source_pairing_rule": analytical.get("pairing_rule"),
            },
            "frozen_additive_dimensions": frozen_additive_dimensions,
        }

    raise ValueError(
        "decompose: INVALID_ARGUMENT - compare_artifact_id must point to a scalar_delta or "
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
    table_name: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, str | None, float | None]:
    """Run a single segmented metric query for one time scope.

    Returns (rows, sql_text, query_hash, elapsed_ms) where sql_text is the
    translated SQL, query_hash is an MD5 of the translated SQL, and elapsed_ms
    is the query execution duration in milliseconds.
    """
    start_str, end_str = _extract_date_range(time_scope)

    mq_params: dict[str, Any] = {
        "table": table_name or qualified_table,
        "metric": metric_name,
        "time_scope": {
            "mode": "single_window",
            "boundary_mode": "exact",
            "current": {"start": start_str, "end": end_str},
        },
        "dimensions": [dimension],
    }
    if scope:
        mq_params["scope"] = scope
    time_scope_field = str(time_scope.get("field") or "").strip()
    if time_scope_field:
        mq_params["time_scope_field"] = time_scope_field

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
                "time_scope": mq_params["time_scope"],
                **({"time_scope_field": time_scope_field} if time_scope_field else {}),
                "scoped_query": scoped_query,
            },
        ),
        engine_type=engine_type,
        semantic_context={"metric_sql": metric_sql, "dimensions": [dimension]},
    )
    result = execute_compiled(engine, compiled_query, session_id=session_id)
    sql = result.metadata.get("translated_sql") or ""
    query_hash: str | None = hashlib.md5(sql.encode()).hexdigest() if sql else None
    elapsed_ms = result.metadata.get("elapsed_ms")
    return list(result.rows), sql, query_hash, elapsed_ms


def _extract_date_range(time_scope: dict[str, Any]) -> tuple[str, str]:
    """Extract (start_str, end_str) from a canonical time_scope dict."""
    if "start" in time_scope and "end" in time_scope:
        return time_scope["start"], time_scope["end"]
    raise ValueError("decompose: cannot extract date range from time_scope without start/end")


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
