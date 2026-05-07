"""Tests for MarivoRuntime intent method dispatch to SemanticLayerService."""

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


def _make_ports() -> object:
    from app.runtime.ports import RuntimePorts

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
    )


# --- Helpers ---


def _make_runtime() -> MarivoRuntime:
    mock_svc = MagicMock()
    mock_svc.run_intent.return_value = {"status": "ok"}
    ports = _make_ports()
    core = CoreEngine()
    return MarivoRuntime(ports=ports, core=core, svc=mock_svc)


# --- Intent dispatch tests ---


INTENT_METHODS = [
    "observe",
    "compare",
    "decompose",
    "correlate",
    "detect",
    "test",
    "forecast",
    "attribute",
    "diagnose",
    "validate",
]


def test_all_intent_methods_exist() -> None:
    rt = _make_runtime()
    for name in INTENT_METHODS:
        assert callable(getattr(rt, name)), f"MarivoRuntime missing intent method: {name}"


def test_intent_dispatches_run_intent() -> None:
    rt = _make_runtime()
    params = {
        "metric": "revenue",
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-02-01"},
    }
    for intent_name in INTENT_METHODS:
        rt.svc.run_intent.reset_mock()
        method = getattr(rt, intent_name)
        result = method("sess_123", params)
        rt.svc.run_intent.assert_called_once_with("sess_123", intent_name, params)
        assert result == {"status": "ok"}


def test_observe_dispatches() -> None:
    rt = _make_runtime()
    rt.observe("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "observe", {"metric": "m"})


def test_compare_dispatches() -> None:
    rt = _make_runtime()
    rt.compare("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "compare", {"metric": "m"})


def test_decompose_dispatches() -> None:
    rt = _make_runtime()
    rt.decompose("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "decompose", {"metric": "m"})


def test_correlate_dispatches() -> None:
    rt = _make_runtime()
    rt.correlate("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "correlate", {"metric": "m"})


def test_detect_dispatches() -> None:
    rt = _make_runtime()
    rt.detect("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "detect", {"metric": "m"})


def test_test_dispatches() -> None:
    rt = _make_runtime()
    rt.test("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "test", {"metric": "m"})


def test_forecast_dispatches() -> None:
    rt = _make_runtime()
    rt.forecast("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "forecast", {"metric": "m"})


def test_attribute_dispatches() -> None:
    rt = _make_runtime()
    rt.attribute("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "attribute", {"metric": "m"})


def test_diagnose_dispatches() -> None:
    rt = _make_runtime()
    rt.diagnose("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "diagnose", {"metric": "m"})


def test_validate_dispatches() -> None:
    rt = _make_runtime()
    rt.validate("s1", {"metric": "m"})
    rt.svc.run_intent.assert_called_with("s1", "validate", {"metric": "m"})


def test_intent_returns_service_result() -> None:
    rt = _make_runtime()
    expected = {"step_id": "step_1", "status": "completed"}
    rt.svc.run_intent.return_value = expected
    result = rt.observe("s1", {"metric": "m"})
    assert result is expected
