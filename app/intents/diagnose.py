"""Diagnose derived intent runner (Phase 3c-2).

Deterministically expands to:
  detect(metric, time_scope, ..., candidate_limit)
  → for each top-followup_limit candidate:
      observe(current_window) + observe(baseline_window)
      + compare(current_ref, baseline_ref, mode="scalar")
      + decompose(compare_ref, dimension, ...) × len(candidate_dimensions)

Baseline policy: previous_adjacent_equal_length (fixed, not caller-configurable).
Candidate follow-up is capped at followup_limit; untracked candidates are disclosed
via detect_summary.truncated.

Design contract: docs/analysis/intents/derived/diagnose.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from app.core.intent.primitives import new_step_id
from app.intents.compare import run_compare_intent
from app.intents.decompose import run_decompose_intent
from app.intents.detect import run_detect_intent
from app.intents.observe import run_observe_intent
from app.time_contracts import TimeGrain, normalize_hour_boundary, previous_adjacent_window

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime

_DEFAULT_FOLLOWUP_LIMIT = 3
_MAX_FOLLOWUP_LIMIT = 10
_DEFAULT_DECOMPOSITION_LIMIT = 5
_MAX_DECOMPOSITION_LIMIT = 100
_DERIVED_LOGIC_VERSION = "1.0"
_PROJECTION_VERSION = "diagnosis_bundle.v1"
_VALID_PATTERNS = frozenset({"point_anomaly", "period_shift"})


def _normalize_range_time_scope(
    raw: Any,
    *,
    granularity: TimeGrain,
    label: str,
) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"diagnose: INVALID_ARGUMENT - {label} is required")
    if raw.get("kind") != "range":
        raise ValueError(
            f"diagnose: INVALID_ARGUMENT - {label}.kind must be 'range', got '{raw.get('kind')}'"
        )
    start = str(raw.get("start") or "").strip()
    end = str(raw.get("end") or "").strip()
    if not start or not end:
        raise ValueError(f"diagnose: INVALID_ARGUMENT - {label} requires 'start' and 'end'")
    if granularity == "hour":
        start = normalize_hour_boundary(start, label=f"{label}.start")
        end = normalize_hour_boundary(end, label=f"{label}.end")
        if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
            raise ValueError(
                f"diagnose: INVALID_ARGUMENT - {label}.start ('{start}') must be before "
                f"end ('{end}')"
            )
    elif start >= end:
        raise ValueError(
            f"diagnose: INVALID_ARGUMENT - {label}.start ('{start}') must be before end ('{end}')"
        )
    return {"kind": "range", "start": start, "end": end}


def _normalize_granularity(raw: Any) -> TimeGrain:
    granularity = str(raw or "").lower()
    if granularity not in {"hour", "day", "week", "month"}:
        raise ValueError(
            f"diagnose: INVALID_ARGUMENT - granularity must be one of "
            f"'hour', 'day', 'week', 'month', got '{granularity}'"
        )
    return cast("TimeGrain", granularity)


def _normalize_patterns(raw_patterns: Any) -> list[str] | None:
    if raw_patterns is None:
        return None
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise ValueError("diagnose: INVALID_ARGUMENT - patterns must be a non-empty list")
    patterns: list[str] = []
    for raw in raw_patterns:
        pattern = str(raw).strip()
        if pattern not in _VALID_PATTERNS:
            raise ValueError(
                f"diagnose: INVALID_ARGUMENT - pattern '{pattern}' is not valid. "
                f"Must be one of: {sorted(_VALID_PATTERNS)}"
            )
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def run_diagnose_intent(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Execute a `diagnose` derived intent.

    `mode="auto_detect"` expands detect + follow-up over top candidates.
    `mode="explicit_compare"` expands observe(current) + observe(baseline)
    + compare + decompose without running detect.
    """
    p = params or {}

    metric_ref: str = (p.get("metric") or "").strip()
    if not metric_ref:
        raise ValueError("diagnose: INVALID_ARGUMENT - metric is required")
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    mode = str(p.get("mode") or "auto_detect").strip()
    if mode not in {"auto_detect", "explicit_compare"}:
        raise ValueError(
            "diagnose: INVALID_ARGUMENT - mode must be 'auto_detect' or 'explicit_compare'"
        )
    scope: dict[str, Any] | None = p.get("scope") or None
    detect_split_by: str | None = (p.get("detect_split_by") or "").strip() or None
    profile: str = str(p.get("profile") or "auto").lower()
    sensitivity: str = str(p.get("sensitivity") or "balanced").lower()
    patterns = _normalize_patterns(p.get("patterns"))
    baseline_policy = str(p.get("baseline_policy") or "previous_adjacent_equal_length")
    if baseline_policy != "previous_adjacent_equal_length":
        raise ValueError(
            "diagnose: INVALID_ARGUMENT - baseline_policy must be 'previous_adjacent_equal_length'"
        )

    raw_dims: list[Any] = p.get("candidate_dimensions") or []
    if not raw_dims:
        raise ValueError(
            "diagnose: INVALID_ARGUMENT - candidate_dimensions must be a non-empty list"
        )
    dimensions: list[str] = []
    for d in raw_dims:
        d_str = str(d).strip()
        if not d_str:
            raise ValueError(
                "diagnose: INVALID_ARGUMENT - candidate_dimensions must not contain blank strings"
            )
        dimensions.append(d_str)
    seen: set[str] = set()
    deduped_dims: list[str] = []
    for d in dimensions:
        if d not in seen:
            seen.add(d)
            deduped_dims.append(d)
    if not deduped_dims:
        raise ValueError(
            "diagnose: INVALID_ARGUMENT - candidate_dimensions is empty after deduplication"
        )
    dimensions = deduped_dims

    raw_followup = p.get("followup_limit")
    if raw_followup is None:
        followup_limit = _DEFAULT_FOLLOWUP_LIMIT
    else:
        followup_limit = int(raw_followup)
        if followup_limit <= 0:
            raise ValueError(
                f"diagnose: INVALID_ARGUMENT - followup_limit must be > 0, got {followup_limit}"
            )
        if followup_limit > _MAX_FOLLOWUP_LIMIT:
            raise ValueError(
                f"diagnose: INVALID_ARGUMENT - followup_limit exceeds max allowed "
                f"({_MAX_FOLLOWUP_LIMIT}), got {followup_limit}"
            )

    raw_decomp_limit = p.get("decomposition_limit")
    if raw_decomp_limit is None:
        decomposition_limit = _DEFAULT_DECOMPOSITION_LIMIT
    else:
        decomposition_limit = int(raw_decomp_limit)
        if decomposition_limit <= 0:
            raise ValueError(
                f"diagnose: INVALID_ARGUMENT - decomposition_limit must be > 0, "
                f"got {decomposition_limit}"
            )
        if decomposition_limit > _MAX_DECOMPOSITION_LIMIT:
            raise ValueError(
                f"diagnose: INVALID_ARGUMENT - decomposition_limit exceeds max allowed "
                f"({_MAX_DECOMPOSITION_LIMIT}), got {decomposition_limit}"
            )

    top_level_issues: list[dict[str, Any]] = []
    diagnoses: list[dict[str, Any]] = []
    detect_summary: dict[str, Any] | None = None
    validation_guidance: dict[str, Any] | None = None
    resolved_time_scope: dict[str, Any] | None = None
    granularity: str | None = None
    followed_candidate_count = 0
    detect_step_id: str | None = None

    if mode == "explicit_compare":
        current_input = p.get("current")
        baseline_input = p.get("baseline")
        if not isinstance(current_input, dict) or not isinstance(baseline_input, dict):
            raise ValueError(
                "diagnose: INVALID_ARGUMENT - current and baseline are required "
                "when mode='explicit_compare'"
            )
        current_scope = current_input.get("scope") or scope
        baseline_scope = baseline_input.get("scope") or scope
        if current_scope != baseline_scope:
            raise ValueError(
                "diagnose: INVALID_ARGUMENT - explicit_compare current.scope and "
                "baseline.scope must match"
            )
        explicit_granularity: TimeGrain = "day"
        current_window = _normalize_range_time_scope(
            current_input.get("time_scope"),
            granularity=explicit_granularity,
            label="current.time_scope",
        )
        baseline_window = _normalize_range_time_scope(
            baseline_input.get("time_scope"),
            granularity=explicit_granularity,
            label="baseline.time_scope",
        )
        candidate = {
            "candidate_type": "explicit_compare",
            "window": {"start": current_window["start"], "end": current_window["end"]},
            "slice": None,
        }
        result = _follow_up_candidate(
            runtime=runtime,
            session_id=session_id,
            candidate=candidate,
            metric_ref=metric_ref,
            base_scope=current_scope,
            dimensions=dimensions,
            decomposition_limit=decomposition_limit,
            grain=explicit_granularity,
            baseline_window_override={
                "start": baseline_window["start"],
                "end": baseline_window["end"],
            },
        )
        diagnoses.append(result)
        followed_candidate_count = 1
        for issue in result.get("issues") or []:
            if issue.get("severity") == "error":
                top_level_issues.append(issue)
    else:
        ts_granularity = _normalize_granularity(p.get("granularity"))
        granularity = ts_granularity
        resolved_time_scope = _normalize_range_time_scope(
            p.get("time_scope"),
            granularity=ts_granularity,
            label="time_scope",
        )
        raw_candidate_limit = p.get("candidate_limit")
        candidate_limit: int | None = None
        if raw_candidate_limit is not None:
            candidate_limit = int(raw_candidate_limit)
            if candidate_limit <= 0:
                raise ValueError(
                    f"diagnose: INVALID_ARGUMENT - candidate_limit must be > 0, "
                    f"got {candidate_limit}"
                )

        detect_params: dict[str, Any] = {
            "metric": metric_ref,
            "time_scope": resolved_time_scope,
            "granularity": granularity,
            "sensitivity": sensitivity,
            "profile": profile,
        }
        if patterns is not None:
            detect_params["patterns"] = patterns
        if scope is not None:
            detect_params["scope"] = scope
        if detect_split_by:
            detect_params["split_by"] = detect_split_by
        if candidate_limit is not None:
            detect_params["limit"] = candidate_limit

        try:
            detect_result = run_detect_intent(
                runtime,
                session_id,
                detect_params,
            )
        except Exception as exc:
            raise ValueError(f"diagnose: DETECT_FAILED - {exc}") from exc

        detect_step_id = detect_result["step_ref"]["step_id"]
        detect_ref: dict[str, Any] = {
            "session_id": session_id,
            "step_id": detect_step_id,
            "step_type": "detect",
            "artifact_id": detect_result["artifact_id"],
        }
        detectability: dict[str, Any] = detect_result.get("detectability") or {}
        validation_guidance = detectability.get("guidance")
        if detectability.get("status") == "needs_attention":
            for iss in detectability.get("issues") or []:
                top_level_issues.append(
                    {
                        "code": "detect_needs_attention",
                        "severity": iss.get("severity", "warning"),
                        "message": iss.get("message", "detect returned needs_attention"),
                    }
                )

        all_candidates: list[dict[str, Any]] = detect_result.get("candidates") or []
        total_candidate_count: int = (detect_result.get("scan_summary") or {}).get(
            "total_candidate_count"
        ) or 0
        returned_candidate_count: int = len(all_candidates)
        candidates_to_follow = all_candidates[:followup_limit]
        followed_candidate_count = len(candidates_to_follow)
        follow_up_truncated = returned_candidate_count > followup_limit

        if total_candidate_count == 0:
            top_level_issues.append(
                {
                    "code": "no_detect_candidates",
                    "severity": "warning",
                    "message": (
                        "detect returned no candidates; use mode='explicit_compare' "
                        "when the current and baseline windows are already known, "
                        "or expand the scan window / enable period_shift."
                    ),
                }
            )
            explicit_compare_guidance = {
                "kind": "explicit_compare",
                "message": (
                    "Run diagnose(mode='explicit_compare') with current and baseline "
                    "range windows when investigating structural degradation."
                ),
            }
            if validation_guidance is None:
                validation_guidance = {
                    "recommended_next_action": "use_explicit_compare_or_expand_scan",
                    "fallback_path": explicit_compare_guidance,
                }
            else:
                validation_guidance["explicit_compare_fallback"] = explicit_compare_guidance

        for cand in candidates_to_follow:
            cand_result = _follow_up_candidate(
                runtime=runtime,
                session_id=session_id,
                candidate=cand,
                metric_ref=metric_ref,
                base_scope=scope,
                dimensions=dimensions,
                decomposition_limit=decomposition_limit,
                grain=ts_granularity,
                baseline_window_override=cand.get("baseline_window"),
            )
            diagnoses.append(cand_result)
            for issue in cand_result.get("issues") or []:
                if issue.get("severity") == "error":
                    top_level_issues.append(issue)

        if follow_up_truncated:
            top_level_issues.append(
                {
                    "code": "candidate_followup_truncated",
                    "severity": "warning",
                    "message": (
                        f"{returned_candidate_count} candidates returned by detect; "
                        f"only {followed_candidate_count} followed up "
                        f"(followup_limit={followup_limit})."
                    ),
                }
            )

        detect_summary = {
            "detect_ref": detect_ref,
            "returned_candidate_count": returned_candidate_count,
            "total_candidate_count": total_candidate_count,
            "followed_candidate_count": followed_candidate_count,
            "truncated": follow_up_truncated,
        }

    has_error_issue = any(i.get("severity") == "error" for i in top_level_issues)
    has_no_candidate_issue = any(i.get("code") == "no_detect_candidates" for i in top_level_issues)
    validation_status = (
        "needs_attention" if has_error_issue or has_no_candidate_issue else "diagnosable"
    )

    validation: dict[str, Any] = {
        "status": validation_status,
        "issues": top_level_issues,
    }
    if validation_guidance is not None:
        validation["guidance"] = validation_guidance

    now = datetime.now(UTC).isoformat()
    step_id = new_step_id()

    bundle: dict[str, Any] = {
        "result_type": "diagnosis_bundle",
        "intent_type": "diagnose",
        "step_type": "diagnose",
        "artifact_schema_version": "v1",
        "mode": mode,
        "metric": metric_ref,
        "time_scope": resolved_time_scope,
        "granularity": granularity,
        "scope": scope,
        "detect_split_by": detect_split_by,
        "candidate_dimensions": dimensions,
        "profile": profile,
        "sensitivity": sensitivity,
        "patterns": patterns,
        "baseline_policy": baseline_policy,
        "validation": validation,
        "provenance": {
            "artifact_ref": {
                "session_id": session_id,
                "step_id": step_id,
                "step_type": "diagnose",
                "artifact_id": None,  # patched after _insert_artifact
            },
            "source_detect_ref": detect_summary["detect_ref"] if detect_summary else None,
            "artifact_schema_version": "v1",
            "derivation_version": _DERIVED_LOGIC_VERSION,
            "projection_ref": None,
        },
        "detect_summary": detect_summary,
        "diagnoses": diagnoses,
        "version": {
            "intent_contract_version": "diagnose.v1",
            "projection_version": _PROJECTION_VERSION,
            "derived_logic_version": _DERIVED_LOGIC_VERSION,
        },
        "execution_metadata": {
            "engine": "service",
            "executed_at": now,
        },
    }

    # ── Step 6: persist bundle ─────────────────────────────────────────────────
    artifact_name = f"{metric_name}_diagnosis_bundle"
    diagnosed_count = sum(1 for d in diagnoses if d.get("status") == "diagnosed")
    summary_str = (
        f"diagnose {metric_name}: {validation_status} "
        f"({followed_candidate_count} followed, {diagnosed_count} diagnosed, "
        f"{len(dimensions)} dimension(s))"
    )

    # NOTE: Cannot use commit_step_result() here because this derived intent
    # uses raw insert_artifact (no extraction boundary) and patches the
    # artifact_id into the bundle between insert and step creation.
    artifact_id = runtime.insert_artifact(
        session_id, step_id, "diagnosis_bundle", artifact_name, bundle
    )
    bundle["provenance"]["artifact_ref"]["artifact_id"] = artifact_id
    bundle["step_ref"] = {
        "session_id": session_id,
        "step_id": step_id,
        "step_type": "diagnose",
    }
    bundle["artifact_id"] = artifact_id

    provenance: dict[str, Any] = {
        "detect_step_id": detect_step_id,
        "mode": mode,
        "followed_candidate_count": followed_candidate_count,
        "dimensions": dimensions,
        "followup_limit": followup_limit,
        "decomposition_limit": decomposition_limit,
        "derived_logic_version": _DERIVED_LOGIC_VERSION,
        "projection_version": _PROJECTION_VERSION,
    }
    runtime.insert_step(step_id, session_id, "diagnose", summary_str, bundle, provenance=provenance)
    return bundle


