"""Tests for port adapter wrappers in app.adapters.server.

Verifies that each adapter satisfies its corresponding Port Protocol by
checking structural method conformance (same approach as
test_runtime_construction.py for stubs).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from marivo.adapters.server.audit_log import FileAuditLogAdapter
from marivo.adapters.server.authz import NoopAuthZAdapter
from marivo.adapters.server.cache_store import InMemoryCacheStore
from marivo.adapters.server.data_source import DataSourceAdapter
from marivo.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
from marivo.adapters.server.model_store import SqlModelStoreAdapter
from marivo.adapters.server.runtime_config import TomlRuntimeConfigAdapter
from marivo.adapters.server.session_store import SqlSessionStoreAdapter
from marivo.adapters.server.telemetry import LocalTelemetryAdapter
from marivo.config import MarivoConfig
from marivo.contracts.evidence import Evidence, Finding
from marivo.contracts.ids import (
    Action,
    ArtifactId,
    CacheKey,
    EvidenceRef,
    FindingId,
    ResourceId,
    SessionId,
    UserId,
)
from marivo.contracts.semantic import SemanticModel
from marivo.contracts.session import SessionEvent
from marivo.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    LogicalQuery,
    QueryResult,
    TelemetryEvent,
)
from marivo.ports.audit_log import AuditLog
from marivo.ports.authz import AuthZ
from marivo.ports.cache_store import CacheStore
from marivo.ports.data_source import DataSource
from marivo.ports.evidence_store import EvidenceStore
from marivo.ports.model_store import ModelStore
from marivo.ports.runtime_config import RuntimeConfig
from marivo.ports.session_store import SessionStore
from marivo.ports.telemetry import Telemetry

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
    metadata = MagicMock(spec=["query_one", "query_rows", "execute"])
    return SqlSessionStoreAdapter(metadata=metadata)


def _make_data_source_adapter() -> DataSourceAdapter:
    engine = MagicMock(spec=["query_rows"])
    router = MagicMock()
    return DataSourceAdapter(engine=engine, router=router)


def _make_evidence_store_adapter() -> MetadataEvidenceStoreAdapter:
    import tempfile
    from pathlib import Path

    from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
    from marivo.adapters.server.evidence_repositories import (
        AssessmentRepository,
        FindingRepository,
        PropositionRepository,
    )

    tmp = Path(tempfile.mkdtemp())
    store = SQLiteMetadataStore(tmp / "test.meta.sqlite")
    store.initialize()
    return MetadataEvidenceStoreAdapter(
        finding_repo=FindingRepository(store),
        proposition_repo=PropositionRepository(store),
        assessment_repo=AssessmentRepository(store),
    )


def _make_cache_store_adapter() -> InMemoryCacheStore:
    return InMemoryCacheStore()


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
    selector.owner = None
    adapter._metadata.query_one.return_value = None
    result = adapter.get(selector)
    assert result is None


def test_sql_model_store_list_returns_empty_when_no_match() -> None:
    adapter = _make_sql_model_store_adapter()
    adapter._metadata.query_rows.return_value = []
    query = MagicMock()
    query.owner = None
    query.include_public = True
    query.include_private = False
    result = adapter.list(query)
    assert isinstance(result, list)
    assert len(result) == 0


def test_sql_model_store_save_returns_model_id() -> None:
    adapter = _make_sql_model_store_adapter()
    adapter._metadata.query_one.side_effect = [
        None,  # no existing model
        {"model_id": 1},  # newly inserted row
    ]
    model = SemanticModel(name="test")
    model_id = adapter.save(model, actor=UserId("u1"))
    assert model_id is not None


# ---------------------------------------------------------------------------
# SqlSessionStoreAdapter
# ---------------------------------------------------------------------------


def test_sql_session_store_satisfies_protocol() -> None:
    adapter = _make_sql_session_store_adapter()
    for name in _protocol_method_names(SessionStore):
        assert callable(getattr(adapter, name)), f"SqlSessionStoreAdapter missing {name}"


def test_sql_session_store_append_event_session_created_delegates_to_metadata() -> None:
    adapter = _make_sql_session_store_adapter()
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="session_created",
        timestamp="2026-01-01T00:00:00Z",
        payload={"goal": "test"},
        actor=UserId("alice"),
    )
    adapter.append_event(SessionId("s1"), event)
    adapter._metadata.execute.assert_called_once()


def test_sql_session_store_append_event_unknown_type_is_noop() -> None:
    adapter = _make_sql_session_store_adapter()
    event = SessionEvent(
        session_id=SessionId("s1"),
        event_type="test",
        timestamp="2026-01-01T00:00:00Z",
    )
    # Unknown event types are silently ignored (no error raised)
    adapter.append_event(SessionId("s1"), event)
    adapter._metadata.execute.assert_not_called()


def test_sql_session_store_load_events_raises_not_found_for_missing() -> None:
    from marivo.contracts.errors import NotFoundError

    adapter = _make_sql_session_store_adapter()
    adapter._metadata.query_one.return_value = None
    with pytest.raises(NotFoundError):
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
    # write() now returns a deterministic SHA-256 hash computed from
    # the evidence content (excluding the ref field), consistent with
    # FileEvidenceStore.
    assert isinstance(result, str)
    assert len(result) == 64  # SHA-256 hex digest


def test_evidence_store_read_raises_not_found_for_missing() -> None:
    adapter = _make_evidence_store_adapter()
    from marivo.contracts.errors import NotFoundError

    with pytest.raises(NotFoundError):
        adapter.read(EvidenceRef("0" * 64))


# ---------------------------------------------------------------------------
# InMemoryCacheStore
# ---------------------------------------------------------------------------


def test_cache_store_satisfies_protocol() -> None:
    adapter = _make_cache_store_adapter()
    for name in _protocol_method_names(CacheStore):
        assert callable(getattr(adapter, name)), f"InMemoryCacheStore missing {name}"


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
