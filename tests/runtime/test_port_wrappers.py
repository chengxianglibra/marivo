"""Tests for port adapter wrappers in app.adapters.server.wrappers.

Verifies that each adapter satisfies its corresponding Port Protocol by
checking structural method conformance (same approach as
test_runtime_construction.py for stubs).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.server.wrappers import (
    DataSourceAdapter,
    FileAuditLogAdapter,
    LocalTelemetryAdapter,
    MetadataCacheStoreAdapter,
    MetadataEvidenceStoreAdapter,
    NoopAuthZAdapter,
    SqlModelStoreAdapter,
    SqlSessionStoreAdapter,
    TomlRuntimeConfigAdapter,
)
from app.config import MarivoConfig
from app.contracts.evidence import Evidence, Finding
from app.contracts.ids import (
    Action,
    ArtifactId,
    CacheKey,
    EvidenceRef,
    FindingId,
    ResourceId,
    SessionId,
    UserId,
)
from app.contracts.semantic import SemanticModel
from app.contracts.session import SessionEvent
from app.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    LogicalQuery,
    QueryResult,
    TelemetryEvent,
)
from app.ports.audit_log import AuditLog
from app.ports.authz import AuthZ
from app.ports.cache_store import CacheStore
from app.ports.data_source import DataSource
from app.ports.evidence_store import EvidenceStore
from app.ports.model_store import ModelStore
from app.ports.runtime_config import RuntimeConfig
from app.ports.session_store import SessionStore
from app.ports.telemetry import Telemetry

# --- Helper ---


def _protocol_method_names(protocol_cls: type) -> set[str]:
    """Extract method names defined directly on a Protocol class."""
    return {
        name
        for name, value in vars(protocol_cls).items()
        if callable(value) and not name.startswith("_")
    }


def _make_sql_model_store_adapter() -> SqlModelStoreAdapter:
    service = MagicMock()
    metadata = MagicMock(spec=["query_one", "query_rows", "execute"])
    return SqlModelStoreAdapter(service=service, metadata=metadata)


def _make_sql_session_store_adapter() -> SqlSessionStoreAdapter:
    session_manager = MagicMock()
    metadata = MagicMock(spec=["query_one", "query_rows", "execute"])
    return SqlSessionStoreAdapter(session_manager=session_manager, metadata=metadata)


def _make_data_source_adapter() -> DataSourceAdapter:
    engine = MagicMock(spec=["query_rows"])
    router = MagicMock()
    return DataSourceAdapter(engine=engine, router=router)


def _make_evidence_store_adapter() -> MetadataEvidenceStoreAdapter:
    finding_repo = MagicMock()
    proposition_repo = MagicMock()
    assessment_repo = MagicMock()
    return MetadataEvidenceStoreAdapter(
        finding_repo=finding_repo,
        proposition_repo=proposition_repo,
        assessment_repo=assessment_repo,
    )


def _make_cache_store_adapter() -> MetadataCacheStoreAdapter:
    metadata = MagicMock(spec=["query_one", "query_rows", "execute"])
    return MetadataCacheStoreAdapter(metadata=metadata)


# ---------------------------------------------------------------------------
# NoopAuthZAdapter
# ---------------------------------------------------------------------------


def test_noop_authz_check_returns_allowed() -> None:
    adapter = NoopAuthZAdapter()
    result = adapter.check(UserId("user1"), Action("read"), ResourceId("res1"))
    assert isinstance(result, AuthZDecision)
    assert result.allowed is True


def test_noop_authz_satisfies_protocol() -> None:
    adapter = NoopAuthZAdapter()
    for name in _protocol_method_names(AuthZ):
        assert callable(getattr(adapter, name)), f"NoopAuthZAdapter missing {name}"


# ---------------------------------------------------------------------------
# FileAuditLogAdapter
# ---------------------------------------------------------------------------


def test_file_audit_log_record_does_not_raise() -> None:
    adapter = FileAuditLogAdapter()
    entry = AuditEntry(
        actor=UserId("user1"),
        action="create",
        resource_type="model",
        resource_id="m1",
    )
    adapter.record(entry)


def test_file_audit_log_satisfies_protocol() -> None:
    adapter = FileAuditLogAdapter()
    for name in _protocol_method_names(AuditLog):
        assert callable(getattr(adapter, name)), f"FileAuditLogAdapter missing {name}"


# ---------------------------------------------------------------------------
# LocalTelemetryAdapter
# ---------------------------------------------------------------------------


def test_local_telemetry_emit_does_not_raise() -> None:
    adapter = LocalTelemetryAdapter()
    event = TelemetryEvent(name="test_event")
    adapter.emit(event)


def test_local_telemetry_satisfies_protocol() -> None:
    adapter = LocalTelemetryAdapter()
    for name in _protocol_method_names(Telemetry):
        assert callable(getattr(adapter, name)), f"LocalTelemetryAdapter missing {name}"


# ---------------------------------------------------------------------------
# TomlRuntimeConfigAdapter
# ---------------------------------------------------------------------------


def test_toml_runtime_config_get_returns_string_or_none() -> None:
    config = MarivoConfig()
    adapter = TomlRuntimeConfigAdapter(config)
    result = adapter.get("metadata")
    assert result is None or isinstance(result, str)


def test_toml_runtime_config_get_returns_none_for_unknown_key() -> None:
    config = MarivoConfig()
    adapter = TomlRuntimeConfigAdapter(config)
    result = adapter.get("nonexistent_key_xyz")
    assert result is None


def test_toml_runtime_config_satisfies_protocol() -> None:
    adapter = TomlRuntimeConfigAdapter(MarivoConfig())
    for name in _protocol_method_names(RuntimeConfig):
        assert callable(getattr(adapter, name)), f"TomlRuntimeConfigAdapter missing {name}"


# ---------------------------------------------------------------------------
# SqlModelStoreAdapter
# ---------------------------------------------------------------------------


def test_sql_model_store_satisfies_protocol() -> None:
    adapter = _make_sql_model_store_adapter()
    for name in _protocol_method_names(ModelStore):
        assert callable(getattr(adapter, name)), f"SqlModelStoreAdapter missing {name}"


def test_sql_model_store_get_returns_none_for_missing_name() -> None:
    adapter = _make_sql_model_store_adapter()
    selector = MagicMock()
    selector.name = None
    result = adapter.get(selector)
    assert result is None


def test_sql_model_store_get_returns_none_when_not_found() -> None:
    adapter = _make_sql_model_store_adapter()
    selector = MagicMock()
    selector.name = "nonexistent"
    adapter._service.get_semantic_model.side_effect = Exception("not found")
    result = adapter.get(selector)
    assert result is None


def test_sql_model_store_list_delegates_to_service() -> None:
    adapter = _make_sql_model_store_adapter()
    adapter._service.list_semantic_models.return_value = []
    query = MagicMock()
    query.owner = None
    result = adapter.list(query)
    assert isinstance(result, list)


def test_sql_model_store_save_raises_not_implemented() -> None:
    adapter = _make_sql_model_store_adapter()
    model = SemanticModel(name="test")
    with pytest.raises(NotImplementedError):
        adapter.save(model, actor=UserId("u1"), expected_revision=None)


# ---------------------------------------------------------------------------
# SqlSessionStoreAdapter
# ---------------------------------------------------------------------------


def test_sql_session_store_satisfies_protocol() -> None:
    adapter = _make_sql_session_store_adapter()
    for name in _protocol_method_names(SessionStore):
        assert callable(getattr(adapter, name)), f"SqlSessionStoreAdapter missing {name}"


def test_sql_session_store_append_event_raises_not_implemented() -> None:
    adapter = _make_sql_session_store_adapter()
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="test",
        timestamp="2026-01-01T00:00:00Z",
    )
    with pytest.raises(NotImplementedError):
        adapter.append_event(SessionId("s1"), event)


def test_sql_session_store_load_events_raises_not_implemented() -> None:
    adapter = _make_sql_session_store_adapter()
    with pytest.raises(NotImplementedError):
        adapter.load_events(SessionId("s1"))


# ---------------------------------------------------------------------------
# DataSourceAdapter
# ---------------------------------------------------------------------------


def test_data_source_satisfies_protocol() -> None:
    adapter = _make_data_source_adapter()
    for name in _protocol_method_names(DataSource):
        assert callable(getattr(adapter, name)), f"DataSourceAdapter missing {name}"


def test_data_source_execute_returns_query_result() -> None:
    adapter = _make_data_source_adapter()
    adapter._engine.query_rows.return_value = [
        {"col1": 1, "col2": "a"},
        {"col1": 2, "col2": "b"},
    ]
    query = LogicalQuery(sql="SELECT col1, col2 FROM t")
    result = adapter.execute(query)
    assert isinstance(result, QueryResult)
    assert result.row_count == 2
    assert result.columns == ["col1", "col2"]


def test_data_source_execute_empty_result() -> None:
    adapter = _make_data_source_adapter()
    adapter._engine.query_rows.return_value = []
    query = LogicalQuery(sql="SELECT 1")
    result = adapter.execute(query)
    assert isinstance(result, QueryResult)
    assert result.row_count == 0
    assert result.columns == []


# ---------------------------------------------------------------------------
# MetadataEvidenceStoreAdapter
# ---------------------------------------------------------------------------


def test_evidence_store_satisfies_protocol() -> None:
    adapter = _make_evidence_store_adapter()
    for name in _protocol_method_names(EvidenceStore):
        assert callable(getattr(adapter, name)), f"MetadataEvidenceStoreAdapter missing {name}"


def test_evidence_store_write_returns_evidence_ref() -> None:
    adapter = _make_evidence_store_adapter()
    evidence = Evidence(
        ref=EvidenceRef("ref1"),
        findings=[
            Finding(
                finding_id=FindingId("f1"),
                session_id=SessionId("s1"),
                artifact_id=ArtifactId("a1"),
                finding_type="test",
                content={},
            )
        ],
    )
    result = adapter.write(evidence)
    assert result == EvidenceRef("ref1")


def test_evidence_store_read_raises_not_implemented() -> None:
    adapter = _make_evidence_store_adapter()
    with pytest.raises(NotImplementedError):
        adapter.read(EvidenceRef("ref1"))


# ---------------------------------------------------------------------------
# MetadataCacheStoreAdapter
# ---------------------------------------------------------------------------


def test_cache_store_satisfies_protocol() -> None:
    adapter = _make_cache_store_adapter()
    for name in _protocol_method_names(CacheStore):
        assert callable(getattr(adapter, name)), f"MetadataCacheStoreAdapter missing {name}"


def test_cache_store_get_returns_none_for_missing_key() -> None:
    adapter = _make_cache_store_adapter()
    result = adapter.get(CacheKey("missing"))
    assert result is None


def test_cache_store_set_and_get_roundtrip() -> None:
    adapter = _make_cache_store_adapter()
    key = CacheKey("test_key")
    value = CacheValue(b"test_value")
    adapter.set(key, value)
    result = adapter.get(key)
    assert result is not None
    assert bytes(result) == b"test_value"


def test_cache_store_set_with_ttl_ignored() -> None:
    adapter = _make_cache_store_adapter()
    key = CacheKey("ttl_key")
    value = CacheValue(b"ttl_value")
    adapter.set(key, value, ttl=60)
    result = adapter.get(key)
    assert result is not None
