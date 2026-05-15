"""Pure assessment computation logic extracted from the evidence pipeline.

Extracted from ``marivo.runtime.evidence.assessment_recompute`` as part of
Phase 3c.  This module contains only the pure computation portions:
- Assessment ID generation
- Status resolution algorithm
- Canonical diff detection
- Calendar alignment requirement evaluation
- Coverage requirement evaluation

The I/O-bound parts (repository access, commit logic, gap materialization)
remain in the original module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


def make_assessment_id(session_id: str, proposition_id: str, snapshot_seq: int) -> str:
    """Derive a stable ``assessment_id`` from (session, proposition, seq).

    Format: ``"assess_"`` + first 24 hex chars of SHA-256.
    """
    raw = f"{session_id}:{proposition_id}:{snapshot_seq}"
    return "assess_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def make_inference_record_id(
    session_id: str, proposition_id: str, assessment_id: str, rule_id: str
) -> str:
    """Derive a stable ``inference_record_id``.

    Format: ``"irec_"`` + first 24 hex chars of SHA-256.
    """
    raw = f"{session_id}:{proposition_id}:{assessment_id}:{rule_id}"
    return "irec_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def make_gap_id(
    session_id: str,
    proposition_id: str,
    requirement_key: str,
    opened_by_assessment_id: str,
) -> str:
    """Derive a stable ``gap_id`` from (session, proposition, requirement_key, assessment).

    Format: ``"gap_"`` + first 24 hex chars of SHA-256.
    """
    raw = f"{session_id}:{proposition_id}:{requirement_key}:{opened_by_assessment_id}"
    return "gap_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Status resolution (Step 6)
# ---------------------------------------------------------------------------


def resolve_assessment_status(
    *,
    precond_gate_result: str,
    comparability_gate_result: str,
    support_satisfied: bool,
    oppose_satisfied: bool,
) -> tuple[str, bool]:
    """Implement the 4-step threshold algorithm for assessment status.

    Parameters
    ----------
    precond_gate_result:
        ``"hit"`` or ``"miss"`` from the precondition gate.
    comparability_gate_result:
        ``"hit"`` or ``"miss"`` from the comparability gate.
    support_satisfied:
        Whether the support evidence threshold is met.
    oppose_satisfied:
        Whether the oppose evidence threshold is met.

    Returns
    -------
    tuple[str, bool]
        (status, guardrail_blocked).  Status is one of:
        ``"supported"``, ``"contradicted"``, ``"mixed"``, ``"insufficient"``.
    """
    guardrail_blocked = precond_gate_result == "miss" or comparability_gate_result == "miss"

    if guardrail_blocked:
        status = "insufficient"
    elif support_satisfied and oppose_satisfied:
        status = "mixed"
    elif support_satisfied:
        status = "supported"
    elif oppose_satisfied:
        status = "contradicted"
    else:
        status = "insufficient"

    return status, guardrail_blocked


# ---------------------------------------------------------------------------
# Canonical diff detection (Step 9)
# ---------------------------------------------------------------------------


def compute_canonical_diff(
    *,
    candidate_status: str,
    candidate_confidence_grade: str,
    candidate_confidence_rationale: dict[str, Any],
    candidate_supporting_ids: list[str],
    candidate_opposing_ids: list[str],
    candidate_gap_memberships: list[dict[str, Any]],
    prior_latest: dict[str, Any] | None,
) -> bool:
    """Return ``True`` iff the candidate snapshot differs from *prior_latest*.

    Always returns ``True`` when *prior_latest* is ``None`` (first snapshot).
    """
    if prior_latest is None:
        return True

    candidate_key = _canonical_diff_key(
        status=candidate_status,
        confidence_grade=candidate_confidence_grade,
        confidence_rationale=candidate_confidence_rationale,
        supporting_finding_ids=candidate_supporting_ids,
        opposing_finding_ids=candidate_opposing_ids,
        gap_memberships=candidate_gap_memberships,
    )

    prior_rationale = prior_latest.get("confidence_rationale_json") or {}
    prior_supporting = prior_latest.get("supporting_finding_ids_json") or []
    prior_opposing = prior_latest.get("opposing_finding_ids_json") or []
    prior_gap_memberships = prior_latest.get("gap_memberships_json") or []

    prior_key = _canonical_diff_key(
        status=prior_latest["status"],
        confidence_grade=prior_latest["confidence_grade"],
        confidence_rationale=prior_rationale,
        supporting_finding_ids=prior_supporting,
        opposing_finding_ids=prior_opposing,
        gap_memberships=prior_gap_memberships,
    )

    return candidate_key != prior_key


def _canonical_diff_key(
    *,
    status: str,
    confidence_grade: str,
    confidence_rationale: dict[str, Any],
    supporting_finding_ids: list[str],
    opposing_finding_ids: list[str],
    gap_memberships: list[dict[str, Any]],
) -> str:
    """Produce a stable string key for canonical diff comparison."""
    return json.dumps(
        {
            "status": status,
            "confidence_grade": confidence_grade,
            "confidence_rationale": confidence_rationale,
            "supporting_finding_ids": sorted(supporting_finding_ids),
            "opposing_finding_ids": sorted(opposing_finding_ids),
            "gap_memberships": sorted(
                [
                    {
                        "gap_ref": m["gap_ref"],
                        "blocking": m["blocking"],
                        "severity": m["severity"],
                    }
                    for m in gap_memberships
                ],
                key=lambda m: m["gap_ref"]["gap_id"],
            ),
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# Calendar alignment requirement evaluation (Step 3 — comparability gate)
# ---------------------------------------------------------------------------

_COVERAGE_INSUFFICIENT_CODES = frozenset({"alignment_coverage_insufficient"})
_DATA_COVERAGE_INSUFFICIENT_CODES = frozenset({"metric_data_coverage_incomplete"})
_HOLIDAY_ALIGNMENT_FAILURE_CODES = frozenset({"holiday_cluster_unmapped"})
_WEEKDAY_TIE_FAILURE_CODES = frozenset({"weekday_pairing_tie"})
_TIE_BREAKER_FAILURE_CODES = frozenset({"weekday_pairing_tie", "alignment_tie_breaker_unresolved"})

_CALENDAR_ALIGNMENT_REQUIRED_STRING_FIELDS: tuple[str, ...] = (
    "policy_ref",
    "comparison_basis",
    "resolved_calendar_source",
    "resolved_calendar_version",
    "resolved_baseline_generation_rule",
)
_CALENDAR_ALIGNMENT_REQUIRED_DICT_FIELDS: tuple[str, ...] = (
    "current_window",
    "baseline_window",
    "coverage_summary",
)
_CALENDAR_ALIGNMENT_REQUIRED_LIST_FIELDS: tuple[str, ...] = (
    "bucket_pairing",
    "comparability_warnings",
)


def has_complete_calendar_alignment_summary(calendar_alignment: dict[str, Any] | None) -> bool:
    """Check whether a calendar alignment summary dict is structurally complete."""
    if not isinstance(calendar_alignment, dict):
        return False
    for field in _CALENDAR_ALIGNMENT_REQUIRED_STRING_FIELDS:
        value = calendar_alignment.get(field)
        if not isinstance(value, str) or not value:
            return False
    for field in _CALENDAR_ALIGNMENT_REQUIRED_DICT_FIELDS:
        if not isinstance(calendar_alignment.get(field), dict):
            return False
    for field in _CALENDAR_ALIGNMENT_REQUIRED_LIST_FIELDS:
        if not isinstance(calendar_alignment.get(field), list):
            return False
    return True


def evaluate_calendar_alignment_requirements(
    *,
    comparability_status: str,
    issue_codes: set[str],
    error_issue_codes: set[str],
    warning_codes: set[str],
    data_warning_codes: set[str],
    calendar_alignment: dict[str, Any] | None,
    aligned_ratio: Any,
    unpaired_bucket_count: Any,
    data_coverage_ratio: Any,
) -> dict[str, Any]:
    """Evaluate calendar alignment requirements for the comparability gate.

    Returns a dict with:
    - ``matched_requirement_keys``: set of requirement keys that passed
    - ``failed_requirement_keys``: set of requirement keys that failed
    - ``has_attention_signal``: bool
    - ``has_error_signal``: bool
    """
    matched_requirement_keys: set[str] = set()
    failed_requirement_keys: set[str] = set()
    has_attention_signal = comparability_status == "needs_attention"
    has_error_signal = False

    if has_complete_calendar_alignment_summary(calendar_alignment):
        matched_requirement_keys.add("baseline_calendar_policy_resolved")
    else:
        failed_requirement_keys.add("baseline_calendar_policy_resolved")

    if issue_codes & _HOLIDAY_ALIGNMENT_FAILURE_CODES:
        failed_requirement_keys.add("holiday_cluster_alignment_complete")
    else:
        matched_requirement_keys.add("holiday_cluster_alignment_complete")

    if issue_codes & _WEEKDAY_TIE_FAILURE_CODES:
        failed_requirement_keys.add("weekday_pairing_compatible")
    else:
        matched_requirement_keys.add("weekday_pairing_compatible")

    tie_breaker_failed = bool(issue_codes & _TIE_BREAKER_FAILURE_CODES)
    if tie_breaker_failed:
        failed_requirement_keys.add("alignment_tie_breaker_resolved")
    else:
        matched_requirement_keys.add("alignment_tie_breaker_resolved")

    coverage_failed, coverage_attention = _evaluate_coverage_requirement(
        calendar_alignment=calendar_alignment,
        issue_codes=issue_codes,
        warning_codes=warning_codes,
        aligned_ratio=aligned_ratio,
        unpaired_bucket_count=unpaired_bucket_count,
    )
    if coverage_failed:
        failed_requirement_keys.add("calendar_coverage_sufficient")
    else:
        matched_requirement_keys.add("calendar_coverage_sufficient")

    data_coverage_failed = _evaluate_data_coverage_requirement(
        issue_codes=issue_codes,
        data_warning_codes=data_warning_codes,
        data_coverage_ratio=data_coverage_ratio,
    )
    if data_coverage_failed:
        failed_requirement_keys.add("metric_data_coverage_sufficient")
    else:
        matched_requirement_keys.add("metric_data_coverage_sufficient")

    if error_issue_codes:
        has_error_signal = True
    if coverage_attention or tie_breaker_failed or warning_codes or data_coverage_failed:
        has_attention_signal = True

    return {
        "matched_requirement_keys": matched_requirement_keys,
        "failed_requirement_keys": failed_requirement_keys,
        "has_attention_signal": has_attention_signal,
        "has_error_signal": has_error_signal,
    }


def _normalize_numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _evaluate_coverage_requirement(
    *,
    calendar_alignment: dict[str, Any] | None,
    issue_codes: set[str],
    warning_codes: set[str],
    aligned_ratio: Any,
    unpaired_bucket_count: Any,
) -> tuple[bool, bool]:
    """Returns (coverage_failed, coverage_attention)."""
    coverage_failed = bool((issue_codes | warning_codes) & _COVERAGE_INSUFFICIENT_CODES)
    if calendar_alignment is None:
        return coverage_failed, coverage_failed

    aligned_ratio_value = _normalize_numeric(aligned_ratio)
    unpaired_bucket_count_value = _normalize_numeric(unpaired_bucket_count)
    if aligned_ratio_value is None or unpaired_bucket_count_value is None:
        return True, True
    if aligned_ratio_value != 1.0 or unpaired_bucket_count_value != 0.0:
        return True, True
    return coverage_failed, coverage_failed


def _evaluate_data_coverage_requirement(
    *,
    issue_codes: set[str],
    data_warning_codes: set[str],
    data_coverage_ratio: Any,
) -> bool:
    """Returns True if data coverage is insufficient."""
    if (issue_codes | data_warning_codes) & _DATA_COVERAGE_INSUFFICIENT_CODES:
        return True
    return isinstance(data_coverage_ratio, (int, float)) and float(data_coverage_ratio) < 0.9999


# ---------------------------------------------------------------------------
# Confidence shaping (Step 8) — pure grade derivation
# ---------------------------------------------------------------------------

_GRADE_ORDER = ["very_low", "low", "medium", "high", "very_high"]


def derive_confidence_grade(
    *,
    status: str,
    precond_gate_result: str,
    data_quality_impact: str,
) -> tuple[str, dict[str, Any]]:
    """Derive confidence grade from assessment state.

    Returns (confidence_grade, confidence_rationale).

    This is the pure core of Step 8 confidence shaping, with the grade
    derivation rules extracted from the original monolithic function.
    """
    precond_miss = precond_gate_result == "miss"

    # evidence_sufficiency
    evidence_sufficiency = "very_weak" if precond_miss else "weak"

    # evidence_consistency
    if status == "supported" or status == "contradicted":
        evidence_consistency = "consistent"
    elif status == "mixed":
        evidence_consistency = "conflicting"
    else:
        evidence_consistency = "mixed"

    rule_coverage = "partial"

    # Base grade derivation
    if evidence_sufficiency == "very_weak" or status == "insufficient":
        base_grade = "very_low"
    elif status in ("supported", "contradicted"):
        base_grade = "low"
    else:
        base_grade = "low"

    def _cap_grade(grade: str, cap: str) -> str:
        gi = _GRADE_ORDER.index(grade)
        ci = _GRADE_ORDER.index(cap)
        return _GRADE_ORDER[min(gi, ci)]

    confidence_grade = base_grade
    if data_quality_impact == "severe":
        confidence_grade = _cap_grade(confidence_grade, "low")
    if evidence_sufficiency == "very_weak":
        confidence_grade = _cap_grade(confidence_grade, "low")

    confidence_rationale: dict[str, Any] = {
        "evidence_sufficiency": evidence_sufficiency,
        "evidence_consistency": evidence_consistency,
        "rule_coverage": rule_coverage,
        "data_quality_impact": data_quality_impact,
        "rationale_notes": [],
    }

    return confidence_grade, confidence_rationale
