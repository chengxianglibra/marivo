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

    # --- Phase 3b: proxies used by migrated intent runners ---

    def new_step_id(self) -> str:
        """Generate a new unique step ID."""
        return self._svc._new_step_id()

    # TODO(3c): I/O methods below — move to ports once session_store/evidence_store
    # adapters support artifact + step persistence.

    def resolve_artifact_for_ref(self, session_id: str, step_id: str) -> dict[str, Any] | None:
        """Return the content of the most recent committed artifact for a step ref."""
        return self._svc._resolve_artifact_for_ref(session_id, step_id)

    def resolve_artifact_id_for_step(self, session_id: str, step_id: str) -> str | None:
        """Return the artifact_id of the most recent committed artifact for a step."""
        return self._svc._resolve_artifact_id_for_step(session_id, step_id)

    def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
        """Commit an artifact with mandatory extraction."""
        return self._svc._commit_artifact_with_extraction(*args, **kwargs)

    def insert_step(self, *args: Any, **kwargs: Any) -> None:
        """Insert a step record into the session."""
        return self._svc._insert_step(*args, **kwargs)
