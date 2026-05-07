from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.contracts.ids import ModelId, UserId
from app.contracts.semantic import ModelSummary, SemanticModel

if TYPE_CHECKING:
    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts
    from app.service import SemanticLayerService


class MarivoRuntime:
    """Use-case facade for the Marivo platform.

    Phase 4b-1: I/O proxy methods moved from CoreEngine; delegate to
    self._svc temporarily until ports fully support artifact + step
    persistence.  4b-2 will migrate intent runners to call ports.*
    directly, removing these helpers.
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
        svc: SemanticLayerService | None = None,
    ) -> None:
        self._ports = ports
        self._core = core
        self._svc = svc  # Temporary: I/O proxy methods delegate here

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

    # --- Session lifecycle ---

    def create_session(self, goal: str, **kwargs: Any) -> Any:
        return self.svc.create_session(goal, **kwargs)

    def get_session(self, session_id: str) -> Any:
        return self.svc.get_session(session_id)

    def terminate_session(self, session_id: str, **kwargs: Any) -> Any:
        return self.svc.terminate_session(session_id, **kwargs)

    def get_session_state(self, session_id: str, **filters: Any) -> dict[str, Any]:
        return self.svc.get_session_state(session_id, filters)

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        return self._ports.model_store.get(selector)

    def save_semantic_model(self, model: SemanticModel, *, actor: UserId) -> ModelId:
        return self._ports.model_store.save(model, actor=actor, expected_revision=None)

    def list_semantic_models(self, query: Any) -> list[ModelSummary]:
        return self._ports.model_store.list(query)

    # --- Datasource ops ---

    def discover_catalog(self) -> dict[str, Any]:
        return self.svc.discover_catalog()
