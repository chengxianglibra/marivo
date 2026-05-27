"""C1/C2 followup generation conformance."""

from __future__ import annotations

import pytest

from marivo.analysis_py.errors import FollowupGenerationRuleViolatedError
from marivo.analysis_py.evidence.followups import (
    generate_followups,
    GenerationContext,
)
from marivo.analysis_py.followups import BlockingIssue, FollowupAction


def _ctx(
    *,
    artifact_id: str = "art_1",
    family: str = "metric_frame",
    semantic_kind: str = "time_series",
    blocking_issues: list[BlockingIssue] | None = None,
) -> GenerationContext:
    return GenerationContext(
        source_artifact_id=artifact_id,
        source_family=family,
        source_semantic_kind=semantic_kind,
        blocking_issues=blocking_issues or [],
    )


def test_c1_metric_time_series_emits_whitelist() -> None:
    actions = generate_followups(_ctx(family="metric_frame", semantic_kind="time_series"))
    operators = sorted({a.operator for a in actions if a.category == "dag_continuation"})
    assert operators == ["assess_quality", "discover", "discover", "forecast"][:4] or set(operators) == {
        "assess_quality",
        "discover",
        "forecast",
    }
    objectives = {
        a.params.get("objective") for a in actions if a.operator == "discover"
    }
    assert objectives == {"point_anomalies", "interesting_windows"}


def test_c1_metric_scalar_only_emits_assess_quality() -> None:
    actions = generate_followups(_ctx(family="metric_frame", semantic_kind="scalar"))
    assert [a.operator for a in actions] == ["assess_quality"]
    assert all(a.category == "dag_continuation" for a in actions)
    assert all(a.source_issue_id is None for a in actions)


def test_c1_delta_emits_discover_and_assess_quality() -> None:
    actions = generate_followups(
        _ctx(family="delta_frame", semantic_kind="time_series")
    )
    operators = {a.operator for a in actions}
    assert "assess_quality" in operators
    assert "discover" in operators
    objectives = {a.params.get("objective") for a in actions if a.operator == "discover"}
    assert {"driver_axes", "period_shifts", "interesting_slices"} <= objectives


def test_c1_delta_scalar_emits_no_period_shift() -> None:
    actions = generate_followups(_ctx(family="delta_frame", semantic_kind="scalar"))
    objectives = {a.params.get("objective") for a in actions if a.operator == "discover"}
    assert "period_shifts" not in objectives
    assert "driver_axes" in objectives


def test_c2_null_rate_high_emits_remediation() -> None:
    issue = BlockingIssue(
        issue_id="iss_1",
        kind="null_rate_high",
        severity="warning",
        message="too many nulls",
    )
    actions = generate_followups(
        _ctx(family="metric_frame", semantic_kind="time_series", blocking_issues=[issue])
    )
    remediations = [a for a in actions if a.category == "quality_remediation"]
    assert any(
        a.operator == "transform" and a.params.get("op") == "impute_nulls"
        for a in remediations
    )
    assert all(a.source_issue_id == "iss_1" for a in remediations)


def test_c2_evidence_partial_emits_retry() -> None:
    issue = BlockingIssue(
        issue_id="iss_2",
        kind="evidence_partial",
        severity="warning",
        message="seeding failed",
    )
    actions = generate_followups(
        _ctx(family="delta_frame", semantic_kind="scalar", blocking_issues=[issue])
    )
    retry = [a for a in actions if a.category == "quality_remediation" and a.kind == "adjust_policy"]
    assert len(retry) == 1
    assert retry[0].source_issue_id == "iss_2"


def test_c2_sample_size_low_emits_no_remediation() -> None:
    issue = BlockingIssue(
        issue_id="iss_3",
        kind="sample_size_low",
        severity="warning",
        message="not enough rows",
    )
    actions = generate_followups(
        _ctx(family="metric_frame", semantic_kind="time_series", blocking_issues=[issue])
    )
    remediations = [a for a in actions if a.category == "quality_remediation"]
    assert remediations == []


def test_action_id_is_replay_stable() -> None:
    a1 = generate_followups(_ctx(family="metric_frame", semantic_kind="scalar"))
    a2 = generate_followups(_ctx(family="metric_frame", semantic_kind="scalar"))
    assert [x.action_id for x in a1] == [x.action_id for x in a2]


def test_forbidden_family_raises_violation() -> None:
    with pytest.raises(FollowupGenerationRuleViolatedError):
        generate_followups(_ctx(family="quality_report", semantic_kind="scalar"))
