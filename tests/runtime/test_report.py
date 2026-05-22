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
    _deduplicate_propositions,
    _extract_artifact_summary,
    _fmt_coeff,
    _fmt_delta,
    _fmt_pct,
    _fmt_pvalue,
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
    env.filters["fmt_pct"] = _fmt_pct
    env.filters["fmt_pvalue"] = _fmt_pvalue
    env.filters["fmt_coeff"] = _fmt_coeff
    env.filters["fmt_delta"] = _fmt_delta
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
    assert _classify_step_phase("diagnose") == "diagnosis"
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
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "payload": {
            "series": [],
            "scope": {
                "current_value": 5400.0,
                "baseline_value": 6600.0,
                "delta_abs": -1200.0,
                "delta_pct": -0.18,
                "direction": "decrease",
            },
        },
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
    assert summary["shape"] == "scalar_delta"
    assert summary["comparability_status"] == "comparable"


def test_extract_artifact_summary_segmented():
    payload = {
        "artifact_type": "observation",
        "observation_type": "segmented",
        "metric": "total_query_count",
        "dimensions": ["platform"],
        "segments": [
            {"keys": {"platform": "web"}, "value": 100},
            {"keys": {"platform": "mobile"}, "value": 200},
        ],
    }
    summary = _extract_artifact_summary(payload)
    assert summary["segments_count"] == 2
    assert summary["metric"] == "total_query_count"
    assert summary["dimensions"] == "platform"


def test_extract_artifact_summary_detect():
    payload = {
        "artifact_family": "candidate_set",
        "shape": "period_shift_candidates",
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": ["art_delta_001"],
            "strategy": "period_shift",
        },
        "payload": {
            "scan_summary": {
                "scanned_series_count": 8,
                "total_candidate_count": 12,
            },
            "truncation": {
                "returned_candidate_count": 5,
                "total_candidate_count": 12,
                "truncated": True,
            },
            "items": [
                {
                    "window": {"start": "2026-04-27", "end": "2026-05-20"},
                    "keys": {"cluster": "k8sbi-bi1"},
                    "score": 2.46,
                    "delta_pct": 2.46,
                    "direction": "increase",
                    "value": 443986,
                    "baseline_value": 128342,
                },
            ],
        },
    }
    summary = _extract_artifact_summary(payload)
    assert summary["candidate_count_total"] == 12
    assert summary["candidate_count_returned"] == 5
    assert summary["scanned_series_count"] == 8
    assert summary["top_candidate_period"] == "2026-04-27"
    assert summary["top_candidate_slice"] == "cluster=k8sbi-bi1"
    assert summary["top_candidate_score"] == 2.46
    assert summary["top_candidate_direction"] == "increase"
    assert summary["strategy"] == "period_shift"
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


def test_sql_collapsed_by_default():
    """SQL should be inside a collapsed <details> element."""
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
    # SQL should be inside <details> (collapsed by default, no 'open' attribute)
    assert "sql-details" in html
    assert "SELECT 1" in html
    assert "<details" in html


# ---------------------------------------------------------------------------
# Formatting filters
# ---------------------------------------------------------------------------


def test_fmt_pct():
    assert _fmt_pct(5.3729) == "537.3%"
    assert _fmt_pct(0.366) == "36.6%"
    assert _fmt_pct(0) == "0.0%"
    assert _fmt_pct(None) == "—"
    assert _fmt_pct("not a number") == "not a number"


def test_fmt_pvalue():
    assert _fmt_pvalue(0.01458) == "0.015"
    assert _fmt_pvalue(0.001) == "<0.01"
    assert _fmt_pvalue(0.248) == "0.248"
    assert _fmt_pvalue(None) == "—"


def test_fmt_coeff():
    assert _fmt_coeff(0.6155) == "0.62"
    assert _fmt_coeff(-0.3179) == "-0.32"
    assert _fmt_coeff(None) == "—"


def test_fmt_delta():
    assert _fmt_delta(0.22) == "+0.22"
    assert _fmt_delta(-0.04) == "-0.04"
    assert _fmt_delta(0) == "+0.00"
    assert _fmt_delta(None) == "—"


