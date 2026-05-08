"""Tests for RuntimePorts construction and Protocol satisfaction."""

from __future__ import annotations

from typing import Any

from app.contracts.evidence import Evidence
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
from app.ports.artifact_store import ArtifactStore
from app.ports.audit_log import AuditLog
from app.ports.authz import AuthZ
from app.ports.cache_store import CacheStore
from app.ports.data_source import DataSource
from app.ports.evidence_store import EvidenceStore
from app.ports.model_store import ModelStore
from app.ports.runtime_config import RuntimeConfig
from app.ports.session_store import SessionStore
from app.ports.step_store import StepStore
from app.ports.telemetry import Telemetry
from app.runtime.ports import RuntimePorts

# --- Stub implementations for Protocol satisfaction ---


class StubModelStore:
    def get(self, selector: object) -> SemanticModel | None:
        return None

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId:
        return ModelId(1)

    def list(self, query: object) -> list[ModelSummary]:
        return []


class StubSessionStore:
    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        pass

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        return []

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        return []

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


class StubEvidenceStore:
    def write(self, evidence: Evidence) -> EvidenceRef:
        return evidence.ref

    def read(self, ref: EvidenceRef) -> Evidence:
        raise KeyError(ref)


class StubDataSource:
    def execute(self, query: LogicalQuery) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        return SourceSchema(columns=[])

    def resolve_tables(self, table_names: list[str], *, session_id: str | None = None) -> Any:
        raise NotImplementedError


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


def _make_runtime_ports() -> RuntimePorts:
    return RuntimePorts(
        model_store=StubModelStore(),
        session_store=StubSessionStore(),
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


# --- Construction tests ---


def test_runtime_ports_construction() -> None:
    """RuntimePorts can be constructed with stub implementations."""
    ports = _make_runtime_ports()
    assert ports is not None


def test_runtime_ports_stores_model_store() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.model_store, StubModelStore)


def test_runtime_ports_stores_session_store() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.session_store, StubSessionStore)


def test_runtime_ports_stores_evidence_store() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.evidence_store, StubEvidenceStore)


def test_runtime_ports_stores_data_source() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.data_source, StubDataSource)


def test_runtime_ports_stores_cache_store() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.cache_store, StubCacheStore)


def test_runtime_ports_stores_authz() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.authz, StubAuthZ)


def test_runtime_ports_stores_audit_log() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.audit_log, StubAuditLog)


def test_runtime_ports_stores_telemetry() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.telemetry, StubTelemetry)


def test_runtime_ports_stores_runtime_config() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.runtime_config, StubRuntimeConfig)


def test_runtime_ports_stores_artifact_store() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.artifact_store, StubArtifactStore)


def test_runtime_ports_stores_step_store() -> None:
    ports = _make_runtime_ports()
    assert isinstance(ports.step_store, StubStepStore)


# --- Protocol satisfaction via structural method checks ---
# The Port Protocols are not @runtime_checkable, so isinstance() raises
# TypeError. We verify structural conformance by checking that each stub
# implements every method defined on its corresponding Protocol.


def _protocol_method_names(protocol_cls: type) -> set[str]:
    """Extract method names defined directly on a Protocol class."""
    return {
        name
        for name, value in vars(protocol_cls).items()
        if callable(value) and not name.startswith("_")
    }


def test_model_store_satisfies_protocol() -> None:
    stub = StubModelStore()
    for name in _protocol_method_names(ModelStore):
        assert callable(getattr(stub, name)), f"StubModelStore missing {name}"


def test_session_store_satisfies_protocol() -> None:
    stub = StubSessionStore()
    for name in _protocol_method_names(SessionStore):
        assert callable(getattr(stub, name)), f"StubSessionStore missing {name}"


def test_evidence_store_satisfies_protocol() -> None:
    stub = StubEvidenceStore()
    for name in _protocol_method_names(EvidenceStore):
        assert callable(getattr(stub, name)), f"StubEvidenceStore missing {name}"


def test_data_source_satisfies_protocol() -> None:
    stub = StubDataSource()
    for name in _protocol_method_names(DataSource):
        assert callable(getattr(stub, name)), f"StubDataSource missing {name}"


def test_cache_store_satisfies_protocol() -> None:
    stub = StubCacheStore()
    for name in _protocol_method_names(CacheStore):
        assert callable(getattr(stub, name)), f"StubCacheStore missing {name}"


def test_authz_satisfies_protocol() -> None:
    stub = StubAuthZ()
    for name in _protocol_method_names(AuthZ):
        assert callable(getattr(stub, name)), f"StubAuthZ missing {name}"


def test_audit_log_satisfies_protocol() -> None:
    stub = StubAuditLog()
    for name in _protocol_method_names(AuditLog):
        assert callable(getattr(stub, name)), f"StubAuditLog missing {name}"


def test_telemetry_satisfies_protocol() -> None:
    stub = StubTelemetry()
    for name in _protocol_method_names(Telemetry):
        assert callable(getattr(stub, name)), f"StubTelemetry missing {name}"


def test_runtime_config_satisfies_protocol() -> None:
    stub = StubRuntimeConfig()
    for name in _protocol_method_names(RuntimeConfig):
        assert callable(getattr(stub, name)), f"StubRuntimeConfig missing {name}"


def test_artifact_store_satisfies_protocol() -> None:
    stub = StubArtifactStore()
    for name in _protocol_method_names(ArtifactStore):
        assert callable(getattr(stub, name)), f"StubArtifactStore missing {name}"


def test_step_store_satisfies_protocol() -> None:
    stub = StubStepStore()
    for name in _protocol_method_names(StepStore):
        assert callable(getattr(stub, name)), f"StubStepStore missing {name}"


# --- Port attribute protocol satisfaction via RuntimePorts ---


def test_ports_model_store_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(ModelStore):
        assert callable(getattr(ports.model_store, name))


def test_ports_session_store_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(SessionStore):
        assert callable(getattr(ports.session_store, name))


def test_ports_evidence_store_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(EvidenceStore):
        assert callable(getattr(ports.evidence_store, name))


def test_ports_data_source_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(DataSource):
        assert callable(getattr(ports.data_source, name))


def test_ports_cache_store_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(CacheStore):
        assert callable(getattr(ports.cache_store, name))


def test_ports_authz_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(AuthZ):
        assert callable(getattr(ports.authz, name))


def test_ports_audit_log_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(AuditLog):
        assert callable(getattr(ports.audit_log, name))


def test_ports_telemetry_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(Telemetry):
        assert callable(getattr(ports.telemetry, name))


def test_ports_runtime_config_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(RuntimeConfig):
        assert callable(getattr(ports.runtime_config, name))


def test_ports_artifact_store_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(ArtifactStore):
        assert callable(getattr(ports.artifact_store, name))


def test_ports_step_store_satisfies_protocol() -> None:
    ports = _make_runtime_ports()
    for name in _protocol_method_names(StepStore):
        assert callable(getattr(ports.step_store, name))
