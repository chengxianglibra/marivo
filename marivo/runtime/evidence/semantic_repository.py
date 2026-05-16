"""Dataset-native semantic runtime repository."""

from __future__ import annotations

import json
from typing import Any

from marivo.adapters.metadata import MetadataStore
from marivo.core.semantic.resolution import ResolvedSemanticObject, RuntimeSemanticAvailability
from marivo.runtime.errors import SemanticRuntimeNotFoundError


def _extract_ansi_sql(expression_json: str) -> str | None:
    try:
        expr = json.loads(expression_json)
    except (json.JSONDecodeError, TypeError):
        return None
    dialects = expr.get("dialects") if isinstance(expr, dict) else None
    if not isinstance(dialects, list):
        return None
    for d in dialects:
        if isinstance(d, dict) and d.get("dialect") == "ANSI_SQL":
            sql = d.get("expression")
            if isinstance(sql, str):
                return sql
    for d in dialects:
        if isinstance(d, dict) and isinstance(d.get("expression"), str):
            return str(d["expression"])
    return None


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
        metric_name = metric_ref.removeprefix("metric.")
        metric_row = self._query_metric(metric_name)
        if metric_row is None:
            raise SemanticRuntimeNotFoundError(
                f"Metric ref not found: {metric_ref}",
                semantic_ref=metric_ref,
            )
        model_id = metric_row["model_id"]
        dataset_info = self._query_dataset(model_id)
        dimensions = self._query_dimensions(model_id)
        definition_sql = _extract_ansi_sql(metric_row["expression"] or "{}")
        if definition_sql is None:
            raise SemanticRuntimeNotFoundError(
                f"Metric ref has no parseable ANSI SQL expression: {metric_ref}",
                semantic_ref=metric_ref,
            )
        additive_dims = None
        if metric_row.get("additive_dimensions"):
            try:
                additive_dims = json.loads(metric_row["additive_dimensions"])
            except (json.JSONDecodeError, TypeError):
                additive_dims = None
        payload: dict[str, Any] = {
            "definition_sql": definition_sql,
            "_dataset_grounding_ready": dataset_info is not None,
            "dimensions": dimensions,
        }
        if dataset_info is not None:
            payload["dataset_source"] = dataset_info["source"]
            payload["datasource_id"] = dataset_info["datasource_id"]
        header: dict[str, Any] = {
            "metric_ref": metric_ref,
            "additive_dimensions": additive_dims or [],
            "aggregation_semantics": metric_row.get("aggregation_semantics") or "sum",
        }
        return ResolvedSemanticObject(
            object_kind="metric",
            object_id=metric_ref,
            ref=metric_ref,
            semantic_object={"header": header, "payload": payload},
            status="published",
            revision=1,
            created_at=metric_row.get("created_at", ""),
            updated_at=metric_row.get("updated_at", ""),
        )

    def _query_metric(self, metric_name: str) -> dict[str, Any] | None:
        rows = self.metadata.query_rows(
            "SELECT m.*, sm.visibility FROM semantic_metrics m "
            "JOIN semantic_models sm ON m.model_id = sm.model_id "
            "WHERE m.name = ? ORDER BY sm.visibility ASC",
            [metric_name],
        )
        if rows:
            return rows[0]
        return None

    def _query_dataset(self, model_id: int) -> dict[str, Any] | None:
        rows = self.metadata.query_rows(
            "SELECT source, datasource_id FROM semantic_datasets "
            "WHERE model_id = ? AND datasource_id IS NOT NULL AND datasource_id != '' "
            "LIMIT 1",
            [model_id],
        )
        if rows:
            return rows[0]
        return None

    def _query_dimensions(self, model_id: int) -> list[str]:
        rows = self.metadata.query_rows(
            "SELECT DISTINCT f.name FROM semantic_fields f "
            "JOIN semantic_datasets d ON f.dataset_id = d.dataset_id "
            "WHERE d.model_id = ? AND f.is_dimension = 1 "
            "ORDER BY f.name",
            [model_id],
        )
        return [r["name"] for r in rows]

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
        try:
            return self.resolve_metric_ref(f"metric.{metric_name}")
        except SemanticRuntimeNotFoundError:
            return None

    def resolve_metric_sql(self, metric_name: str) -> str | None:
        try:
            resolved = self.resolve_metric_ref(f"metric.{metric_name}")
        except SemanticRuntimeNotFoundError:
            return None
        payload = resolved.semantic_object.get("payload") or {}
        return payload.get("definition_sql")

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        try:
            resolved = self.resolve_metric_ref(f"metric.{metric_name}")
        except SemanticRuntimeNotFoundError:
            return None
        payload = resolved.semantic_object.get("payload") or {}
        dims = payload.get("dimensions")
        if isinstance(dims, list):
            return [str(d) for d in dims]
        return []

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
