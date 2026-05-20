"""Tests for HTML report generation."""

from __future__ import annotations

from pathlib import Path

from marivo.runtime.report import (
    _NOISE_WARNING_CODES,
    ANALYSIS_PHASES,
    ExecutiveSummaryData,
    PropositionReportData,
    ReportData,
    StepReportData,
    _classify_step_phase,
    _extract_artifact_summary,
    _normalize_assessment,
    _normalize_gap,
    _subject_display_string,
    _truncate_artifact_payload,
)


def _render_html(report: ReportData) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    template_dir = Path(__file__).parent.parent.parent / "marivo" / "runtime" / "report_templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("session_report.html")
    return template.render(report=report, phases=ANALYSIS_PHASES)


# ---------------------------------------------------------------------------
# Phase classification
# ---------------------------------------------------------------------------


def test_classify_step_phase():
    assert _classify_step_phase("observe") == "observation"
    assert _classify_step_phase("compare") == "comparison"
    assert _classify_step_phase("decompose") == "decomposition"
    assert _classify_step_phase("attribute") == "decomposition"
    assert _classify_step_phase("detect") == "anomaly_detection"
    assert _classify_step_phase("diagnose") == "anomaly_detection"
    assert _classify_step_phase("test") == "statistical_testing"
    assert _classify_step_phase("validate") == "statistical_testing"
    assert _classify_step_phase("correlate") == "statistical_testing"
    assert _classify_step_phase("forecast") == "forecasting"
    assert _classify_step_phase("unknown_type") == "other"


# ---------------------------------------------------------------------------
# Noise warning filtering
# ---------------------------------------------------------------------------


def test_noise_warning_codes():
    assert "semantic_metadata_unavailable" in _NOISE_WARNING_CODES
    assert "output_summary_unavailable" in _NOISE_WARNING_CODES
    assert "artifact_id_unresolved" not in _NOISE_WARNING_CODES
    assert "provenance_missing" not in _NOISE_WARNING_CODES


def test_noise_warnings_filtered_in_report():
    all_warnings = [
        {"code": "semantic_metadata_unavailable", "message": "..."},
        {"code": "output_summary_unavailable", "message": "..."},
        {"code": "artifact_id_unresolved", "message": "Artifact id could not be resolved."},
    ]
    filtered = [w for w in all_warnings if w.get("code") not in _NOISE_WARNING_CODES]
    assert len(filtered) == 1
    assert filtered[0]["code"] == "artifact_id_unresolved"


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def test_normalize_assessment():
    assessment = {
        "status": "supported",
        "confidence_grade": "high",
        "confidence_rationale_json": "Clear evidence of change",
        "gap_memberships_json": [{"gap_ref": {"gap_id": "g1"}}],
    }
    result = _normalize_assessment(assessment)
    assert result["confidence_rationale"] == "Clear evidence of change"
    assert result["gap_memberships"] == [{"gap_ref": {"gap_id": "g1"}}]
    assert "confidence_rationale_json" in result  # original key still present


def test_normalize_assessment_none():
    assert _normalize_assessment(None) is None


def test_normalize_gap():
    gap = {
        "gap_id": "g1",
        "missing_requirement_json": {
            "requirement_kind": "sample_size",
            "description": "Need more data",
        },
        "satisfiable_by_json": [{"intent": "observe"}],
    }
    result = _normalize_gap(gap)
    assert result["missing_requirement"] == {
        "requirement_kind": "sample_size",
        "description": "Need more data",
    }
    assert result["satisfiable_by"] == [{"intent": "observe"}]


# ---------------------------------------------------------------------------
# Subject display
# ---------------------------------------------------------------------------


def test_subject_display_string():
    assert _subject_display_string({"metric": "dau"}) == "dau"
    assert _subject_display_string({"metric": "dau", "dimension": "region"}) == "dau | by region"
    assert (
        _subject_display_string({"metric": "dau", "dimension": "region", "slice": {"region": "US"}})
        == "dau | by region | region=US"
    )
    assert _subject_display_string({}) == "(unspecified)"


# ---------------------------------------------------------------------------
# Payload helpers (unchanged)
# ---------------------------------------------------------------------------


def test_extract_artifact_summary_scalar():
    payload = {
        "artifact_type": "observation",
        "observation_type": "scalar",
        "value": 42.5,
        "unit": "queries",
    }
    summary = _extract_artifact_summary(payload)
    # value is excluded from summary (it's in the custom rendered table)
    assert "value" not in summary
    assert summary["unit"] == "queries"
    # artifact_type and observation_type are excluded (noise / shown elsewhere)
    assert "artifact_type" not in summary
    assert "observation_type" not in summary


