"""Dataset-native semantic runtime repository."""

from __future__ import annotations

from typing import Any

from marivo.adapters.metadata import MetadataStore
from marivo.core.semantic.resolution import ResolvedSemanticObject, RuntimeSemanticAvailability
from marivo.runtime.errors import SemanticRuntimeNotFoundError


class SemanticRuntimeRepository:
    """Resolve runtime-visible semantic refs from the current metadata schema."""

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
        if semantic_ref.startswith("metric."):
            return self.resolve_metric_ref(semantic_ref)
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def inspect_ref(self, semantic_ref: str) -> Any:
        resolved = self.resolve_ref(semantic_ref)
        blockers: list[dict[str, Any]] = []
        if resolved.object_kind == "metric":
            semantic_object = resolved.semantic_object
            payload = semantic_object.get("payload") or {}
            if not payload.get("_dataset_grounding_ready"):
                blockers.append(
                    {
                        "code": "DATASET_GROUNDING_MISSING",
                        "message": "Metric has no dataset-native execution source.",
                        "subject_ref": semantic_ref,
                    }
                )
        return RuntimeSemanticAvailability(
            resolved=resolved,
            lifecycle_status="active",
            readiness_status="not_ready" if blockers else "ready",
            blocking_requirements=blockers,
            capabilities={"grounding": "dataset_native"},
            dependency_refs=[],
        )

    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        raise SemanticRuntimeNotFoundError(
            f"Metric ref not found: {metric_ref}",
            semantic_ref=metric_ref,
        )

    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Process ref not found: {process_ref}")

    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        return self._resolved_physical_dimension(dimension_ref)

    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Time ref not found: {time_ref}")

    def resolve_binding_ref(self, binding_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError(
            "binding_grounding_removed: v2 runtime uses dataset.datasource_id, "
            "dataset.source, and field.expression"
        )

    def resolve_relationship_ref(self, relationship_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Relationship ref not found: {relationship_ref}")

    def resolve_predicate_ref(self, predicate_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Predicate ref not found: {predicate_ref}")

    def resolve_metric(self, metric_name: str) -> Any:
        return None

    def resolve_metric_sql(self, metric_name: str) -> str | None:
        return None

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        return None

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        return {}

    def _resolved_physical_dimension(self, dimension_ref: str) -> ResolvedSemanticObject:
        now = ""
        physical_name = dimension_ref.removeprefix("dimension.")
        return ResolvedSemanticObject(
            object_kind="dimension",
            object_id=dimension_ref,
            ref=dimension_ref,
            semantic_object={
                "header": {
                    "dimension_ref": dimension_ref,
                    "display_name": physical_name.replace("_", " ").title(),
                },
                "payload": {
                    "physical_name": physical_name,
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "open",
                    },
                },
            },
            status="published",
            revision=1,
            created_at=now,
            updated_at=now,
        )


class _StubResolver:
    """Minimal resolver stub so SemanticRuntimeRepository.resolver is not None."""

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        raise KeyError(f"Semantic ref not found: {semantic_ref}")

    def resolve_metric(self, metric_name: str) -> Any:
        return None
