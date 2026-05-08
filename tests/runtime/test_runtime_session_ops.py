"""Tests for MarivoRuntime session lifecycle and semantic model operations."""

from __future__ import annotations

from app.contracts.errors import NotFoundError
from app.contracts.ids import (
    Action,
    ArtifactId,
    CacheKey,
    EvidenceRef,
    ModelId,
    ResourceId,
    RevisionId,
    SessionId,
    UserId,
)
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionEvent, SessionState
from app.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    LogicalQuery,
    QueryResult,
    SourceRef,
    SourceSchema,
    TelemetryEvent,
)
from app.core.engine import CoreEngine
from app.core.session.rebuild import rebuild_session_state
from app.runtime.runtime import MarivoRuntime

# --- Stub port implementations ---


class StubModelStore:
    def __init__(self) -> None:
        self._saved: list[SemanticModel] = []

    def get(self, selector: object) -> SemanticModel | None:
        return None

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId:
        self._saved.append(model)
        return ModelId(42)

    def list(self, query: object) -> list[ModelSummary]:
        return []


class RecordingSessionStore:
    """Session store that records events and can load them back."""

    def __init__(self) -> None:
        self._events: dict[str, list[SessionEvent]] = {}

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        key = str(session_id)
        if key not in self._events:
            self._events[key] = []
        self._events[key].append(event)

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        key = str(session_id)
        if key not in self._events:
            raise NotFoundError(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id!r} not found",
            )
        return list(self._events[key])

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        states: list[SessionState] = []
        for events in self._events.values():
            if events and events[0].event_type == "session_created" and events[0].actor == owner:
                states.append(rebuild_session_state(events))
        return states


class StubEvidenceStore:
    def write(self, evidence: object) -> EvidenceRef:
        return EvidenceRef("evidence.stub")

    def read(self, ref: object) -> object:
        raise KeyError(ref)


class StubDataSource:
    def execute(self, query: LogicalQuery) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        return SourceSchema(columns=[])


class StubCacheStore:
    def get(self, key: CacheKey) -> CacheValue | None:
        return None

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        pass


class StubAuthZ:
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)


class StubAuditLog:
    def record(self, entry: AuditEntry) -> None:
        pass


class StubTelemetry:
    def emit(self, event: TelemetryEvent) -> None:
        pass


class StubRuntimeConfig:
    def get(self, key: str) -> str | None:
        return None


class StubArtifactStore:
    def insert_artifact(
        self,
        session_id,
        step_id,
        artifact_type,
        name,
        content,
        *,
        lifecycle="committed",
        artifact_schema_version=None,
    ):
        return ArtifactId("art-stub")

    def commit_artifact_with_extraction(
        self,
        session_id,
        step_id,
        artifact_type,
        name,
        content,
        *,
        step_type=None,
        artifact_schema_version=None,
    ):
        return ArtifactId("art-stub")

    def resolve_artifact_for_ref(self, session_id, step_id):
        return None

    def resolve_artifact_id_for_step(self, session_id, step_id):
        return None

    def resolve_artifact_with_id(self, session_id, step_id):
        return None

    def list_artifacts(self, session_id):
        return []


class StubStepStore:
    def insert_step(
        self,
        step_id,
        session_id,
        step_type,
        summary,
        result,
        *,
        provenance=None,
        semantic_metadata=None,
    ):
        pass

    def list_steps(self, session_id):
        return []


def _make_runtime(session_store: RecordingSessionStore | None = None) -> MarivoRuntime:
    from app.runtime.ports import RuntimePorts

    ports = RuntimePorts(
        model_store=StubModelStore(),
        session_store=session_store or RecordingSessionStore(),
        evidence_store=StubEvidenceStore(),
        data_source=StubDataSource(),
        cache_store=StubCacheStore(),
        authz=StubAuthZ(),
        audit_log=StubAuditLog(),
        telemetry=StubTelemetry(),
        runtime_config=StubRuntimeConfig(),
        artifact_store=StubArtifactStore(),
        step_store=StubStepStore(),
    )
    core = CoreEngine()
    rt = MarivoRuntime(ports=ports, core=core)
    # Session lifecycle tests don't need svc; intent dispatch tests
    # wire it separately.
    return rt


# --- Session lifecycle tests (ports-based) ---


def test_create_session_returns_session_state() -> None:
    rt = _make_runtime()
    result = rt.create_session("Analyze revenue")
    assert isinstance(result, SessionState)
    assert result.session_id.startswith("sess-")


def test_create_session_appends_created_event() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    state = rt.create_session("Analyze revenue")
    events = store.load_events(state.session_id)
    assert len(events) == 1
    assert events[0].event_type == "session_created"
    assert events[0].payload["goal"] == "Analyze revenue"


def test_create_session_passes_kwargs_in_payload() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    state = rt.create_session("Goal", budget={"max_steps": 5})
    events = store.load_events(state.session_id)
    assert events[0].payload["budget"] == {"max_steps": 5}


def test_get_session_returns_state() -> None:
    rt = _make_runtime()
    state = rt.create_session("Test goal")
    fetched = rt.get_session(state.session_id)
    assert isinstance(fetched, SessionState)
    assert fetched.session_id == state.session_id
    assert fetched.goal == "Test goal"
    assert fetched.status == "active"


def test_get_session_raises_not_found_for_unknown() -> None:
    rt = _make_runtime()
    import pytest

    with pytest.raises(NotFoundError):
        rt.get_session(SessionId("nonexistent"))


def test_terminate_session_appends_terminated_event() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    state = rt.create_session("Test goal")
    rt.terminate_session(state.session_id, actor=UserId("test-user"))
    events = store.load_events(state.session_id)
    assert len(events) == 2
    assert events[1].event_type == "session_terminated"


def test_get_session_state_returns_rebuilt_state() -> None:
    rt = _make_runtime()
    state = rt.create_session("Test goal")
    rt.terminate_session(state.session_id, actor=UserId("test-user"))
    fetched = rt.get_session_state(state.session_id)
    assert fetched.status == "terminated"


def test_get_session_state_raises_not_found_for_unknown() -> None:
    rt = _make_runtime()
    import pytest

    with pytest.raises(NotFoundError):
        rt.get_session_state(SessionId("nonexistent"))


# --- Semantic model ops tests ---


def test_get_semantic_model_delegates_to_model_store() -> None:
    rt = _make_runtime()
    result = rt.get_semantic_model({"name": "test_model"})
    assert result is None  # StubModelStore.get returns None


def test_save_semantic_model_delegates_to_model_store() -> None:
    rt = _make_runtime()
    model = SemanticModel(name="test_model")
    result = rt.save_semantic_model(model, actor=UserId("user1"))
    assert result == ModelId(42)
    assert rt._ports.model_store._saved == [model]


def test_list_semantic_models_delegates_to_model_store() -> None:
    rt = _make_runtime()
    result = rt.list_semantic_models({"owner": UserId("user1")})
    assert result == []