# ---------------------------------------------------------------------------
# Time-series artifact summary
# ---------------------------------------------------------------------------


def test_extract_artifact_summary_time_series():
    payload = {
        "artifact_type": "observation",
        "observation_type": "time_series",
        "metric": "cache_hit_rate",
        "granularity": "day",
        "series": [
            {"window": {"start": "2026-05-06", "end": "2026-05-07"}, "value": 0.15},
            {"window": {"start": "2026-05-07", "end": "2026-05-08"}, "value": 0.18},
            {"window": {"start": "2026-05-08", "end": "2026-05-09"}, "value": 0.12},
        ],
    }
    summary = _extract_artifact_summary(payload)
    assert summary["series_count"] == 3
    assert summary["series_start"] == "2026-05-06"
    assert summary["series_end"] == "2026-05-08"
    assert summary["primary_result"] == "0.12"
    assert summary["metric"] == "cache_hit_rate"
    assert summary["granularity"] == "day"


def test_extract_artifact_summary_time_series_null_last_value():
    payload = {
        "artifact_type": "observation",
        "observation_type": "time_series",
        "series": [
            {"window": {"start": "2026-05-06"}, "value": 0.15},
            {"window": {"start": "2026-05-07"}, "value": None},
        ],
    }
    summary = _extract_artifact_summary(payload)
    # Should fall back to last non-None value
    assert summary["primary_result"] == "0.15"


# ---------------------------------------------------------------------------
# Proposition deduplication
# ---------------------------------------------------------------------------


def test_deduplicate_propositions_by_identity_key():
    prop_a = PropositionReportData(
        proposition_id="prop_1",
        proposition_type="anomaly",
        subject={"metric": "dau", "identity_key": "anomaly|dau|cluster=k8sai"},
        subject_display="dau | cluster=k8sai",
        latest_assessment={"status": "supported", "created_at": "2026-05-20T10:00:00"},
    )
    prop_b = PropositionReportData(
        proposition_id="prop_2",
        proposition_type="anomaly",
        subject={"metric": "dau", "identity_key": "anomaly|dau|cluster=k8sai"},
        subject_display="dau | cluster=k8sai",
        latest_assessment={"status": "supported", "created_at": "2026-05-20T10:05:00"},
    )
    result = _deduplicate_propositions([prop_a, prop_b])
    assert len(result) == 1
    # Should keep the one with the later assessment
    assert result[0].proposition_id == "prop_2"


def test_deduplicate_propositions_by_composite_key():
    prop_a = PropositionReportData(
        proposition_id="prop_1",
        proposition_type="anomaly",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={"status": "supported", "created_at": "2026-05-20T10:00:00"},
        supporting_findings=[{"finding_id": "f1"}],
    )
    prop_b = PropositionReportData(
        proposition_id="prop_2",
        proposition_type="anomaly",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={"status": "supported", "created_at": "2026-05-20T10:05:00"},
        supporting_findings=[{"finding_id": "f1"}],
    )
    # Different proposition_type → no dedup
    prop_c = PropositionReportData(
        proposition_id="prop_3",
        proposition_type="change",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={"status": "supported"},
    )
    result = _deduplicate_propositions([prop_a, prop_b, prop_c])
    assert len(result) == 2


# ---------------------------------------------------------------------------
# DAG edge deduplication
# ---------------------------------------------------------------------------


