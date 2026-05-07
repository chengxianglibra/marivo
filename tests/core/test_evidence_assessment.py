"""Tests for app.core.evidence.assessment pure functions."""

from __future__ import annotations

from app.core.evidence.assessment import (
    compute_canonical_diff,
    derive_confidence_grade,
    evaluate_calendar_alignment_requirements,
    has_complete_calendar_alignment_summary,
    make_assessment_id,
    make_gap_id,
    make_inference_record_id,
    resolve_assessment_status,
)

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


def test_make_assessment_id_deterministic() -> None:
    id1 = make_assessment_id("s1", "p1", 1)
    id2 = make_assessment_id("s1", "p1", 1)
    assert id1 == id2


def test_make_assessment_id_format() -> None:
    aid = make_assessment_id("s1", "p1", 1)
    assert aid.startswith("assess_")
    assert len(aid) == 7 + 24


def test_make_inference_record_id_format() -> None:
    irec_id = make_inference_record_id("s1", "p1", "a1", "rule1")
    assert irec_id.startswith("irec_")


def test_make_gap_id_format() -> None:
    gap_id = make_gap_id("s1", "p1", "req1", "a1")
    assert gap_id.startswith("gap_")


# ---------------------------------------------------------------------------
# resolve_assessment_status
# ---------------------------------------------------------------------------


def test_resolve_assessment_status_supported() -> None:
    status, blocked = resolve_assessment_status(
        precond_gate_result="hit",
        comparability_gate_result="hit",
        support_satisfied=True,
        oppose_satisfied=False,
    )
    assert status == "supported"
    assert not blocked


def test_resolve_assessment_status_insufficient_no_evidence() -> None:
    status, blocked = resolve_assessment_status(
        precond_gate_result="hit",
        comparability_gate_result="hit",
        support_satisfied=False,
        oppose_satisfied=False,
    )
    assert status == "insufficient"
    assert not blocked


def test_resolve_assessment_status_mixed() -> None:
    status, blocked = resolve_assessment_status(
        precond_gate_result="hit",
        comparability_gate_result="hit",
        support_satisfied=True,
        oppose_satisfied=True,
    )
    assert status == "mixed"
    assert not blocked


def test_resolve_assessment_status_contradicted() -> None:
    status, blocked = resolve_assessment_status(
        precond_gate_result="hit",
        comparability_gate_result="hit",
        support_satisfied=False,
        oppose_satisfied=True,
    )
    assert status == "contradicted"
    assert not blocked


def test_resolve_assessment_status_guardrail_blocked_precond() -> None:
    status, blocked = resolve_assessment_status(
        precond_gate_result="miss",
        comparability_gate_result="hit",
        support_satisfied=True,
        oppose_satisfied=False,
    )
    assert status == "insufficient"
    assert blocked


def test_resolve_assessment_status_guardrail_blocked_comparability() -> None:
    status, blocked = resolve_assessment_status(
        precond_gate_result="hit",
        comparability_gate_result="miss",
        support_satisfied=True,
        oppose_satisfied=False,
    )
    assert status == "insufficient"
    assert blocked


# ---------------------------------------------------------------------------
# compute_canonical_diff
# ---------------------------------------------------------------------------


def test_compute_canonical_diff_no_prior() -> None:
    assert (
        compute_canonical_diff(
            candidate_status="supported",
            candidate_confidence_grade="low",
            candidate_confidence_rationale={},
            candidate_supporting_ids=["f1"],
            candidate_opposing_ids=[],
            candidate_gap_memberships=[],
            prior_latest=None,
        )
        is True
    )


def test_compute_canonical_diff_identical_no_diff() -> None:
    prior = {
        "status": "supported",
        "confidence_grade": "low",
        "confidence_rationale_json": {},
        "supporting_finding_ids_json": ["f1"],
        "opposing_finding_ids_json": [],
        "gap_memberships_json": [],
    }
    assert (
        compute_canonical_diff(
            candidate_status="supported",
            candidate_confidence_grade="low",
            candidate_confidence_rationale={},
            candidate_supporting_ids=["f1"],
            candidate_opposing_ids=[],
            candidate_gap_memberships=[],
            prior_latest=prior,
        )
        is False
    )


