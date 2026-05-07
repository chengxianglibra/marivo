"""Tests for MarivoRuntime session lifecycle and semantic model operations."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.contracts.ids import (
    Action,
    CacheKey,
    EvidenceRef,
    ModelId,
    ResourceId,
    RevisionId,
    UserId,
)
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionEvent
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


class StubSessionStore:
    def append_event(self, session_id: object, event: object) -> None:
        pass

    def load_events(self, session_id: object) -> list[SessionEvent]:
        return []


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


def _make_runtime() -> MarivoRuntime:
    from app.runtime.ports import RuntimePorts

    mock_svc = MagicMock()
    ports = RuntimePorts(
        model_store=StubModelStore(),
        session_store=StubSessionStore(),
        evidence_store=StubEvidenceStore(),
        data_source=StubDataSource(),
        cache_store=StubCacheStore(),
        authz=StubAuthZ(),
        audit_log=StubAuditLog(),
        telemetry=StubTelemetry(),
        runtime_config=StubRuntimeConfig(),
    )
    core = CoreEngine(mock_svc)
    return MarivoRuntime(ports=ports, core=core, svc=mock_svc)


# --- Session lifecycle tests ---


def test_create_session_proxies_to_svc() -> None:
    rt = _make_runtime()
    rt.svc.create_session.return_value = {"session_id": "s1"}
    result = rt.create_session("Analyze revenue")
    rt.svc.create_session.assert_called_once_with("Analyze revenue")
    assert result == {"session_id": "s1"}


def test_create_session_passes_kwargs() -> None:
    rt = _make_runtime()
    rt.svc.create_session.return_value = {"session_id": "s2"}
    rt.create_session("Goal", budget={"max_steps": 5})
    rt.svc.create_session.assert_called_once_with("Goal", budget={"max_steps": 5})


def test_get_session_proxies_to_svc() -> None:
    rt = _make_runtime()
    rt.svc.get_session.return_value = {"session_id": "s1", "status": "open"}
    result = rt.get_session("s1")
    rt.svc.get_session.assert_called_once_with("s1")
    assert result["session_id"] == "s1"


def test_terminate_session_proxies_to_svc() -> None:
    rt = _make_runtime()
    rt.terminate_session("s1")
    rt.svc.terminate_session.assert_called_once_with("s1")


def test_get_session_state_proxies_to_svc() -> None:
    rt = _make_runtime()
    rt.svc.get_session_state.return_value = {"propositions": []}
    result = rt.get_session_state("s1", status="open")
    rt.svc.get_session_state.assert_called_once_with("s1", {"status": "open"})
    assert result == {"propositions": []}


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


# --- Datasource ops tests ---


def test_discover_catalog_proxies_to_svc() -> None:
    rt = _make_runtime()
    rt.svc.discover_catalog.return_value = {"entities": []}
    result = rt.discover_catalog()
    rt.svc.discover_catalog.assert_called_once()
    assert result == {"entities": []}
