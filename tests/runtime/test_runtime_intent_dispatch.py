"""Tests for MarivoRuntime intent method dispatch to intent_execution module."""

from __future__ import annotations

from unittest.mock import patch

from marivo.contracts.ids import (
    Action,
    ArtifactId,
    CacheKey,
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
from marivo.core.engine import CoreEngine
from marivo.runtime.runtime import MarivoRuntime

# --- Stub port implementations ---


class StubModelStore:
    def get(self, selector: object) -> SemanticModel | None:
        return None

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
    ) -> ModelId:
        return ModelId(1)

    def list(self, query: object) -> list[ModelSummary]:
        return []


class StubSessionStore:
    def append_event(self, session_id: object, event: object) -> None:
        pass

    def load_events(self, session_id: object) -> list[SessionEvent]:
        return [
            SessionEvent(
                session_id=SessionId("s1"),
                event_type="session_created",
                timestamp="2024-01-01T00:00:00Z",
                payload={"goal": "test"},
                actor=None,
            )
        ]

    def list_sessions(self, owner: UserId) -> list[SessionState]:
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


def _make_ports() -> object:
    from marivo.runtime.ports import RuntimePorts

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


# --- Helpers ---


def _make_runtime() -> MarivoRuntime:
    ports = _make_ports()
    core = CoreEngine()
    rt = MarivoRuntime(ports=ports, core=core)
    return rt


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


def test_intent_dispatches_to_intent_execution() -> None:
    rt = _make_runtime()
    params = {
        "metric": "revenue",
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-02-01"},
    }
    for intent_name in INTENT_METHODS:
        target = f"marivo.runtime.intent_execution.{intent_name}"
        with patch(target, return_value={"status": "ok"}) as mock_fn:
            method = getattr(rt, intent_name)
            result = method("sess_123", params)
            mock_fn.assert_called_once_with(rt, SessionId("sess_123"), params)
            assert result == {"status": "ok"}


def test_observe_dispatches() -> None:
    rt = _make_runtime()
    with patch("marivo.runtime.intent_execution.observe", return_value={"status": "ok"}) as mock_fn:
        rt.observe("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_compare_dispatches() -> None:
    rt = _make_runtime()
    with patch("marivo.runtime.intent_execution.compare", return_value={"status": "ok"}) as mock_fn:
        rt.compare("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_decompose_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.decompose", return_value={"status": "ok"}
    ) as mock_fn:
        rt.decompose("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_correlate_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.correlate", return_value={"status": "ok"}
    ) as mock_fn:
        rt.correlate("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_detect_dispatches() -> None:
    rt = _make_runtime()
    with patch("marivo.runtime.intent_execution.detect", return_value={"status": "ok"}) as mock_fn:
        rt.detect("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_test_dispatches() -> None:
    rt = _make_runtime()
    with patch("marivo.runtime.intent_execution.test", return_value={"status": "ok"}) as mock_fn:
        rt.test("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_forecast_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.forecast", return_value={"status": "ok"}
    ) as mock_fn:
        rt.forecast("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_attribute_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.attribute", return_value={"status": "ok"}
    ) as mock_fn:
        rt.attribute("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_diagnose_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.diagnose", return_value={"status": "ok"}
    ) as mock_fn:
        rt.diagnose("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_validate_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.validate", return_value={"status": "ok"}
    ) as mock_fn:
        rt.validate("s1", {"metric": "m"})
        mock_fn.assert_called_once_with(rt, SessionId("s1"), {"metric": "m"})


def test_intent_returns_service_result() -> None:
    rt = _make_runtime()
    expected = {"step_id": "step_1", "status": "completed"}
    with patch("marivo.runtime.intent_execution.observe", return_value=expected) as mock_fn:
        result = rt.observe("s1", {"metric": "m"})
        assert result is expected