# ── Per-candidate follow-up ────────────────────────────────────────────────────


def _follow_up_candidate(
    runtime: MarivoRuntime,
    session_id: str,
    candidate: dict[str, Any],
    metric_ref: str,
    base_scope: dict[str, Any] | None,
    dimensions: list[str],
    decomposition_limit: int,
    grain: TimeGrain,
    baseline_window_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expand a single detect candidate into observe+compare+decompose×D.

    Returns a DiagnoseCandidateResult dict.
    """
    candidate_ref: dict[str, Any] | None = candidate.get("candidate_ref") or None
    candidate_summary: dict[str, Any] = {
        k: candidate[k]
        for k in (
            "window",
            "slice",
            "observed_value",
            "expected_value",
            "deviation_abs",
            "deviation_pct",
            "candidate_score",
            "flag_level",
            "direction",
            "candidate_type",
            "baseline_window",
        )
        if k in candidate
    }

    cand_window: dict[str, Any] = candidate.get("window") or {}
    cand_slice: dict[str, Any] | None = candidate.get("slice") or None
    cand_issues: list[dict[str, Any]] = []

    # ── Baseline derivation ────────────────────────────────────────────────────
    baseline_derivation: dict[str, Any]
    current_start_str: str = cand_window.get("start") or ""
    current_end_str: str = cand_window.get("end") or ""
    baseline_start_str: str | None = None
    baseline_end_str: str | None = None
    baseline_ok = False

    try:
        if baseline_window_override is not None:
            baseline_window = {
                "start": str(baseline_window_override.get("start") or "").strip(),
                "end": str(baseline_window_override.get("end") or "").strip(),
            }
            if not baseline_window["start"] or not baseline_window["end"]:
                raise ValueError("baseline_window override requires start and end")
        else:
            baseline_window = previous_adjacent_window(
                current_start_str,
                current_end_str,
                grain=grain,
            )
        baseline_start_str = baseline_window["start"]
        baseline_end_str = baseline_window["end"]
        baseline_derivation = {
            "policy": "previous_adjacent_equal_length",
            "current_window": {"start": current_start_str, "end": current_end_str},
            "baseline_window": {"start": baseline_start_str, "end": baseline_end_str},
        }
        baseline_ok = True
    except Exception as exc:
        baseline_derivation = {
            "policy": "previous_adjacent_equal_length",
            "current_window": {"start": current_start_str, "end": current_end_str},
            "baseline_window": None,
        }
        cand_issues.append(
            {
                "code": "baseline_derivation_failed",
                "severity": "error",
                "message": f"Could not derive baseline window: {exc}",
                "candidate_ref": candidate_ref,
            }
        )

    if not baseline_ok:
        return {
            "candidate_ref": candidate_ref,
            "candidate": candidate_summary,
            "baseline_derivation": baseline_derivation,
            "current_ref": None,
            "baseline_ref": None,
            "compare_ref": None,
            "comparison": None,
            "drivers": [],
            "status": "needs_attention",
            "issues": cand_issues,
        }

    # ── Combine scope with candidate slice ─────────────────────────────────────
    combined_scope = _combine_scope(base_scope, cand_slice)

    # ── Current observe ────────────────────────────────────────────────────────
    current_ref: dict[str, Any] | None = None
    current_step_id: str | None = None

    try:
        current_obs = run_observe_intent(
            runtime,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": {"kind": "range", "start": current_start_str, "end": current_end_str},
                "scope": combined_scope,
            },
        )
        current_step_id = current_obs["step_ref"]["step_id"]
        current_artifact_id: str = current_obs["artifact_id"]
        current_ref = {
            "session_id": session_id,
            "step_id": current_step_id,
            "step_type": "observe",
            "artifact_id": current_artifact_id,
        }
    except Exception as exc:
        cand_issues.append(
            {
                "code": "observe_failed",
                "severity": "error",
                "message": f"Current observe failed: {exc}",
                "candidate_ref": candidate_ref,
            }
        )

    # ── Baseline observe ───────────────────────────────────────────────────────
    baseline_ref: dict[str, Any] | None = None
    baseline_step_id: str | None = None

    try:
        baseline_obs = run_observe_intent(
            runtime,
            session_id,
            {
                "metric": metric_ref,
                "time_scope": {
                    "kind": "range",
                    "start": baseline_start_str,
                    "end": baseline_end_str,
                },
                "scope": combined_scope,
            },
        )
        baseline_step_id = baseline_obs["step_ref"]["step_id"]
        baseline_artifact_id: str = baseline_obs["artifact_id"]
        baseline_ref = {
            "session_id": session_id,
            "step_id": baseline_step_id,
            "step_type": "observe",
            "artifact_id": baseline_artifact_id,
        }
    except Exception as exc:
        cand_issues.append(
            {
                "code": "observe_failed",
                "severity": "error",
                "message": f"Baseline observe failed: {exc}",
                "candidate_ref": candidate_ref,
            }
        )

    both_obs_ok = current_ref is not None and baseline_ref is not None

    # ── Compare ────────────────────────────────────────────────────────────────
    compare_ref: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    compare_step_id: str | None = None
    comparability_status: str = "not_comparable"

    if both_obs_ok:
        try:
            compare_result = run_compare_intent(
                runtime,
                session_id,
                {
                    "left_ref": {
                        "step_id": current_step_id,
                        "session_id": session_id,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": baseline_step_id,
                        "session_id": session_id,
                        "step_type": "observe",
                    },
                    "mode": "scalar",
                },
            )
            compare_step_id = compare_result["step_ref"]["step_id"]
            compare_artifact_id_val: str = compare_result["artifact_id"]
            compare_ref = {
                "session_id": session_id,
                "step_id": compare_step_id,
                "step_type": "compare",
                "artifact_id": compare_artifact_id_val,
            }
            comparability: dict[str, Any] = compare_result.get("comparability") or {}
            comparability_status = comparability.get("status") or "comparable"
            comparison = {
                "comparison_type": "scalar_delta",
                "left_value": compare_result.get("left_value"),
                "right_value": compare_result.get("right_value"),
                "absolute_delta": compare_result.get("absolute_delta"),
                "relative_delta": compare_result.get("relative_delta"),
                "direction": compare_result.get("direction") or "undefined",
                "comparability_status": comparability_status,
            }
            if comparability_status == "needs_attention":
                for iss in comparability.get("issues") or []:
                    cand_issues.append(
                        {
                            "code": "compare_needs_attention",
                            "severity": "warning",
                            "message": iss.get("message", "compare returned needs_attention"),
                            "candidate_ref": candidate_ref,
                        }
                    )
        except Exception as exc:
            msg = str(exc)
            if "NOT_COMPARABLE" in msg:
                cand_issues.append(
                    {
                        "code": "compare_not_comparable",
                        "severity": "error",
                        "message": f"Compare not comparable: {exc}",
                        "candidate_ref": candidate_ref,
                    }
                )
            else:
                cand_issues.append(
                    {
                        "code": "compare_needs_attention",
                        "severity": "error",
                        "message": f"Compare failed unexpectedly: {exc}",
                        "candidate_ref": candidate_ref,
                    }
                )

    # ── Decompose × dimensions ─────────────────────────────────────────────────
    drivers: list[dict[str, Any]] = []

    can_decompose = compare_ref is not None and comparability_status in {
        "comparable",
        "needs_attention",
    }

    if can_decompose:
        for dimension in dimensions:
            driver = _decompose_for_dimension(
                runtime=runtime,
                session_id=session_id,
                compare_step_id=compare_step_id,  # type: ignore[arg-type]
                dimension=dimension,
                decomposition_limit=decomposition_limit,
                candidate_ref=candidate_ref,
            )
            drivers.append(driver)
            for iss in driver.get("issues") or []:
                if iss.get("severity") == "error":
                    cand_issues.append(iss)
    elif compare_ref is None and both_obs_ok:
        # compare was attempted but failed — no decompose possible
        pass
    elif not both_obs_ok:
        # observations failed — no decompose possible
        pass

    # ── Candidate status derivation ────────────────────────────────────────────
    all_refs_present = (
        baseline_derivation.get("baseline_window") is not None
        and current_ref is not None
        and baseline_ref is not None
        and compare_ref is not None
    )
    status = (
        "diagnosed"
        if (all_refs_present and comparability_status == "comparable")
        else "needs_attention"
    )

    return {
        "candidate_ref": candidate_ref,
        "candidate": candidate_summary,
        "baseline_derivation": baseline_derivation,
        "current_ref": current_ref,
        "baseline_ref": baseline_ref,
        "compare_ref": compare_ref,
        "comparison": comparison,
        "drivers": drivers,
        "status": status,
        "issues": cand_issues,
    }


def _decompose_for_dimension(
    runtime: MarivoRuntime,
    session_id: str,
    compare_step_id: str,
    dimension: str,
    decomposition_limit: int,
    candidate_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run decompose for one dimension and build a DiagnoseDriverSet."""
    decompose_ref: dict[str, Any] | None = None
    attribution_status: str = "not_attributable"
    rows: list[dict[str, Any]] = []
    driver_issues: list[dict[str, Any]] = []
    unexplained_abs: float | None = None
    unexplained_share: float | None = None
    unexplained_reason: str | None = None
    total_row_count: int | None = None
    returned_row_count = 0
    is_truncated = False
    others_abs: float | None = None
    others_share: float | None = None

    try:
        decompose_result = run_decompose_intent(
            runtime,
            session_id,
            {
                "compare_ref": {
                    "step_id": compare_step_id,
                    "session_id": session_id,
                    "step_type": "compare",
                },
                "dimension": dimension,
                "method": "delta_share",
                "limit": decomposition_limit,
            },
        )

        decompose_step_id: str = decompose_result["step_ref"]["step_id"]
        decompose_artifact_id: str = decompose_result["artifact_id"]
        decompose_ref = {
            "session_id": session_id,
            "step_id": decompose_step_id,
            "step_type": "decompose",
            "artifact_id": decompose_artifact_id,
        }

        attrib: dict[str, Any] = decompose_result.get("attribution") or {}
        attribution_status = attrib.get("status") or "needs_attention"

        raw_attrib_issues: list[dict[str, Any]] = attrib.get("issues") or []
        for iss in raw_attrib_issues:
            driver_issues.append(
                {
                    "code": "decompose_needs_attention",
                    "severity": iss.get("severity", "warning"),
                    "message": iss.get("message", ""),
                    "dimension": dimension,
                    "candidate_ref": candidate_ref,
                }
            )

        all_rows: list[dict[str, Any]] = decompose_result.get("rows") or []
        total_row_count = len(all_rows)
        returned_rows = all_rows[:decomposition_limit]
        returned_row_count = len(returned_rows)
        is_truncated = total_row_count > returned_row_count
        rows = returned_rows

        if is_truncated:
            tail_rows = all_rows[decomposition_limit:]
            scope_delta = decompose_result.get("scope_absolute_delta")
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
                if scope_delta is not None and scope_delta != 0:
                    others_share = others_abs / scope_delta

        unexplained_abs = decompose_result.get("unexplained_absolute_delta")
        unexplained_share = decompose_result.get("unexplained_share")
        unexplained_reason = decompose_result.get("unexplained_reason")

    except Exception as exc:
        msg = str(exc)
        code = (
            "decompose_not_attributable"
            if "NOT_ATTRIBUTABLE" in msg
            else "decompose_needs_attention"
        )
        severity = "error" if "NOT_ATTRIBUTABLE" in msg else "warning"
        driver_issues.append(
            {
                "code": code,
                "severity": severity,
                "message": f"Decompose failed for dimension '{dimension}': {exc}",
                "dimension": dimension,
                "candidate_ref": candidate_ref,
            }
        )
        attribution_status = "not_attributable" if "NOT_ATTRIBUTABLE" in msg else "needs_attention"

    return {
        "dimension": dimension,
        "decompose_ref": decompose_ref,
        "attribution_status": attribution_status,
        "rows": rows,
        "returned_row_count": returned_row_count,
        "total_row_count": total_row_count,
        "is_truncated": is_truncated,
        "others_absolute_contribution": others_abs if is_truncated else None,
        "others_contribution_share": others_share if is_truncated else None,
        "unexplained_absolute_delta": unexplained_abs,
        "unexplained_share": unexplained_share,
        "unexplained_reason": unexplained_reason,
        "issues": driver_issues,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────


def _combine_scope(
    base_scope: dict[str, Any] | None,
    candidate_slice: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge candidate slice into base scope constraints."""
    if not candidate_slice:
        return base_scope
    base_constraints: dict[str, Any] = {}
    if base_scope and isinstance(base_scope.get("constraints"), dict):
        base_constraints = dict(base_scope["constraints"])
    combined: dict[str, Any] = {**base_constraints, **candidate_slice}
    result: dict[str, Any] = {"constraints": combined}
    if base_scope and base_scope.get("predicate") is not None:
        result["predicate"] = base_scope["predicate"]
    return result