def test_extract_artifact_summary_compare():
    payload = {
        "artifact_type": "comparison",
        "comparison_type": "scalar_delta",
        "absolute_delta": -1200.0,
        "relative_delta": -0.18,
        "direction": "decrease",
        "current_value": 5400.0,
        "baseline_value": 6600.0,
        "comparability": {"status": "comparable", "issues": []},
    }
    summary = _extract_artifact_summary(payload)
    # value keys are excluded (shown in custom comparison table)
    assert "absolute_delta" not in summary
    assert "relative_delta" not in summary
    assert "direction" not in summary
    assert "current_value" not in summary
    assert "baseline_value" not in summary
    # metadata keys are kept
    assert summary["comparison_type"] == "scalar_delta"
    assert summary["comparability_status"] == "comparable"


def test_extract_artifact_summary_segmented():
    payload = {
        "artifact_type": "observation",
        "observation_type": "segmented",
        "segments": [{"key": "US"}, {"key": "EU"}],
    }
    summary = _extract_artifact_summary(payload)
    assert summary["segments_count"] == 2


def test_extract_artifact_summary_detect():
    payload = {
        "artifact_type": "anomaly_candidates",
        "strategy": "period_shift",
        "sensitivity": "aggressive",
        "granularity": "day",
        "scan_summary": {
            "total_candidate_count": 12,
            "returned_candidate_count": 5,
            "eligible_series_count": 8,
        },
        "candidates": [
            {
                "window": {"start": "2026-04-27", "end": "2026-05-20"},
                "slice": {"cluster": "k8sbi-bi1"},
                "candidate_type": "period_shift",
                "candidate_score": 2.46,
                "deviation_pct": 2.46,
                "direction": "up",
                "flag_level": "high",
                "current_value": 443986,
                "baseline_value": 128342,
            },
        ],
    }
    summary = _extract_artifact_summary(payload)
    assert summary["candidate_count_total"] == 12
    assert summary["candidate_count_returned"] == 5
    assert summary["eligible_series_count"] == 8
    assert summary["top_candidate_period"] == "2026-04-27"
    assert summary["top_candidate_slice"] == "cluster=k8sbi-bi1"
    assert summary["top_candidate_score"] == 2.46
    assert summary["top_candidate_direction"] == "up"
    assert summary["top_candidate_flag_level"] == "high"
    assert summary["top_candidate_type"] == "period_shift"
    assert summary["strategy"] == "period_shift"
    assert summary["sensitivity"] == "aggressive"
    assert summary["granularity"] == "day"
    # value keys excluded
    assert "current_value" not in summary
    assert "baseline_value" not in summary
    assert "artifact_type" not in summary


def test_extract_dependency_refs():
    from marivo.runtime.report import _extract_dependency_refs

    # compare step
    refs = _extract_dependency_refs(
        "compare",
        {
            "current_artifact_id": "art_1",
            "baseline_artifact_id": "art_2",
            "current_step_id": "step_1",
            "baseline_step_id": "step_2",
        },
    )
    assert len(refs) == 4
    assert {"role": "current", "artifact_id": "art_1"} in refs
    assert {"role": "baseline", "artifact_id": "art_2"} in refs
    assert {"role": "current", "step_id": "step_1"} in refs
    assert {"role": "baseline", "step_id": "step_2"} in refs

    # decompose step
    refs = _extract_dependency_refs("decompose", {"compare_artifact_id": "art_cmp"})
    assert len(refs) == 1
    assert refs[0]["role"] == "compare"
    assert refs[0]["artifact_id"] == "art_cmp"

    # diagnose step
    refs = _extract_dependency_refs("diagnose", {"detect_step_id": "step_det"})
    assert len(refs) == 1
    assert refs[0]["role"] == "detect"
    assert refs[0]["step_id"] == "step_det"

    # no provenance
    refs = _extract_dependency_refs("observe", None)
    assert refs == []

    # empty provenance
    refs = _extract_dependency_refs("observe", {})
    assert refs == []


def test_truncate_short_payload():
    payload = {"buckets": list(range(10))}
    result = _truncate_artifact_payload(payload)
    assert len(result["buckets"]) == 10
    assert "_buckets_truncated" not in result


def test_truncate_long_payload():
    payload = {"buckets": list(range(200))}
    result = _truncate_artifact_payload(payload)
    assert len(result["buckets"]) == 100
    assert result["_buckets_truncated"] == "Showing 100 of 200"


