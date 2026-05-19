"""Tests for MarivoRuntime session lifecycle and semantic model operations."""

from __future__ import annotations

from marivo.contracts.errors import NotFoundError
from marivo.contracts.ids import (
    Action,
    ArtifactId,
    CacheKey,
    EvidenceRef,
    ModelId,
    ResourceId,
    SessionId,
    StepId,
    UserId,
)
from marivo.contracts.semantic import ModelSummary, SemanticModel
from marivo.contracts.session import SessionEvent, SessionState, Step
from marivo.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    LogicalQuery,
    QueryResult,
    SourceRef,
    SourceSchema,
    TelemetryEvent,
)
from marivo.core.engine import CoreEngine
from marivo.core.session.rebuild import rebuild_session_state
from marivo.identity import current_user
from marivo.runtime.runtime import MarivoRuntime

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

    def resolve_artifact_by_id(self, session_id, artifact_id):
        return None

    def resolve_artifact_with_step_by_id(self, session_id, artifact_id):
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


class RecordingStepStore(StubStepStore):
    def __init__(self, steps: list[Step]):
        self.steps = steps

    def list_steps(self, session_id):
        return [step for step in self.steps if step.session_id == session_id]


class RecordingArtifactStore(StubArtifactStore):
    def __init__(
        self,
        artifact_ids_by_step: dict[tuple[str, str], str | None] | None = None,
        artifacts_by_session: dict[str, list[dict[str, object]]] | None = None,
        failing_steps: set[tuple[str, str]] | None = None,
    ):
        self.artifact_ids_by_step = artifact_ids_by_step or {}
        self.artifacts_by_session = artifacts_by_session or {}
        self.failing_steps = failing_steps or set()

    def resolve_artifact_id_for_step(self, session_id, step_id):
        key = (str(session_id), str(step_id))
        if key in self.failing_steps:
            raise RuntimeError("artifact index unavailable")
        return self.artifact_ids_by_step.get(key)

    def list_artifacts(self, session_id):
        return self.artifacts_by_session.get(str(session_id), [])


class RuntimeFixture:
    def __init__(self, runtime: MarivoRuntime, store: RecordingSessionStore):
        self.runtime = runtime
        self.store = store

    def __iter__(self):
        yield self.runtime
        yield self.store

    def __getattr__(self, name: str):
        return getattr(self.runtime, name)


def _make_runtime(
    session_store=None,
    step_store=None,
    artifact_store=None,
):
    from marivo.runtime.ports import RuntimePorts

    store = session_store or RecordingSessionStore()
    ports = RuntimePorts(
        model_store=StubModelStore(),
        session_store=store,
        evidence_store=StubEvidenceStore(),
        data_source=StubDataSource(),
        cache_store=StubCacheStore(),
        authz=StubAuthZ(),
        audit_log=StubAuditLog(),
        telemetry=StubTelemetry(),
        runtime_config=StubRuntimeConfig(),
        artifact_store=artifact_store or StubArtifactStore(),
        step_store=step_store or StubStepStore(),
    )
    core = CoreEngine()
    rt = MarivoRuntime(ports=ports, core=core)
    # Session lifecycle tests don't need svc; intent dispatch tests
    # wire it separately.
    return RuntimeFixture(rt, store)


def _session_created_event(session_id: str = "sess_trace") -> SessionEvent:
    return SessionEvent(
        session_id=SessionId(session_id),
        event_type="session_created",
        timestamp="2026-05-18T00:00:00+00:00",
        payload={"goal": "Explain revenue change"},
        actor=UserId("alice"),
    )


def _step(
    step_id: str,
    *,
    created_at: str,
    result: dict[str, object] | None = None,
    provenance: dict[str, object] | None = None,
    semantic_metadata: dict[str, object] | None = None,
    session_id: str = "sess_trace",
) -> Step:
    return Step(
        step_id=StepId(step_id),
        session_id=SessionId(session_id),
        step_type="observe",
        summary=f"ran {step_id}",
        result=result or {},
        provenance=provenance,
        semantic_metadata=semantic_metadata,
        created_at=created_at,
    )


# --- Session lifecycle tests (ports-based) ---


def test_create_session_returns_session_state() -> None:
    rt = _make_runtime()
    result = rt.create_session("Analyze revenue")
    assert isinstance(result, SessionState)
    assert result.session_id.startswith("sess_")


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
    state = rt.create_session("Test goal", actor=UserId("test-user"))
    rt.terminate_session(state.session_id, actor=UserId("test-user"))
    events = store.load_events(state.session_id)
    assert len(events) == 2
    assert events[1].event_type == "session_terminated"


def test_terminate_session_resolves_current_user_when_actor_omitted() -> None:
    store = RecordingSessionStore()
    rt = _make_runtime(session_store=store)
    state = rt.create_session("Test goal", actor=UserId("test-user"))

    token = current_user.set("test-user")
    try:
        rt.terminate_session(state.session_id)
    finally:
        current_user.reset(token)

    events = store.load_events(state.session_id)
    assert len(events) == 2
    assert events[1].event_type == "session_terminated"
    assert events[1].actor == "test-user"


