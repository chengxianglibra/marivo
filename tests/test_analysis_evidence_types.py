"""Frozen evidence model construction, extra-field rejection, JSON round-trip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from marivo.analysis.evidence.types import (
    Assessment,
    AssociationSummary,
    AttributedDriver,
    BlockedFollowup,
    ChangeFact,
    Finding,
    ForecastSummary,
    LagSweepSummary,
    OpenAnomaly,
    OpenQuestion,
    Proposition,
    QualitySummary,
    Subject,
    TestedHypothesis,
    TimeWindow,
    TriggeredByFollowup,
)


def test_subject_immutable_round_trip() -> None:
    s = Subject(metric="dau", entity=None, slice={}, grain="day", analysis_axis="change")
    payload = s.model_dump(mode="json")
    restored = Subject.model_validate(payload)
    assert restored == s
    with pytest.raises(ValidationError):
        s.metric = "dau2"  # type: ignore[misc]


def test_subject_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Subject(metric="dau", slice={}, analysis_axis="change", junk=1)  # type: ignore[call-arg]


def test_finding_round_trip() -> None:
    f = Finding(
        finding_id="fnd_abc",
        finding_type="delta",
        artifact_id="art_xyz",
        session_id="sess_1",
        subject=Subject(metric="dau", slice={}, analysis_axis="change"),
        canonical_item_key="value",
        observed_window=None,
        quality_status="ready",
        payload={"direction": "increase", "magnitude": 100.0},
        committed_at=datetime.now(UTC),
    )
    payload = f.model_dump(mode="json")
    restored = Finding.model_validate(payload)
    assert restored == f


def test_quality_summary_optional_fields() -> None:
    q = QualitySummary(coverage=0.95, null_rate=0.05, sample_size=42)
    assert q.coverage == 0.95


def test_proposition_change_kind() -> None:
    p = Proposition(
        proposition_id="prop_1",
        session_id="sess_1",
        proposition_type="change",
        origin_kind="system_seeded",
        derivation_version="v1",
        subject_key="abc123",
        payload={"change_kind": "scalar_change"},
        seed_finding_refs=["fnd_a"],
        created_at=datetime.now(UTC),
    )
    assert p.proposition_type == "change"


def test_assessment_latest_only() -> None:
    a = Assessment(
        snapshot_id="ass_1",
        proposition_id="prop_1",
        session_id="sess_1",
        status="validated",
        confidence=0.9,
        confidence_basis="latest_test_p_lt_alpha",
        payload={},
        created_at=datetime.now(UTC),
        is_latest=True,
    )
    assert a.is_latest is True


def test_change_fact_projection() -> None:
    fact = ChangeFact(
        id="prop_1",
        kind="change",
        subject=Subject(metric="dau", slice={}, analysis_axis="change"),
        window=None,
        status="validated",
        confidence=0.9,
        confidence_basis="latest_test_p_lt_alpha",
        source_refs=["art_xyz"],
        latest_assessment_id="ass_1",
        direction="increase",
        magnitude=100.0,
        comparison_window=None,
        comparison_basis="left_vs_right",
        dimension_keys=None,
    )
    assert fact.kind == "change"


def test_open_anomaly_kind() -> None:
    o = OpenAnomaly(
        id="prop_2",
        kind="anomaly",
        subject=Subject(metric="dau", slice={}, analysis_axis="time"),
        window=None,
        status="pending",
        confidence=None,
        confidence_basis="",
        source_refs=[],
        latest_assessment_id="",
    )
    assert o.kind == "anomaly"


def test_open_question_reason_enum() -> None:
    o = OpenQuestion(
        id="open_1",
        kind="question",
        subject=Subject(metric="dau", slice={}, analysis_axis="change"),
        window=None,
        status="pending",
        confidence=None,
        confidence_basis="",
        source_refs=[],
        latest_assessment_id="",
        reason="reopened_gap",
    )
    assert o.reason == "reopened_gap"


def test_triggered_by_followup_via_enum() -> None:
    t = TriggeredByFollowup(
        action_id="a1",
        source_artifact_id="art_xyz",
        via="run_followup",
    )
    assert t.via == "run_followup"


def test_attributed_driver_construction() -> None:
    fact = AttributedDriver(
        id="prop_1",
        subject=Subject(metric="dau", analysis_axis="decomposition"),
        status="validated",
        confidence_basis="driver_role_primary_driver",
        latest_assessment_id="ass_1",
        dimension="country",
        dimension_keys={"country": "us"},
        contribution_value=12.0,
        contribution_share=0.6,
        contribution_role="primary_driver",
        scope_change_id="prop_chg_1",
    )
    assert fact.kind == "driver"
    assert fact.dimension == "country"
    assert fact.contribution_role == "primary_driver"


def test_tested_hypothesis_construction() -> None:
    fact = TestedHypothesis(
        id="prop_th1",
        subject=Subject(metric="dau", analysis_axis="scalar"),
        status="validated",
        confidence_basis="test_p_lt_alpha",
        latest_assessment_id="ass_2",
        hypothesis_family="difference",
        alternative="two_sided",
        method_family="welch_t",
        alpha=0.05,
        p_value=0.02,
        reject_null=True,
    )
    assert fact.kind == "tested_hypothesis"
    assert fact.reject_null is True


def test_forecast_summary_construction() -> None:
    fact = ForecastSummary(
        id="prop_fc1",
        subject=Subject(metric="dau", analysis_axis="forecast"),
        status="pending",
        confidence_basis="forecast_pending_actual",
        latest_assessment_id="ass_3",
        forecast_window=TimeWindow(field="ds", start="2025-01-08", end="2025-01-15"),
        horizon_index=1,
        forecast_kind="interval",
        prediction_interval=[1050.0, 1150.0],
    )
    assert fact.kind == "forecast"
    assert fact.forecast_kind == "interval"


def test_association_summary_construction() -> None:
    fact = AssociationSummary(
        id="prop_as1",
        subject=Subject(metric=None, analysis_axis="correlation"),
        status="validated",
        confidence_basis="association_p_lt_alpha",
        latest_assessment_id="ass_4",
        left_subject={"metric": "dau"},
        right_subject={"metric": "revenue"},
        method_family="pearson",
        coefficient=0.71,
        lag_mode="single",
        lag=None,
        join_basis="window_bucket",
    )
    assert fact.kind == "association"
    assert fact.coefficient == 0.71


def test_lag_sweep_summary_construction() -> None:
    lag_sweep = LagSweepSummary(grid_min=-7.0, grid_max=7.0, step=1.0, selected_lag=-1.0)
    assert lag_sweep.selected_lag == -1.0


def test_blocked_followup_construction() -> None:
    blocked = BlockedFollowup(
        action_id="act_1",
        reason="missing_input_artifact",
        operator="compare",
        source_artifact_id="art_x",
        blocking_issue_kind="comparability_incompatible",
    )
    assert blocked.reason == "missing_input_artifact"


def test_evidence_namespace_reexports_typed_facts() -> None:
    import marivo.analysis as ap

    assert hasattr(ap.evidence, "ChangeFact")
    assert hasattr(ap.evidence, "AttributedDriver")
    assert hasattr(ap.evidence, "TestedHypothesis")
    assert hasattr(ap.evidence, "ForecastSummary")
    assert hasattr(ap.evidence, "AssociationSummary")
    assert hasattr(ap.evidence, "OpenAnomaly")
    assert hasattr(ap.evidence, "OpenQuestion")
    assert hasattr(ap.evidence, "BlockedFollowup")
    assert hasattr(ap.evidence, "EvidenceTrace")
    assert hasattr(ap.evidence, "Finding")
    assert hasattr(ap.evidence, "Proposition")
    assert hasattr(ap.evidence, "Assessment")
    assert hasattr(ap.evidence, "Subject")
    assert hasattr(ap.evidence, "TimeWindow")
    assert hasattr(ap, "FollowupAction")
    assert hasattr(ap, "BlockingIssue")
