from __future__ import annotations

from marivo.analysis.evidence.followups import GenerationContext, generate_followups
from marivo.analysis.followups import BlockingIssue


def _ctx(
    family: str,
    kind: str = "scalar",
    issues: list[BlockingIssue] | None = None,
) -> GenerationContext:
    return GenerationContext(
        source_artifact_id="art_x",
        source_family=family,
        source_semantic_kind=kind,
        blocking_issues=issues or [],
    )


def test_attribution_frame_c1_only_assess_quality() -> None:
    actions = generate_followups(_ctx("attribution_frame"))
    operators = [(a.operator, a.params) for a in actions if a.category == "dag_continuation"]
    assert operators == [("assess_quality", {})]


def test_candidate_set_c1_only_assess_quality() -> None:
    actions = generate_followups(_ctx("candidate_set"))
    operators = [(a.operator, a.params) for a in actions if a.category == "dag_continuation"]
    assert operators == [("assess_quality", {})]


def test_forecast_frame_c1_only_assess_quality() -> None:
    # Current public contract: ForecastFrame only gets assess_quality today.
    actions = generate_followups(_ctx("forecast_frame"))
    operators = [a.operator for a in actions if a.category == "dag_continuation"]
    assert operators == ["assess_quality"]


def test_quality_report_c1_is_empty() -> None:
    actions = generate_followups(_ctx("quality_report"))
    assert [a for a in actions if a.category == "dag_continuation"] == []


def test_c2_definition_drift_emits_window_transform() -> None:
    issue = BlockingIssue(
        issue_id="iss_drift",
        kind="definition_drift_detected",
        severity="warning",
        source_refs=["art_x"],
        message="metric definition changed",
        payload={"definition_valid_range": {"start": "2025-01-01", "end": "2025-02-01"}},
    )
    actions = generate_followups(_ctx("metric_frame", "scalar", [issue]))
    quality = [a for a in actions if a.category == "quality_remediation"]
    assert any(a.operator == "transform" and a.params.get("op") == "window" for a in quality)


def test_c2_outlier_winsorize_emits_when_policy_provided() -> None:
    issue = BlockingIssue(
        issue_id="iss_out",
        kind="outlier_winsorize_recommended",
        severity="warning",
        source_refs=["art_x"],
        message="outliers detected",
        payload={"suggested_policy": {"upper": 0.99, "lower": 0.01}},
    )
    actions = generate_followups(_ctx("metric_frame", "scalar", [issue]))
    assert any(
        a.operator == "transform" and a.params.get("op") == "winsorize"
        for a in actions
        if a.category == "quality_remediation"
    )


def test_c2_sample_size_low_emits_nothing() -> None:
    issue = BlockingIssue(
        issue_id="iss_n",
        kind="sample_size_low",
        severity="warning",
        source_refs=["art_x"],
        message="sample size too small",
    )
    actions = generate_followups(_ctx("metric_frame", "scalar", [issue]))
    assert [a for a in actions if a.category == "quality_remediation"] == []
