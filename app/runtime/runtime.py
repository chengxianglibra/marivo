from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.contracts.ids import ModelId, SessionId, StepId, UserId
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

    Phase 4b-2: I/O proxy methods use ports.artifact_store / ports.step_store
    as the primary path.  Semantic/routing methods still delegate to
    self._svc until the semantic and routing ports are defined (future phase).
    When artifact_store or step_store is None (local mode without adapters),
    the methods fall back to self._svc.
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
    ) -> None:
        self._ports = ports
        self._core = core
        self._svc: SemanticLayerService | None = None  # set via wire_svc()
        self._semantic_v2_svc: Any = None  # set via wire_semantic_v2_svc()
        self._datasource_svc: Any = None  # set via wire_datasource_svc()

    def wire_svc(self, svc: SemanticLayerService) -> None:
        """Attach the backing service for I/O proxy + intent methods.

        Temporary bridge until all I/O proxy methods are fully
        port-ified.  After that, this method and all self._svc
        references will be removed.
        """
        self._svc = svc

    def wire_semantic_v2_svc(self, svc: Any) -> None:
        """Attach the SemanticModelV2Service for V2 CRUD operations."""
        self._semantic_v2_svc = svc

    def wire_datasource_svc(self, svc: Any) -> None:
        """Attach the DatasourceService for datasource operations."""
        self._datasource_svc = svc

    @property
    def core(self) -> CoreEngine:
        """Pure computation facade (no I/O)."""
        return self._core

    @property
    def ports(self) -> RuntimePorts:
        """Typed container for all port implementations."""
        return self._ports

    @property
    def semantic_v2_svc(self) -> Any:
        """SemanticModelV2Service for V2 CRUD operations."""
        return self._semantic_v2_svc

    @property
    def datasource_svc(self) -> Any:
        """DatasourceService for datasource operations."""
        return self._datasource_svc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_svc(self, method_name: str) -> SemanticLayerService:
        """Return self._svc, asserting it has been wired."""
        assert self._svc is not None, f"Runtime.{method_name} requires _svc (not yet wired)"
        return self._svc

    # ------------------------------------------------------------------
    # Artifact / Step I/O  (ports-first, svc fallback)
    # ------------------------------------------------------------------

    def resolve_artifact_for_ref(self, session_id: str, step_id: str) -> dict[str, Any] | None:
        """Return the content of the most recent committed artifact for a step ref."""
        store = self._ports.artifact_store
        if store is not None:
            try:
                return store.resolve_artifact_for_ref(SessionId(session_id), StepId(step_id))
            except NotImplementedError:
                pass  # fall through to svc
        return self._require_svc("resolve_artifact_for_ref")._resolve_artifact_for_ref(
            session_id, step_id
        )

    def resolve_artifact_id_for_step(self, session_id: str, step_id: str) -> str | None:
        """Return the artifact_id of the most recent committed artifact for a step."""
        store = self._ports.artifact_store
        if store is not None:
            try:
                result = store.resolve_artifact_id_for_step(SessionId(session_id), StepId(step_id))
                return str(result) if result is not None else None
            except NotImplementedError:
                pass
        return self._require_svc("resolve_artifact_id_for_step")._resolve_artifact_id_for_step(
            session_id, step_id
        )

    def resolve_artifact_with_id(
        self, session_id: str, step_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return (artifact_id, content) for the most recent committed artifact."""
        store = self._ports.artifact_store
        if store is not None:
            try:
                result = store.resolve_artifact_with_id(SessionId(session_id), StepId(step_id))
                if result is None:
                    return None
                artifact_id, content = result
                return str(artifact_id), content
            except NotImplementedError:
                pass
        return self._require_svc("resolve_artifact_with_id")._resolve_artifact_with_id(
            session_id, step_id
        )

    def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
        """Canonical commit boundary for mandatory-extraction artifacts."""
        store = self._ports.artifact_store
        if store is not None:
            try:
                result = store.commit_artifact_with_extraction(*args, **kwargs)
                return str(result)
            except NotImplementedError:
                pass
        return self._require_svc(
            "commit_artifact_with_extraction"
        )._commit_artifact_with_extraction(*args, **kwargs)

    def insert_step(self, *args: Any, **kwargs: Any) -> None:
        """Insert a step record."""
        store = self._ports.step_store
        if store is not None:
            try:
                store.insert_step(*args, **kwargs)
                return
            except NotImplementedError:
                pass
        self._require_svc("insert_step")._insert_step(*args, **kwargs)

    def insert_artifact(self, *args: Any, **kwargs: Any) -> str:
        """Insert a raw artifact (no extraction boundary)."""
        store = self._ports.artifact_store
        if store is not None:
            try:
                result = store.insert_artifact(*args, **kwargs)
                return str(result)
            except NotImplementedError:
                pass
        return self._require_svc("insert_artifact")._insert_artifact(*args, **kwargs)

    # ------------------------------------------------------------------
    # Semantic / Routing I/O  (svc-required, ports fallback future)
    # ------------------------------------------------------------------
    # These methods are deeply coupled to SemanticLayerService internals
    # (semantic_repository, routing_runtime, compiler, etc.).
    # They still delegate to self._svc until dedicated port protocols
    # are defined for semantic resolution and routing.

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        return self._require_svc(
            "resolve_metric_execution_context"
        )._resolve_metric_execution_context(*args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        return self._require_svc("compile_step")._compile_step_with_feedback(*args, **kwargs)

    def resolve_metric_dimensions(self, metric_ref: str) -> list[str] | None:
        return self._require_svc("resolve_metric_dimensions").resolve_metric_dimensions(metric_ref)

    def resolve_metric(self, metric_name: str) -> Any:
        return self._require_svc("resolve_metric").semantic_repository.resolve_metric(metric_name)

    def resolve_metric_table(self, metric_name: str, **kwargs: Any) -> str | None:
        return self._require_svc("resolve_metric_table")._resolve_metric_table(
            metric_name, **kwargs
        )

    def resolve_metric_sql_for_execution(self, *args: Any, **kwargs: Any) -> str:
        return self._require_svc(
            "resolve_metric_sql_for_execution"
        ).resolve_metric_sql_for_execution(*args, **kwargs)

    def resolve_metric_value_sql_for_execution(self, *args: Any, **kwargs: Any) -> str | None:
        return self._require_svc(
            "resolve_metric_value_sql_for_execution"
        ).resolve_metric_value_sql_for_execution(*args, **kwargs)

    def resolve_scope_constraint_column(self, *args: Any, **kwargs: Any) -> str:
        return self._require_svc(
            "resolve_scope_constraint_column"
        )._resolve_scope_constraint_column(*args, **kwargs)

    def resolve_engine_for_session(self, *args: Any, **kwargs: Any) -> Any:
        return self._require_svc("resolve_engine_for_session")._resolve_engine_for_session(
            *args, **kwargs
        )

    def resolve_engine(self, *args: Any, **kwargs: Any) -> Any:
        return self._require_svc("resolve_engine")._resolve_engine(*args, **kwargs)

    def resolve_windowed_query_time_axis(self, *args: Any, **kwargs: Any) -> None:
        return self._require_svc(
            "resolve_windowed_query_time_axis"
        )._resolve_windowed_query_time_axis(*args, **kwargs)

    def build_scoped_query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._require_svc("build_scoped_query")._build_scoped_query(*args, **kwargs)

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

    def list_sessions(
        self,
        status: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List sessions matching optional filters."""
        return self._require_svc("list_sessions").list_sessions(
            status=status,
            session_id=session_id,
            limit=limit,
            page_token=page_token,
        )

    def query_session_state(self, session_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical SessionStateView with a structured query body."""
        return self._require_svc("query_session_state").query_session_state(session_id, query)

    def get_proposition_context(self, session_id: str, proposition_id: str) -> dict[str, Any]:
        """Return PropositionContextView for a proposition."""
        return self._require_svc("get_proposition_context").get_proposition_context(
            session_id, proposition_id
        )

    def discover_catalog(self) -> dict[str, Any]:
        """Return the API catalog of entities, models, and datasources."""
        return self._require_svc("discover_catalog").discover_catalog()

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        return self._ports.model_store.get(selector)

    def save_semantic_model(self, model: SemanticModel, *, actor: UserId) -> ModelId:
        return self._ports.model_store.save(model, actor=actor, expected_revision=None)

    def list_semantic_models(self, query: Any) -> list[ModelSummary]:
        return self._ports.model_store.list(query)

    # --- Datasource ops ---
