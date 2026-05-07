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

    Phase 3a: proxies to SemanticLayerService.
    Phase 3b+: intent runners use (core, ports, session_id, params).
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
        svc: SemanticLayerService | None = None,
    ) -> None:
        self._ports = ports
        self._core = core
        self._svc = svc  # Phase 3a: retained for proxying

    @property
    def svc(self) -> SemanticLayerService:
        """Return the backing service, asserting it has been wired."""
        assert self._svc is not None, "MarivoRuntime.svc accessed before wiring"
        return self._svc

    # --- Intent use-cases ---

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

    def terminate_session(self, session_id: str) -> None:
        self.svc.terminate_session(session_id)

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