def test_get_session_returns_rebuilt_state() -> None:
    rt, _ = _make_runtime()
    state = rt.create_session("Test goal", actor=UserId("test-user"))
    rt.terminate_session(state.session_id, actor=UserId("test-user"))
    fetched = rt.get_session(state.session_id)
    assert fetched.status == "terminated"


def test_get_session_trace_returns_empty_trace_for_session_without_steps() -> None:
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    runtime, _ = _make_runtime(session_store=session_store)

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace == {
        "session_id": "sess_trace",
        "goal": "Explain revenue change",
        "lifecycle_status": "active",
        "created_at": "2026-05-18T00:00:00+00:00",
        "updated_at": "2026-05-18T00:00:00+00:00",
        "steps": [],
        "artifact_ids": [],
        "schema_version": "session_trace.v1",
    }


def test_get_session_trace_sorts_steps_and_dedupes_artifact_ids() -> None:
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step("step_b", created_at="2026-05-18T00:03:00+00:00"),
            _step("step_a2", created_at="2026-05-18T00:01:00+00:00"),
            _step("step_a1", created_at="2026-05-18T00:01:00+00:00"),
        ]
    )
    artifact_store = RecordingArtifactStore(
        artifact_ids_by_step={
            ("sess_trace", "step_a1"): "art_1",
            ("sess_trace", "step_a2"): "art_1",
            ("sess_trace", "step_b"): "art_2",
        }
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=artifact_store,
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert [step["step_id"] for step in trace["steps"]] == ["step_a1", "step_a2", "step_b"]
    assert trace["artifact_ids"] == ["art_1", "art_2"]


def test_get_session_trace_prefers_result_artifact_id_over_fallback() -> None:
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step(
                "step_1",
                created_at="2026-05-18T00:01:00+00:00",
                result={"artifact_id": "art_from_result", "row_count": 10},
            )
        ]
    )
    artifact_store = RecordingArtifactStore(
        artifact_ids_by_step={("sess_trace", "step_1"): "art_from_fallback"}
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=artifact_store,
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["artifact_id"] == "art_from_result"
    assert trace["artifact_ids"] == ["art_from_result"]


def test_get_session_trace_falls_back_to_artifact_store_and_warns_per_step_on_failure() -> None:
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step("step_ok", created_at="2026-05-18T00:01:00+00:00"),
            _step("step_bad", created_at="2026-05-18T00:02:00+00:00"),
        ]
    )
    artifact_store = RecordingArtifactStore(
        artifact_ids_by_step={("sess_trace", "step_ok"): "art_ok"},
        failing_steps={("sess_trace", "step_bad")},
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=artifact_store,
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["artifact_id"] == "art_ok"
    assert {
        "code": "provenance_missing",
        "message": "Step provenance is unavailable.",
        "field": "provenance",
    } in trace["steps"][0]["warnings"]
    assert trace["steps"][1]["artifact_id"] is None
    assert {
        "code": "artifact_id_unresolved",
        "message": "Artifact id could not be resolved for this step.",
        "field": "artifact_id",
    } in trace["steps"][1]["warnings"]
    assert trace["artifact_ids"] == ["art_ok"]


def test_get_session_trace_output_summary_uses_deterministic_whitelist() -> None:
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step(
                "step_1",
                created_at="2026-05-18T00:01:00+00:00",
                result={
                    "intent_type": "observe",
                    "status": "success",
                    "artifact_type": "observation",
                    "row_count": 3,
                    "candidate_count": 2,
                    "rows": [{"region": "US", "revenue": 100}],
                    "large_payload": {"nested": "value"},
                },
                provenance={"runner": "observe"},
                semantic_metadata={"metric": "revenue"},
            )
        ]
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=RecordingArtifactStore(
            artifact_ids_by_step={("sess_trace", "step_1"): "art_1"}
        ),
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["output_summary"] == {
        "intent_type": "observe",
        "status": "success",
        "artifact_type": "observation",
        "row_count": 3,
        "candidate_count": 2,
    }
    assert trace["steps"][0]["warnings"] == []
    assert trace["steps"][0]["provenance"] == {"runner": "observe"}
    assert trace["steps"][0]["semantic_metadata"] == {"metric": "revenue"}


def test_get_session_trace_warns_when_output_summary_is_unavailable() -> None:
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step(
                "step_1",
                created_at="2026-05-18T00:01:00+00:00",
                result={"rows": [{"region": "US"}]},
            )
        ]
    )
    runtime, _ = _make_runtime(session_store=session_store, step_store=step_store)

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["output_summary"] is None
    assert {
        "code": "output_summary_unavailable",
        "message": "No whitelisted scalar output summary fields are available.",
        "field": "output_summary",
    } in trace["steps"][0]["warnings"]


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
