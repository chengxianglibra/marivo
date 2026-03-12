from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.analysis_core import SUPPORTED_STEP_TYPES
from app.semantic_runtime.planner_context import PlannerContextProvider
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.bindings import BindingService


class CatalogRuntimeService:
    """Runtime helpers for semantic catalog search, resolution, and planning."""

    def __init__(
        self,
        metadata: MetadataStore,
        binding_service: BindingService | None = None,
    ) -> None:
        self.metadata = metadata
        self.binding_service = binding_service
        self.planner_context_provider = PlannerContextProvider(metadata)

    def search(self, query: str, object_type: str | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        pattern = f"%{query}%"

        if object_type is None or object_type == "entity":
            rows = self.metadata.query_rows(
                """
                SELECT entity_id, name, display_name, description, status
                FROM semantic_entities
                WHERE status = 'published'
                  AND (name LIKE ? OR display_name LIKE ? OR description LIKE ?)
                ORDER BY name
                """,
                [pattern, pattern, pattern],
            )
            results.extend(
                {
                    "type": "entity",
                    "id": row["entity_id"],
                    "name": row["name"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "status": row["status"],
                }
                for row in rows
            )

        if object_type is None or object_type == "metric":
            rows = self.metadata.query_rows(
                """
                SELECT metric_id, name, display_name, description, definition_sql, status
                FROM semantic_metrics
                WHERE status = 'published'
                  AND (name LIKE ? OR display_name LIKE ? OR description LIKE ? OR definition_sql LIKE ?)
                ORDER BY name
                """,
                [pattern, pattern, pattern, pattern],
            )
            results.extend(
                {
                    "type": "metric",
                    "id": row["metric_id"],
                    "name": row["name"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "definition_sql": row["definition_sql"],
                    "status": row["status"],
                }
                for row in rows
            )

        if object_type is None or object_type == "asset":
            rows = self.metadata.query_rows(
                """
                SELECT object_id, native_name, object_type, fqn, source_id, synced_at
                FROM source_objects
                WHERE object_type = 'table'
                  AND (native_name LIKE ? OR fqn LIKE ?)
                ORDER BY fqn
                """,
                [pattern, pattern],
            )
            results.extend(
                {
                    "type": "asset",
                    "id": row["object_id"],
                    "name": row["native_name"],
                    "display_name": row["native_name"],
                    "fqn": row["fqn"],
                    "source_id": row["source_id"],
                    "synced_at": row["synced_at"],
                }
                for row in rows
            )

        return results

    def resolve(self, name: str) -> dict[str, Any]:
        metric_row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE name = ?",
            [name],
        )
        if metric_row is not None:
            mappings = self.metadata.query_rows(
                "SELECT * FROM semantic_mappings WHERE semantic_type = 'metric' AND semantic_id = ?",
                [metric_row["metric_id"]],
            )
            return {
                "resolved_type": "metric",
                "semantic_object": {
                    "metric_id": metric_row["metric_id"],
                    "name": metric_row["name"],
                    "display_name": metric_row["display_name"],
                    "description": metric_row["description"],
                    "definition_sql": metric_row["definition_sql"],
                    "dimensions": json.loads(metric_row["dimensions_json"]),
                    "status": metric_row["status"],
                    "revision": metric_row["revision"],
                },
                "physical_assets": self._resolve_mappings(mappings),
                "mappings": [self._mapping_row_to_dict(mapping) for mapping in mappings],
            }

        entity_row = self.metadata.query_one(
            "SELECT * FROM semantic_entities WHERE name = ?",
            [name],
        )
        if entity_row is not None:
            mappings = self.metadata.query_rows(
                "SELECT * FROM semantic_mappings WHERE semantic_type = 'entity' AND semantic_id = ?",
                [entity_row["entity_id"]],
            )
            return {
                "resolved_type": "entity",
                "semantic_object": {
                    "entity_id": entity_row["entity_id"],
                    "name": entity_row["name"],
                    "display_name": entity_row["display_name"],
                    "description": entity_row["description"],
                    "keys": json.loads(entity_row["keys_json"]),
                    "status": entity_row["status"],
                    "revision": entity_row["revision"],
                },
                "physical_assets": self._resolve_mappings(mappings),
                "mappings": [self._mapping_row_to_dict(mapping) for mapping in mappings],
            }

        raise KeyError(f"Could not resolve term: {name}")

    def planner_context(self, session_id: str) -> dict[str, Any]:
        context = self.planner_context_provider.build_planner_context(session_id)
        session = context.pop("session", None)
        return {
            "session_id": session["session_id"] if session else session_id,
            "metrics": context["metrics"],
            "entities": context["entities"],
            "available_step_types": list(SUPPORTED_STEP_TYPES),
            "policies": [
                "Results are aggregate-only.",
                "Evidence graph keeps support and contradiction links for every claim.",
            ],
        }

    def _resolve_mappings(self, mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assets = []
        for mapping in mappings:
            obj = self.metadata.query_one(
                "SELECT * FROM source_objects WHERE object_id = ?",
                [mapping["object_id"]],
            )
            if obj is None:
                continue

            asset: dict[str, Any] = {
                "object_id": obj["object_id"],
                "native_name": obj["native_name"],
                "fqn": obj["fqn"],
                "object_type": obj["object_type"],
                "source_id": obj["source_id"],
                "synced_at": obj["synced_at"],
                "properties": json.loads(obj["properties_json"]),
            }
            if self.binding_service is not None:
                engines = self.binding_service.get_engines_for_source(obj["source_id"])
                if engines:
                    best = engines[0]
                    asset["engine"] = {
                        "engine_id": best["engine_id"],
                        "engine_type": best["engine_type"],
                        "display_name": best["display_name"],
                        "priority": best["priority"],
                        "namespace": best.get("namespace", {}),
                    }
                else:
                    asset["engine"] = None
            assets.append(asset)
        return assets

    @staticmethod
    def _mapping_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "mapping_id": row["mapping_id"],
            "semantic_type": row["semantic_type"],
            "semantic_id": row["semantic_id"],
            "object_id": row["object_id"],
            "mapping_type": row["mapping_type"],
            "mapping_json": json.loads(row["mapping_json"]),
        }
