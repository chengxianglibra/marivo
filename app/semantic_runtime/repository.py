from __future__ import annotations

from typing import Any

from app.semantic_runtime.planner_context import PlannerContextProvider
from app.semantic_runtime.resolution import (
    ResolvedEntity,
    ResolvedMetric,
    SemanticResolver,
)
from app.storage.metadata import MetadataStore


class SemanticRuntimeRepository:
    """Unified runtime facade for resolving semantic objects and planner context."""

    def __init__(
        self,
        metadata: MetadataStore,
        *,
        resolver: SemanticResolver | None = None,
        planner_context_provider: PlannerContextProvider | None = None,
    ) -> None:
        self.metadata = metadata
        self.resolver = resolver or SemanticResolver(metadata)
        self.planner_context_provider = planner_context_provider or PlannerContextProvider(metadata)

    def resolve_metric(self, metric_name: str) -> ResolvedMetric | None:
        return self.resolver.resolve_metric(metric_name)

    def resolve_entity(self, entity_name: str) -> ResolvedEntity | None:
        return self.resolver.resolve_entity(entity_name)

    def resolve_metric_sql(self, metric_name: str) -> str | None:
        resolved = self.resolve_metric(metric_name)
        return resolved.definition_sql if resolved else None

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        resolved = self.resolve_metric(metric_name)
        return list(resolved.dimensions) if resolved else None

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        return self.planner_context_provider.build_planner_context(session_id)
