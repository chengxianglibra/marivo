"""Tests for MarivoRuntime intent method dispatch to intent_execution module."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from marivo.contracts.generated import aoi
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
        reasoning=None,
        sql_texts=None,
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


def _time_scope() -> aoi.TimeScope:
    return aoi.TimeScope(
        field="event_time",
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 8, tzinfo=UTC),
    )


def _observe_request() -> aoi.Observe:
    return aoi.Observe(
        metric="view_time",
        time_scope=_time_scope(),
        granularity="day",
    )


def _compare_request() -> aoi.Compare:
    return aoi.Compare(
        current_artifact_id="artifact-left",
        baseline_artifact_id="artifact-right",
        compare_type="normal",
    )


def _decompose_request() -> aoi.Decompose:
    return aoi.Decompose(
        compare_artifact_id="artifact-compare",
        dimension="region",
        limit=10,
    )


def _correlate_request() -> aoi.Correlate:
    return aoi.Correlate(
        left_artifact_id="artifact-left",
        right_artifact_id="artifact-right",
        method="pearson",
    )


def _detect_request() -> aoi.Detect:
    return aoi.Detect(
        metric="view_time",
        time_scope=_time_scope(),
        granularity="day",
        strategy="point_anomaly",
        sensitivity="aggressive",
        limit=10,
    )


def _forecast_request() -> aoi.Forecast:
    return aoi.Forecast(source_artifact_id="artifact-source", horizon=7)


def _attribute_request() -> aoi.Attribute:
    return aoi.Attribute(
        metric="view_time",
        current=aoi.Slice(time_scope=_time_scope()),
        baseline=aoi.Slice(time_scope=_time_scope()),
        dimensions=["region"],
    )


def _diagnose_request() -> aoi.Diagnose:
    return aoi.Diagnose(
        metric="view_time",
        time_scope=_time_scope(),
        granularity="day",
        dimensions=["region"],
        strategy="point_anomaly",
    )


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
    "forecast",
    "attribute",
    "diagnose",
]

ATOMIC_INTENT_REQUESTS = {
    "observe": _observe_request,
    "compare": _compare_request,
    "decompose": _decompose_request,
    "correlate": _correlate_request,
    "detect": _detect_request,
    "forecast": _forecast_request,
}


def test_all_intent_methods_exist() -> None:
    rt = _make_runtime()
    for name in INTENT_METHODS:
        assert callable(getattr(rt, name)), f"MarivoRuntime missing intent method: {name}"


def test_intent_dispatches_to_intent_execution() -> None:
    rt = _make_runtime()
    for intent_name, make_request in ATOMIC_INTENT_REQUESTS.items():
        target = f"marivo.runtime.intent_execution.{intent_name}"
        with patch(target, return_value={"status": "ok"}) as mock_fn:
            request = make_request()
            method = getattr(rt, intent_name)
            result = method("sess_123", request)
            mock_fn.assert_called_once_with(rt, SessionId("sess_123"), request, reasoning=None)
            assert result == {"status": "ok"}

    attribute_request = _attribute_request()
    with patch(
        "marivo.runtime.intent_execution.attribute", return_value={"status": "ok"}
    ) as mock_fn:
        result = rt.attribute("sess_123", attribute_request)
        mock_fn.assert_called_once_with(
            rt, SessionId("sess_123"), attribute_request, reasoning=None
        )
        assert result == {"status": "ok"}

    diagnose_request = _diagnose_request()
    with patch(
        "marivo.runtime.intent_execution.diagnose", return_value={"status": "ok"}
    ) as mock_fn:
        result = rt.diagnose("sess_123", diagnose_request)
        mock_fn.assert_called_once_with(rt, SessionId("sess_123"), diagnose_request, reasoning=None)
        assert result == {"status": "ok"}


def test_observe_dispatches() -> None:
    rt = _make_runtime()
    with patch("marivo.runtime.intent_execution.observe", return_value={"status": "ok"}) as mock_fn:
        request = _observe_request()
        rt.observe("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_compare_dispatches() -> None:
    rt = _make_runtime()
    with patch("marivo.runtime.intent_execution.compare", return_value={"status": "ok"}) as mock_fn:
        request = _compare_request()
        rt.compare("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_decompose_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.decompose", return_value={"status": "ok"}
    ) as mock_fn:
        request = _decompose_request()
        rt.decompose("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_correlate_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.correlate", return_value={"status": "ok"}
    ) as mock_fn:
        request = _correlate_request()
        rt.correlate("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_forecast_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.forecast", return_value={"status": "ok"}
    ) as mock_fn:
        request = _forecast_request()
        rt.forecast("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_attribute_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.attribute", return_value={"status": "ok"}
    ) as mock_fn:
        request = _attribute_request()
        rt.attribute("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_diagnose_dispatches() -> None:
    rt = _make_runtime()
    with patch(
        "marivo.runtime.intent_execution.diagnose", return_value={"status": "ok"}
    ) as mock_fn:
        request = _diagnose_request()
        rt.diagnose("s1", request)
        mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning=None)


def test_intent_returns_service_result() -> None:
    rt = _make_runtime()
    expected = {"step_id": "step_1", "status": "completed"}
    with patch("marivo.runtime.intent_execution.observe", return_value=expected) as mock_fn:
        result = rt.observe("s1", _observe_request())
        assert result is expected