def test_dag_edge_dedup():
    from marivo.runtime.report import _build_dag, _extract_dependency_refs

    # Build dependency refs from provenance (same as generate_session_report does)
    step_a = StepReportData(
        step_id="step_a",
        step_type="observe",
        summary="observe dau",
        created_at="2026-05-20T10:00:00",
        reasoning=None,
        sql_texts=None,
        provenance=None,
        artifact_id="art_a",
        artifact_type="observation",
        artifact_summary={"metric": "dau", "granularity": "day"},
        artifact_payload=None,
        output_summary=None,
        analysis_phase="observation",
        dependency_refs=None,
    )
    step_b = StepReportData(
        step_id="step_b",
        step_type="observe",
        summary="observe dau baseline",
        created_at="2026-05-20T10:01:00",
        reasoning=None,
        sql_texts=None,
        provenance=None,
        artifact_id="art_b",
        artifact_type="observation",
        artifact_summary={"metric": "dau", "granularity": "day"},
        artifact_payload=None,
        output_summary=None,
        analysis_phase="observation",
        dependency_refs=None,
    )
    compare_provenance = {
        "current_artifact_id": "art_a",
        "baseline_artifact_id": "art_b",
        "current_step_id": "step_a",
        "baseline_step_id": "step_b",
    }
    step_c = StepReportData(
        step_id="step_c",
        step_type="compare",
        summary="compare dau",
        created_at="2026-05-20T10:02:00",
        reasoning=None,
        sql_texts=None,
        provenance=compare_provenance,
        artifact_id="art_c",
        artifact_type="comparison",
        artifact_summary={"metric": "dau"},
        artifact_payload=None,
        output_summary=None,
        analysis_phase="comparison",
        dependency_refs=_extract_dependency_refs("compare", compare_provenance),
    )
    steps = [step_a, step_b, step_c]
    nodes, edges = _build_dag(steps)
    # compare produces both artifact_id and step_id refs that resolve to the same edges
    # Should be deduplicated: only 2 unique edges (current + baseline), not 4
    edge_keys = [(e.source_step_id, e.target_step_id, e.role) for e in edges]
    assert len(edge_keys) == len(set(edge_keys)), f"Duplicate edges found: {edge_keys}"
    assert len(edges) == 2


# ---------------------------------------------------------------------------
# Rationale rendering
# ---------------------------------------------------------------------------


def test_rationale_dict_rendered():
    prop = PropositionReportData(
        proposition_id="prop_rat",
        proposition_type="change",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={
            "status": "supported",
            "confidence_grade": "high",
            "confidence_rationale": {
                "evidence_sufficiency": "strong",
                "evidence_consistency": "consistent",
                "rule_coverage": "partial",
                "data_quality_impact": "none",
            },
        },
    )
    report = ReportData(
        session_id="sess_rat_test",
        goal="Test",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:00:00",
        propositions=[prop],
    )
    html = _render_html(report)
    # Should render as a list, not raw dict
    assert "Evidence sufficiency" in html
    assert "strong" in html
    assert "Evidence consistency" in html
    assert "rationale-list" in html


# ---------------------------------------------------------------------------
# Gap rendering
# ---------------------------------------------------------------------------


def test_gap_with_title_rendered():
    prop = PropositionReportData(
        proposition_id="prop_gap",
        proposition_type="change",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={"status": "supported", "confidence_grade": "medium"},
        gaps=[
            {
                "gap_id": "gap_1",
                "title": "Insufficient baseline data",
                "description": "Need at least 14 days of baseline",
                "status": "open",
                "blocking": True,
            }
        ],
    )
    report = ReportData(
        session_id="sess_gap_test",
        goal="Test",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:00:00",
        propositions=[prop],
    )
    html = _render_html(report)
    assert "Insufficient baseline data" in html
    assert "Need at least 14 days of baseline" in html


# ---------------------------------------------------------------------------
# Finding payload rendering
# ---------------------------------------------------------------------------


def test_finding_payload_anomaly_candidate():
    prop = PropositionReportData(
        proposition_id="prop_find",
        proposition_type="change",
        subject={"metric": "dau"},
        subject_display="dau",
        latest_assessment={"status": "supported", "confidence_grade": "high"},
        supporting_findings=[
            {
                "finding_type": "anomaly_candidate",
                "subject": {"metric": "dau"},
                "payload": {
                    "baseline_value": 0.04,
                    "current_value": 0.24,
                    "deviation_pct": 5.0,
                    "flag_level": "high",
                },
            }
        ],
    )
    report = ReportData(
        session_id="sess_find_test",
        goal="Test",
        lifecycle_status="terminated",
        created_at="2026-05-20T10:00:00",
        updated_at="2026-05-20T10:00:00",
        propositions=[prop],
    )
    html = _render_html(report)
    # Should render inline summary, not raw JSON
    assert "Baseline" in html
    assert "Current" in html
    assert "500.0%" in html  # 5.0 * 100 formatted by fmt_pct
