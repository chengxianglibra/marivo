"""Legacy semantic_runtime.repository stubs — preserved for import compatibility."""

from __future__ import annotations

from typing import Any

from app.semantic_runtime.resolution import ResolvedSemanticObject
from app.storage.metadata import MetadataStore


class SemanticRuntimeRepository:
    """Stub — returns defaults during OSI v2 migration.  See Task 7."""

    def __init__(
        self,
        metadata: MetadataStore,
        *,
        resolver: Any = None,
        planner_context_provider: Any = None,
        **_kwargs: Any,
    ) -> None:
        self.metadata = metadata
        self.resolver = resolver or _StubResolver()
        self.planner_context_provider = planner_context_provider

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def inspect_ref(self, semantic_ref: str) -> Any:
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def resolve_entity_ref(self, entity_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Entity ref not found: {entity_ref}")

    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Metric ref not found: {metric_ref}")

    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Process ref not found: {process_ref}")

    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Dimension ref not found: {dimension_ref}")

    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Time ref not found: {time_ref}")

    def resolve_binding_ref(self, binding_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Binding ref not found: {binding_ref}")

    def resolve_relationship_ref(self, relationship_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Relationship ref not found: {relationship_ref}")

    def resolve_predicate_ref(self, predicate_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Predicate ref not found: {predicate_ref}")

    def resolve_metric(self, metric_name: str) -> Any:
        return None

    def resolve_entity(self, entity_name: str) -> Any:
        return None

    def resolve_metric_sql(self, metric_name: str) -> str | None:
        return None

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        return None

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        return {}


class _StubResolver:
    """Minimal resolver stub so SemanticRuntimeRepository.resolver is not None."""

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def resolve_metric(self, metric_name: str) -> Any:
        return None

    def resolve_entity(self, entity_name: str) -> Any:
        return None
