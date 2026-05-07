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

    def normalize_intent_metric_ref(self, metric_ref: str) -> str:
        return self._svc.normalize_intent_metric_ref(metric_ref)

    def metric_name_from_ref(self, metric_ref: str) -> str:
        return self._svc.metric_name_from_ref(metric_ref)

    def resolve_metric_dimensions(self, metric_ref: str) -> list[str] | None:
        return self._svc.resolve_metric_dimensions(metric_ref)

    def resolve_metric(self, metric_name: str) -> Any:
        """Resolve a metric from the semantic repository."""
        return self._svc.semantic_repository.resolve_metric(metric_name)

    def resolve_metric_table(self, metric_name: str, **kwargs: Any) -> str | None:
        """Return the source table for a metric."""
        return self._svc._resolve_metric_table(metric_name, **kwargs)

    def resolve_metric_sql_for_execution(self, *args: Any, **kwargs: Any) -> str:
        return self._svc.resolve_metric_sql_for_execution(*args, **kwargs)

    def resolve_metric_value_sql_for_execution(self, *args: Any, **kwargs: Any) -> str | None:
        return self._svc.resolve_metric_value_sql_for_execution(*args, **kwargs)

    # TODO(3c): I/O methods below — move to ports once session_store/evidence_store
    # adapters support artifact + step persistence.

    def new_step_id(self) -> str:
        """Generate a new unique step ID."""
        return self._svc._new_step_id()

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

    # TODO(3c): I/O method — returns (artifact_id, artifact) tuple.

    def resolve_artifact_with_id(
        self, session_id: str, step_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return (artifact_id, artifact_content) for the most recent committed artifact."""
        return self._svc._resolve_artifact_with_id(session_id, step_id)

    # TODO(3c): I/O methods — engine resolution and query building.

    def resolve_engine_for_session(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc._resolve_engine_for_session(*args, **kwargs)

    def resolve_engine(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc._resolve_engine(*args, **kwargs)

    def resolve_windowed_query_time_axis(self, *args: Any, **kwargs: Any) -> None:
        return self._svc._resolve_windowed_query_time_axis(*args, **kwargs)

    def build_scoped_query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._svc._build_scoped_query(*args, **kwargs)

    def make_provenance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._svc._make_provenance(*args, **kwargs)

    def resolve_scope_constraint_column(self, *args: Any, **kwargs: Any) -> str:
        """Resolve a scope constraint dimension to its physical column expression."""
        return self._svc._resolve_scope_constraint_column(*args, **kwargs)

    def insert_artifact(self, *args: Any, **kwargs: Any) -> str:
        """Insert an artifact record and return its ID."""
        return self._svc._insert_artifact(*args, **kwargs)
