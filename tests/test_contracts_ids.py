from __future__ import annotations

from app.contracts.ids import (
    Action,
    ActionProposalId,
    ArtifactId,
    AssessmentId,
    AttemptId,
    CacheKey,
    DatasetName,
    DatasourceId,
    EngineId,
    EvidenceRef,
    FindingId,
    GapId,
    InferenceRecordId,
    MetricName,
    ModelId,
    PropositionId,
    RelationshipName,
    ResourceId,
    RevisionId,
    RouteId,
    SessionId,
    StepId,
    UserId,
)


def test_session_ids_are_str_at_runtime() -> None:
    sid = SessionId("sess-001")
    assert isinstance(sid, str)
    assert sid == "sess-001"


def test_model_id_is_int_at_runtime() -> None:
    mid = ModelId(42)
    assert isinstance(mid, int)
    assert mid == 42


def test_revision_id_is_str_at_runtime() -> None:
    rid = RevisionId("abc123")
    assert isinstance(rid, str)
    assert rid == "abc123"


def test_all_str_ids_construct_from_str() -> None:
    str_ids = [
        SessionId("s"),
        StepId("step"),
        ArtifactId("art"),
        AttemptId("att"),
        FindingId("f"),
        PropositionId("p"),
        AssessmentId("a"),
        ActionProposalId("ap"),
        GapId("g"),
        InferenceRecordId("ir"),
        RevisionId("r"),
        DatasetName("ds"),
        MetricName("m"),
        RelationshipName("rel"),
        DatasourceId("d"),
        EngineId("e"),
        RouteId("rt"),
        UserId("u"),
        Action("act"),
        ResourceId("res"),
        EvidenceRef("eref"),
        CacheKey("ck"),
    ]
    for typed_id in str_ids:
        assert isinstance(typed_id, str)


def test_int_ids_construct_from_int() -> None:
    int_ids = [ModelId(1)]
    for typed_id in int_ids:
        assert isinstance(typed_id, int)
