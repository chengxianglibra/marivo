"""Tests for MarivoRuntime session lifecycle and semantic model operations."""

from __future__ import annotations

from app.contracts.ids import (
    Action,
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
        return list(self._events.get(str(session_id), []))


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
    )
    core = CoreEngine()
    rt = MarivoRuntime(ports=ports, core=core)
    # Session lifecycle tests don't need svc; intent dispatch tests
    # wire it separately.
    return rt


# --- Session lifecycle tests (ports-based) ---


def test_create_session_returns_session_id() -> None:
    rt = _make_runtime()
    result = rt.create_session("Analyze revenue")
    assert isinstance(result, str)
    assert result.startswith("sess-")


def test_create_session_appends_created_event() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    session_id = rt.create_session("Analyze revenue")
    events = store.load_events(session_id)
    assert len(events) == 1
    assert events[0].event_type == "session_created"
    assert events[0].payload["goal"] == "Analyze revenue"


def test_create_session_passes_kwargs_in_payload() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    session_id = rt.create_session("Goal", budget={"max_steps": 5})
    events = store.load_events(session_id)
    assert events[0].payload["budget"] == {"max_steps": 5}


def test_get_session_returns_state() -> None:
    rt = _make_runtime()
    session_id = rt.create_session("Test goal")
    state = rt.get_session(session_id)
    assert state is not None
    assert isinstance(state, SessionState)
    assert state.session_id == session_id
    assert state.goal == "Test goal"
    assert state.status == "active"


def test_get_session_returns_none_for_unknown() -> None:
    rt = _make_runtime()
    result = rt.get_session(SessionId("nonexistent"))
    assert result is None


def test_terminate_session_appends_terminated_event() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    session_id = rt.create_session("Test goal")
    rt.terminate_session(session_id)
    events = store.load_events(session_id)
    assert len(events) == 2
    assert events[1].event_type == "session_terminated"


def test_get_session_state_returns_rebuilt_state() -> None:
    rt = _make_runtime()
    session_id = rt.create_session("Test goal")
    rt.terminate_session(session_id)
    state = rt.get_session_state(session_id)
    assert state is not None
    assert state.status == "terminated"


def test_get_session_state_returns_none_for_unknown() -> None:
    rt = _make_runtime()
    result = rt.get_session_state(SessionId("nonexistent"))
    assert result is None


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
