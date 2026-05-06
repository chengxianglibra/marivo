from __future__ import annotations

from app.contracts import (
    ArtifactId,
    Assessment,
    AssessmentId,
    CacheKey,
    DatasourceId,
    DomainError,
    Evidence,
    EvidenceRef,
    Finding,
    FindingId,
    Granularity,
    LogicalQuery,
    ModelId,
    ModelSummary,
    Proposition,
    PropositionId,
    QueryResult,
    RevisionId,
    SemanticModel,
    SessionEvent,
    SessionId,
    SessionState,
    StepId,
    TimeScope,
    TimeScopeAsOf,
    TimeScopeLatestAvailable,
    TimeScopeRange,
    TimeScopeSnapshotNow,
    UserId,
)


def test_key_ids_importable() -> None:
    assert SessionId("s") == "s"
    assert ModelId(1) == 1
    assert StepId("step") == "step"
    assert UserId("u") == "u"
    assert AssessmentId("a") == "a"
    assert ArtifactId("art") == "art"
    assert CacheKey("k") == "k"
    assert DatasourceId("ds") == "ds"
    assert EvidenceRef("ref") == "ref"
    assert FindingId("f") == "f"
    assert PropositionId("p") == "p"
    assert RevisionId("r") == "r"


def test_key_value_objects_importable() -> None:
    ts = TimeScopeRange(start="2024-01-01", end="2024-02-01")
    assert ts.kind == "range"

    assert TimeScopeSnapshotNow().kind == "snapshot_now"
    assert TimeScopeLatestAvailable().kind == "latest_available"
    assert TimeScopeAsOf(at="2024-01-01").kind == "as_of"

    # TimeScope union accepts all variants
    _: list[TimeScope] = [
        ts,
        TimeScopeSnapshotNow(),
        TimeScopeLatestAvailable(),
        TimeScopeAsOf(at="2024-01-01"),
    ]

    # Granularity is a Literal type alias; confirm it is importable and usable as annotation
    day: Granularity = "day"
    assert day == "day"

    q = LogicalQuery(sql="SELECT 1")
    assert q.sql == "SELECT 1"

    qr = QueryResult(columns=["a"], rows=[{"a": 1}], row_count=1)
    assert qr.row_count == 1


def test_domain_types_importable() -> None:
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="created",
        timestamp="2024-01-01T00:00:00Z",
    )
    assert event.event_type == "created"

    state = SessionState(
        session_id=SessionId("s1"),
        status="active",
        created_at="2024-01-01",
        updated_at="2024-01-01",
    )
    assert state.status == "active"

    finding = Finding(
        finding_id=FindingId("f1"),
        session_id=SessionId("s1"),
        artifact_id=ArtifactId("a1"),
        finding_type="anomaly",
        content={},
    )
    assert finding.finding_type == "anomaly"

    prop = Proposition(
        proposition_id=PropositionId("p1"),
        session_id=SessionId("s1"),
        identity_key="key",
    )
    assert prop.identity_key == "key"

    assessment = Assessment(
        assessment_id=AssessmentId("a1"),
        proposition_id=PropositionId("p1"),
        status="confirmed",
    )
    assert assessment.status == "confirmed"

    evidence = Evidence(ref=EvidenceRef("sha256:abc"), findings=[finding])
    assert evidence.ref == "sha256:abc"

    sm = SemanticModel(name="m1")
    assert sm.name == "m1"

    ms = ModelSummary(model_id=ModelId(1), name="m1")
    assert ms.name == "m1"


def test_errors_importable() -> None:
    err = DomainError(code="not_found", message="missing")
    assert str(err) == "missing"