def test_compute_canonical_diff_different_status() -> None:
    prior = {
        "status": "insufficient",
        "confidence_grade": "very_low",
        "confidence_rationale_json": {},
        "supporting_finding_ids_json": [],
        "opposing_finding_ids_json": [],
        "gap_memberships_json": [],
    }
    assert (
        compute_canonical_diff(
            candidate_status="supported",
            candidate_confidence_grade="low",
            candidate_confidence_rationale={},
            candidate_supporting_ids=["f1"],
            candidate_opposing_ids=[],
            candidate_gap_memberships=[],
            prior_latest=prior,
        )
        is True
    )


# ---------------------------------------------------------------------------
# has_complete_calendar_alignment_summary
# ---------------------------------------------------------------------------


def test_has_complete_calendar_alignment_summary_none() -> None:
    assert has_complete_calendar_alignment_summary(None) is False


def test_has_complete_calendar_alignment_summary_incomplete() -> None:
    assert has_complete_calendar_alignment_summary({}) is False


def test_has_complete_calendar_alignment_summary_complete() -> None:
    summary = {
        "policy_ref": "calendar_policy.natural_yoy",
        "comparison_basis": "yoy",
        "resolved_calendar_source": "builtin",
        "resolved_calendar_version": "v1",
        "resolved_baseline_generation_rule": "previous_year",
        "current_window": {"start": "2024-01-01", "end": "2024-02-01"},
        "baseline_window": {"start": "2023-01-01", "end": "2023-02-01"},
        "coverage_summary": {"aligned_bucket_count": 31},
        "bucket_pairing": [],
        "comparability_warnings": [],
    }
    assert has_complete_calendar_alignment_summary(summary) is True


# ---------------------------------------------------------------------------
# evaluate_calendar_alignment_requirements
# ---------------------------------------------------------------------------


def test_evaluate_calendar_alignment_requirements_all_pass() -> None:
    result = evaluate_calendar_alignment_requirements(
        comparability_status="comparable",
        issue_codes=set(),
        error_issue_codes=set(),
        warning_codes=set(),
        data_warning_codes=set(),
        calendar_alignment={
            "policy_ref": "p",
            "comparison_basis": "yoy",
            "resolved_calendar_source": "s",
            "resolved_calendar_version": "v",
            "resolved_baseline_generation_rule": "r",
            "current_window": {},
            "baseline_window": {},
            "coverage_summary": {},
            "bucket_pairing": [],
            "comparability_warnings": [],
        },
        aligned_ratio=1.0,
        unpaired_bucket_count=0,
        data_coverage_ratio=1.0,
    )
    assert len(result["failed_requirement_keys"]) == 0
    assert "baseline_calendar_policy_resolved" in result["matched_requirement_keys"]


def test_evaluate_calendar_alignment_requirements_holiday_failure() -> None:
    result = evaluate_calendar_alignment_requirements(
        comparability_status="needs_attention",
        issue_codes={"holiday_cluster_unmapped"},
        error_issue_codes=set(),
        warning_codes=set(),
        data_warning_codes=set(),
        calendar_alignment=None,
        aligned_ratio=None,
        unpaired_bucket_count=None,
        data_coverage_ratio=None,
    )
    assert "holiday_cluster_alignment_complete" in result["failed_requirement_keys"]
    assert "baseline_calendar_policy_resolved" in result["failed_requirement_keys"]


# ---------------------------------------------------------------------------
# derive_confidence_grade
# ---------------------------------------------------------------------------


def test_derive_confidence_grade_insufficient() -> None:
    grade, rationale = derive_confidence_grade(
        status="insufficient",
        precond_gate_result="miss",
        data_quality_impact="none",
    )
    assert grade == "very_low"
    assert rationale["evidence_sufficiency"] == "very_weak"


def test_derive_confidence_grade_supported() -> None:
    grade, rationale = derive_confidence_grade(
        status="supported",
        precond_gate_result="hit",
        data_quality_impact="none",
    )
    assert grade == "low"
    assert rationale["evidence_consistency"] == "consistent"


def test_derive_confidence_grade_severe_data_quality() -> None:
    grade, _ = derive_confidence_grade(
        status="supported",
        precond_gate_result="hit",
        data_quality_impact="severe",
    )
    assert grade == "low"