# ---------------------------------------------------------------------------
# Rendering tests
# ---------------------------------------------------------------------------


def test_render_empty_report():
    report = ReportData(
        session_id="sess_test123",
        goal="Test goal",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:05:00",
    )
    html = _render_html(report)
    assert "sess_test123" in html
    assert "Test goal" in html
    assert "terminated" in html


def test_render_report_with_step():
    step = StepReportData(
        step_id="step_abc",
        step_type="observe",
        summary="observe dau scalar: 4200",
        created_at="2026-05-20T10:01:00",
        reasoning="Check overall DAU trend",
        sql_texts=[
            {
                "sql": "SELECT SUM(cnt) AS value FROM events",
                "engine_type": "duckdb",
                "label": "main_query",
            }
        ],
        provenance={"query_hash": "abcd1234", "engine": "duckdb"},
        artifact_id="art_xyz",
        artifact_type="observation",
        artifact_summary={"unit": "queries"},
        artifact_payload={"value": 4200, "observation_type": "scalar"},
        output_summary={"intent_type": "observe", "artifact_type": "observation"},
        analysis_phase="observation",
        dependency_refs=None,
    )

    report = ReportData(
        session_id="sess_test456",
        goal="Investigate DAU drop",
        lifecycle_status="active",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:01:00",
        steps=[step],
    )

    html = _render_html(report)
    assert "Check overall DAU trend" in html
    assert "SELECT SUM(cnt)" in html
    assert "4200" in html
    assert "observe" in html
    # Reasoning shown as "Why:"
    assert "Why:" in html
    # Phase grouping shown
    assert "Observation" in html


def test_render_report_with_proposition():
    prop = PropositionReportData(
        proposition_id="prop_789",
        proposition_type="change",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={"status": "supported", "confidence_grade": "high"},
        supporting_findings=[{"finding_type": "scalar_observation", "subject": {"metric": "dau"}}],
        gaps=[],
    )

    report = ReportData(
        session_id="sess_test789",
        goal="Test propositions",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:02:00",
        propositions=[prop],
    )

    html = _render_html(report)
    assert "supported" in html
    assert "high" in html
    assert "dau" in html
    # Finding rendered
    assert "scalar_observation" in html


def test_executive_summary_rendered():
    summary = ExecutiveSummaryData(
        goal="Investigate DAU drop",
        metrics_examined=["dau"],
        total_steps=5,
        key_findings=[
            {
                "type": "change",
                "subject": "dau",
                "confidence": "high",
                "rationale": "Clear drop pattern",
            }
        ],
        phase_counts={"observation": 2, "comparison": 1, "decomposition": 2},
        overall_conclusion="Analysis confirmed 1 proposition with sufficient evidence.",
    )

    report = ReportData(
        session_id="sess_test_exec",
        goal="Investigate DAU drop",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:05:00",
        executive_summary=summary,
    )

    html = _render_html(report)
    assert "Executive Summary" in html
    assert "Investigate DAU drop" in html
    assert "Analysis confirmed 1 proposition" in html
    assert "Observation" in html
    assert "Comparison" in html
    assert "Decomposition" in html


def test_subject_json_used_in_proposition():
    """Verify that subject_json is correctly used (not subject key)."""
    prop = PropositionReportData(
        proposition_id="prop_test",
        proposition_type="change",
        subject={"metric": "cache_hit_rate", "dimension": "source", "slice": {"source": "ai"}},
        subject_display="cache_hit_rate | by source | source=ai",
        latest_assessment={"status": "supported", "confidence_grade": "high"},
    )
    report = ReportData(
        session_id="sess_subject_test",
        goal="Test",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:00:00",
        propositions=[prop],
    )
    html = _render_html(report)
    assert "cache_hit_rate" in html
    assert "source=ai" in html


def test_sql_always_visible():
    """SQL should be visible inline, not hidden in <details>."""
    step = StepReportData(
        step_id="step_sql",
        step_type="observe",
        summary="observe metric",
        created_at="2026-05-20T10:00:00",
        reasoning=None,
        sql_texts=[{"sql": "SELECT 1", "engine_type": "duckdb", "label": "main"}],
        provenance=None,
        artifact_id=None,
        artifact_type=None,
        artifact_summary=None,
        artifact_payload=None,
        output_summary=None,
        analysis_phase="observation",
        dependency_refs=None,
    )
    report = ReportData(
        session_id="sess_sql_test",
        goal="Test",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:00:00",
        steps=[step],
    )
    html = _render_html(report)
    # SQL should be in a visible div, not inside <details>
    assert "sql-section" in html
    assert "SELECT 1" in html
