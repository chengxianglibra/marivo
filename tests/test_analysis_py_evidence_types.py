"""Frozen evidence model construction, extra-field rejection, JSON round-trip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from marivo.analysis_py.evidence.types import (
    Assessment,
    ChangeFact,
    Finding,
    OpenAnomaly,
    OpenQuestion,
    Proposition,
    QualitySummary,
    Subject,
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
