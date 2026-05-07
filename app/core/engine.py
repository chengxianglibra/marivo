from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.service import SemanticLayerService


class CoreEngine:
    """Phase 3a: proxies to SemanticLayerService for domain computation.
    Phase 3c: replaced with real core modules."""

    def __init__(self, svc: SemanticLayerService) -> None:
        self._svc = svc

    # --- Pure domain computation proxies ---

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc._resolve_metric_execution_context(*args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc._compile_step_with_feedback(*args, **kwargs)

    def build_step_semantic_metadata(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc.build_step_semantic_metadata(*args, **kwargs)
