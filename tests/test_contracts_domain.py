from __future__ import annotations

from marivo.contracts.evidence import Assessment, Evidence, Finding, Proposition
from marivo.contracts.ids import (
    ArtifactId,
    AssessmentId,
    EvidenceRef,
    FindingId,
    ModelId,
    PropositionId,
    RevisionId,
    SessionId,
    UserId,
)
from marivo.contracts.semantic import ModelSummary, SemanticModel
from marivo.contracts.session import SessionEvent, SessionState


def test_session_event() -> None:
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="created",
        timestamp="2024-01-01T00:00:00Z",
    )
    assert event.session_id == "s1"
    assert event.event_type == "created"
    assert event.actor is None


def test_session_event_with_actor() -> None:
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="closed",
        timestamp="2024-01-01T00:00:00Z",
        actor=UserId("user-1"),
    )
    assert event.actor == "user-1"


def test_session_state() -> None:
    state = SessionState(
        session_id=SessionId("s1"),
        status="active",
        goal="investigate revenue drop",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-02T00:00:00Z",
    )
    assert state.status == "active"
    assert state.goal == "investigate revenue drop"


def test_session_state_optional_goal() -> None:
    state = SessionState(
        session_id=SessionId("s1"),
        status="closed",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-02T00:00:00Z",
    )
    assert state.goal is None


def test_finding() -> None:
    f = Finding(
        finding_id=FindingId("f1"),
        session_id=SessionId("s1"),
        artifact_id=ArtifactId("a1"),
        finding_type="anomaly",
        content={"metric": "revenue", "delta": -0.15},
    )
    assert f.finding_type == "anomaly"
    assert f.invalidated is False
    assert f.proposition_id is None


def test_proposition() -> None:
    p = Proposition(
        proposition_id=PropositionId("p1"),
        session_id=SessionId("s1"),
        identity_key="rev_drop_us_region",
    )
    assert p.identity_key == "rev_drop_us_region"
    assert p.invalidated is False


def test_assessment() -> None:
    a = Assessment(
        assessment_id=AssessmentId("a1"),
        proposition_id=PropositionId("p1"),
        status="confirmed",
    )
    assert a.status == "confirmed"
    assert a.snapshot_seq == 0


def test_evidence_container() -> None:
    e = Evidence(
        ref=EvidenceRef("sha256:abc"),
        findings=[
            Finding(
                finding_id=FindingId("f1"),
                session_id=SessionId("s1"),
                artifact_id=ArtifactId("a1"),
                finding_type="anomaly",
                content={"delta": -0.1},
            )
        ],
    )
    assert e.ref == "sha256:abc"
    assert len(e.findings) == 1
    assert e.proposition is None
    assert e.assessment is None


def test_semantic_model() -> None:
    m = SemanticModel(name="my_model")
    assert m.name == "my_model"
    assert m.model_id is None
    assert m.visibility == "private"


def test_semantic_model_full() -> None:
    m = SemanticModel(
        model_id=ModelId(1),
        name="my_model",
        revision=RevisionId("v1"),
        description="test model",
        visibility="public",
        owner=UserId("user-1"),
    )
    assert m.model_id == 1
    assert m.revision == "v1"


def test_model_summary() -> None:
    s = ModelSummary(
        model_id=ModelId(1),
        name="my_model",
        updated_at="2024-01-01",
    )
    assert s.model_id == 1
    assert s.updated_at == "2024-01-01"
