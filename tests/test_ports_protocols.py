from __future__ import annotations

from pydantic import BaseModel

from marivo.contracts.evidence import Evidence
from marivo.contracts.ids import (
    Action,
    CacheKey,
    DatasourceId,
    EvidenceRef,
    ModelId,
    ResourceId,
    SessionId,
    UserId,
)
from marivo.contracts.semantic import ModelSummary, SemanticModel
from marivo.contracts.session import SessionEvent, SessionState
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
from marivo.core.session.rebuild import rebuild_session_state

# --- Concrete implementations for Protocol satisfaction ---


class InMemoryModelSelector(BaseModel):
    model_id: ModelId | None = None
    name: str | None = None


class InMemoryModelListQuery(BaseModel):
    owner: UserId | None = None
    visibility: str | None = None
    include_public: bool = True
    include_private: bool = False


class InMemoryModelStore:
    def __init__(self) -> None:
        self._models: dict[ModelId, SemanticModel] = {}

    def get(self, selector: InMemoryModelSelector) -> SemanticModel | None:
        if selector.model_id is not None:
            return self._models.get(selector.model_id)
        return None

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
    ) -> ModelId:
        mid = ModelId(len(self._models) + 1)
        self._models[mid] = model
        return mid

    def list(self, query: InMemoryModelListQuery) -> list[ModelSummary]:
        return []


class InMemorySessionStore:
    def __init__(self) -> None:
        self._events: dict[SessionId, list[SessionEvent]] = {}

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        self._events.setdefault(session_id, []).append(event)

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        return self._events.get(session_id, [])

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        states: list[SessionState] = []
        for events in self._events.values():
            if events and events[0].event_type == "session_created" and events[0].actor == owner:
                states.append(rebuild_session_state(events))
        return states


class InMemoryDataSource:
    def execute(self, query: LogicalQuery) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        return SourceSchema(columns=[])


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._store: dict[EvidenceRef, Evidence] = {}

    def write(self, evidence: Evidence) -> EvidenceRef:
        self._store[evidence.ref] = evidence
        return evidence.ref

    def read(self, ref: EvidenceRef) -> Evidence:
        return self._store[ref]


class InMemoryCacheStore:
    def __init__(self) -> None:
        self._cache: dict[CacheKey, CacheValue] = {}

    def get(self, key: CacheKey) -> CacheValue | None:
        return self._cache.get(key)

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        self._cache[key] = value


class InMemoryAuthZ:
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class InMemoryTelemetry:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class InMemoryRuntimeConfig:
    def __init__(self) -> None:
        self._config: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._config.get(key)


# --- Tests ---


def test_model_store_import() -> None:
    from marivo.ports.model_store import ModelStore

    assert ModelStore is not None


def test_model_store_get() -> None:
    store = InMemoryModelStore()
    result = store.get(InMemoryModelSelector())
    assert result is None


def test_model_store_save() -> None:
    store = InMemoryModelStore()
    mid = store.save(SemanticModel(name="test"), actor=UserId("u1"))
    assert isinstance(mid, int)


def test_model_store_list() -> None:
    store = InMemoryModelStore()
    result = store.list(InMemoryModelListQuery())
    assert result == []


def test_session_store_import() -> None:
    from marivo.ports.session_store import SessionStore

    assert SessionStore is not None


def test_session_store_append_and_load() -> None:
    store = InMemorySessionStore()
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="created",
        timestamp="2024-01-01T00:00:00Z",
    )
    store.append_event(SessionId("s1"), event)
    events = store.load_events(SessionId("s1"))
    assert len(events) == 1
    assert events[0].event_type == "created"


def test_data_source_import() -> None:
    from marivo.ports.data_source import DataSource

    assert DataSource is not None


def test_data_source_execute() -> None:
    ds = InMemoryDataSource()
    result = ds.execute(LogicalQuery(sql="SELECT 1"))
    assert result.row_count == 0


def test_data_source_schema() -> None:
    ds = InMemoryDataSource()
    result = ds.schema(
        SourceRef(
            datasource_id=DatasourceId("ds1"),
            schema_name="public",
            table_name="t",
        )
    )
    assert result.columns == []


def test_evidence_store_import() -> None:
    from marivo.ports.evidence_store import EvidenceStore

    assert EvidenceStore is not None


def test_evidence_store_write_and_read() -> None:
    store = InMemoryEvidenceStore()
    evidence = Evidence(ref=EvidenceRef("ref1"), findings=[])
    ref = store.write(evidence)
    assert ref == "ref1"
    result = store.read(EvidenceRef("ref1"))
    assert result.ref == "ref1"


def test_cache_store_import() -> None:
    from marivo.ports.cache_store import CacheStore

    assert CacheStore is not None


def test_cache_store_get_and_set() -> None:
    store = InMemoryCacheStore()
    key = CacheKey("k1")
    assert store.get(key) is None
    store.set(key, CacheValue(b"v1"))
    assert store.get(key) == b"v1"


def test_authz_import() -> None:
    from marivo.ports.authz import AuthZ

    assert AuthZ is not None


def test_authz_check() -> None:
    authz = InMemoryAuthZ()
    decision = authz.check(UserId("u1"), Action("read"), ResourceId("r1"))
    assert decision.allowed


def test_audit_log_import() -> None:
    from marivo.ports.audit_log import AuditLog

    assert AuditLog is not None


def test_audit_log_record() -> None:
    log = InMemoryAuditLog()
    log.record(
        AuditEntry(
            actor=UserId("u1"),
            action="read",
            resource_type="model",
            resource_id="m1",
        )
    )
    assert len(log.entries) == 1


def test_telemetry_import() -> None:
    from marivo.ports.telemetry import Telemetry

    assert Telemetry is not None


def test_telemetry_emit() -> None:
    tel = InMemoryTelemetry()
    tel.emit(TelemetryEvent(name="test"))
    assert len(tel.events) == 1


def test_runtime_config_import() -> None:
    from marivo.ports.runtime_config import RuntimeConfig

    assert RuntimeConfig is not None


def test_runtime_config_get() -> None:
    cfg = InMemoryRuntimeConfig()
    assert cfg.get("missing") is None
