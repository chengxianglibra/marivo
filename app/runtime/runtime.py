from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.contracts.ids import ModelId, SessionId, UserId
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionEvent, SessionState
from app.core.session.rebuild import rebuild_session_state

if TYPE_CHECKING:
    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts
    from app.service import SemanticLayerService


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class MarivoRuntime:
    """Use-case facade for the Marivo platform.

    Phase 4b-3: Session lifecycle methods use ports.session_store
    directly.  I/O proxy methods still delegate to self._svc until
    Tasks 12-16 migrate intent runners to call ports.* directly.
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
    ) -> None:
        self._ports = ports
        self._core = core
        self._svc: SemanticLayerService | None = None  # set via wire_svc()

    def wire_svc(self, svc: SemanticLayerService) -> None:
        """Attach the backing service for I/O proxy + intent methods.

        Temporary bridge until Tasks 12-16 migrate intent runners to
        call ports.* directly.  After those tasks, this method and all
        self._svc references will be removed.
        """
        self._svc = svc

    @property
    def core(self) -> CoreEngine:
        """Pure computation facade (no I/O)."""
        return self._core

    @property
    def ports(self) -> RuntimePorts:
        """Typed container for all port implementations."""
        return self._ports

    # --- I/O proxy methods (moved from CoreEngine, 4b-1) ---
    # These temporarily delegate to self._svc.
    # 4b-2 will migrate intent runners to call ports.* directly.

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        assert self._svc is not None, "Runtime._svc required for resolve_metric_execution_context"
        return self._svc._resolve_metric_execution_context(*args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        assert self._svc is not None, "Runtime._svc required for compile_step"
        return self._svc._compile_step_with_feedback(*args, **kwargs)

    def resolve_metric_dimensions(self, metric_ref: str) -> list[str] | None:
        assert self._svc is not None, "Runtime._svc required for resolve_metric_dimensions"
        return self._svc.resolve_metric_dimensions(metric_ref)

    def resolve_metric(self, metric_name: str) -> Any:
        assert self._svc is not None, "Runtime._svc required for resolve_metric"
        return self._svc.semantic_repository.resolve_metric(metric_name)

    def resolve_metric_table(self, metric_name: str, **kwargs: Any) -> str | None:
        assert self._svc is not None, "Runtime._svc required for resolve_metric_table"
        return self._svc._resolve_metric_table(metric_name, **kwargs)

    def resolve_metric_sql_for_execution(self, *args: Any, **kwargs: Any) -> str:
        assert self._svc is not None, "Runtime._svc required for resolve_metric_sql_for_execution"
        return self._svc.resolve_metric_sql_for_execution(*args, **kwargs)

    def resolve_metric_value_sql_for_execution(self, *args: Any, **kwargs: Any) -> str | None:
        assert self._svc is not None, (
            "Runtime._svc required for resolve_metric_value_sql_for_execution"
        )
        return self._svc.resolve_metric_value_sql_for_execution(*args, **kwargs)

    def resolve_scope_constraint_column(self, *args: Any, **kwargs: Any) -> str:
        assert self._svc is not None, "Runtime._svc required for resolve_scope_constraint_column"
        return self._svc._resolve_scope_constraint_column(*args, **kwargs)

    def resolve_artifact_for_ref(self, session_id: str, step_id: str) -> dict[str, Any] | None:
        assert self._svc is not None, "Runtime._svc required for resolve_artifact_for_ref"
        return self._svc._resolve_artifact_for_ref(session_id, step_id)

    def resolve_artifact_id_for_step(self, session_id: str, step_id: str) -> str | None:
        assert self._svc is not None, "Runtime._svc required for resolve_artifact_id_for_step"
        return self._svc._resolve_artifact_id_for_step(session_id, step_id)

    def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
        assert self._svc is not None, "Runtime._svc required for commit_artifact_with_extraction"
        return self._svc._commit_artifact_with_extraction(*args, **kwargs)

    def insert_step(self, *args: Any, **kwargs: Any) -> None:
        assert self._svc is not None, "Runtime._svc required for insert_step"
        return self._svc._insert_step(*args, **kwargs)

    def resolve_artifact_with_id(
        self, session_id: str, step_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        assert self._svc is not None, "Runtime._svc required for resolve_artifact_with_id"
        return self._svc._resolve_artifact_with_id(session_id, step_id)

    def insert_artifact(self, *args: Any, **kwargs: Any) -> str:
        assert self._svc is not None, "Runtime._svc required for insert_artifact"
        return self._svc._insert_artifact(*args, **kwargs)

    def resolve_engine_for_session(self, *args: Any, **kwargs: Any) -> Any:
        assert self._svc is not None, "Runtime._svc required for resolve_engine_for_session"
        return self._svc._resolve_engine_for_session(*args, **kwargs)

    def resolve_engine(self, *args: Any, **kwargs: Any) -> Any:
        assert self._svc is not None, "Runtime._svc required for resolve_engine"
        return self._svc._resolve_engine(*args, **kwargs)

    def resolve_windowed_query_time_axis(self, *args: Any, **kwargs: Any) -> None:
        assert self._svc is not None, "Runtime._svc required for resolve_windowed_query_time_axis"
        return self._svc._resolve_windowed_query_time_axis(*args, **kwargs)

    def build_scoped_query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        assert self._svc is not None, "Runtime._svc required for build_scoped_query"
        return self._svc._build_scoped_query(*args, **kwargs)

    # --- Intent use-cases (still proxying through svc) ---

    @property
    def svc(self) -> SemanticLayerService:
        """Return the backing service, asserting it has been wired."""
        assert self._svc is not None, "MarivoRuntime.svc accessed before wiring"
        return self._svc

    def observe(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "observe", params)

    def compare(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "compare", params)

    def decompose(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "decompose", params)

    def correlate(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "correlate", params)

    def detect(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "detect", params)

    def test(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "test", params)

    def forecast(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "forecast", params)

    def attribute(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "attribute", params)

    def diagnose(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "diagnose", params)

    def validate(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.svc.run_intent(session_id, "validate", params)

    # --- Session lifecycle (ports-based, svc fallback) ---

    def create_session(self, goal: str, **kwargs: Any) -> SessionId | dict[str, Any]:
        """Create a new session.

        Uses ports.session_store when available. Falls back to svc
        when the session store adapter does not implement the
        event-sourced interface (e.g. SqlSessionStoreAdapter).
        """
        try:
            session_id = SessionId(f"sess-{uuid.uuid4().hex[:12]}")
            event = SessionEvent(
                session_id=session_id,
                event_type="session_created",
                timestamp=_iso_now(),
                payload={"goal": goal, **kwargs},
                actor=None,
            )
            self._ports.session_store.append_event(session_id, event)
            return session_id
        except NotImplementedError:
            # Session store adapter not yet event-sourced; delegate to svc
            assert self._svc is not None, (
                "Runtime.create_session: session_store raised NotImplementedError "
                "and no svc fallback is available"
            )
            return self._svc.create_session(goal, **kwargs)

    def get_session(self, session_id: str | SessionId) -> SessionState | dict[str, Any] | None:
        """Get session state by ID.

        Uses ports.session_store when available. Falls back to svc
        when the session store adapter does not implement the
        event-sourced interface.
        """
        sid = SessionId(session_id) if isinstance(session_id, str) else session_id
        try:
            events = self._ports.session_store.load_events(sid)
            if not events:
                return None
            return rebuild_session_state(events)
        except NotImplementedError:
            assert self._svc is not None, (
                "Runtime.get_session: session_store raised NotImplementedError "
                "and no svc fallback is available"
            )
            return self._svc.get_session(str(sid))

    def terminate_session(
        self, session_id: str | SessionId, **kwargs: Any
    ) -> dict[str, Any] | None:
        """Terminate a session.

        Uses ports.session_store when available. Falls back to svc
        when the session store adapter does not implement the
        event-sourced interface.
        """
        sid = SessionId(session_id) if isinstance(session_id, str) else session_id
        try:
            event = SessionEvent(
                session_id=sid,
                event_type="session_terminated",
                timestamp=_iso_now(),
                payload={},
                actor=None,
            )
            self._ports.session_store.append_event(sid, event)
            return None
        except NotImplementedError:
            assert self._svc is not None, (
                "Runtime.terminate_session: session_store raised NotImplementedError "
                "and no svc fallback is available"
            )
            return self._svc.terminate_session(str(sid), **kwargs)

    def get_session_state(
        self, session_id: str | SessionId, **kwargs: Any
    ) -> SessionState | dict[str, Any] | None:
        """Get session state view by ID.

        Uses ports.session_store when available. Falls back to svc
        when the session store adapter does not implement the
        event-sourced interface.
        """
        sid = SessionId(session_id) if isinstance(session_id, str) else session_id
        try:
            events = self._ports.session_store.load_events(sid)
            if not events:
                return None
            return rebuild_session_state(events)
        except NotImplementedError:
            assert self._svc is not None, (
                "Runtime.get_session_state: session_store raised NotImplementedError "
                "and no svc fallback is available"
            )
            return self._svc.get_session_state(str(sid), kwargs)

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        return self._ports.model_store.get(selector)

    def save_semantic_model(self, model: SemanticModel, *, actor: UserId) -> ModelId:
        return self._ports.model_store.save(model, actor=actor, expected_revision=None)

    def list_semantic_models(self, query: Any) -> list[ModelSummary]:
        return self._ports.model_store.list(query)

    # --- Datasource ops ---
    # discover_catalog removed (4b-3): MCP tools call
    # ports.data_source.schema() directly.
