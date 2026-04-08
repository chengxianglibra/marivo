from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.analysis_core import SUPPORTED_STEP_TYPES
from app.semantic_runtime.repository import SemanticRuntimeRepository
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.bindings import BindingService


class CatalogRuntimeService:
    """Runtime helpers for semantic catalog search, resolution, and planning."""

    def __init__(
        self,
        metadata: MetadataStore,
        binding_service: BindingService | None = None,
        semantic_repository: SemanticRuntimeRepository | None = None,
    ) -> None:
        self.metadata = metadata
        self.binding_service = binding_service
        self.semantic_repository = semantic_repository or SemanticRuntimeRepository(metadata)

    def search(self, query: str, object_type: str | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        pattern = f"%{query}%"

        if object_type is None or object_type == "entity":
            rows = self.metadata.query_rows(
                """
                SELECT entity_contract_id, entity_ref, display_name, description, status
                FROM semantic_entity_contracts
                WHERE status = 'published'
                  AND (entity_ref LIKE ? OR display_name LIKE ? OR description LIKE ?)
                ORDER BY entity_ref
                """,
                [pattern, pattern, pattern],
            )
            results.extend(
                {
                    "type": "entity",
                    "id": row["entity_contract_id"],
                    "name": row["entity_ref"].split(".", 1)[1],
                    "entity_ref": row["entity_ref"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "status": row["status"],
                }
                for row in rows
            )

        if object_type is None or object_type == "metric":
            rows = self.metadata.query_rows(
                """
                SELECT metric_contract_id, metric_ref, display_name, description, status
                FROM semantic_metric_contracts
                WHERE status = 'published'
                  AND (metric_ref LIKE ? OR display_name LIKE ? OR description LIKE ?)
                ORDER BY metric_ref
                """,
                [pattern, pattern, pattern],
            )
            results.extend(
                {
                    "type": "metric",
                    "id": row["metric_contract_id"],
                    "name": row["metric_ref"].split(".", 1)[1],
                    "metric_ref": row["metric_ref"],
                    "display_name": row["display_name"],
                    "description": row["description"],
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
        resolved_metric = self.semantic_repository.resolve_metric(name)
        if resolved_metric is not None:
            metric_id = str(resolved_metric.metadata["metric_id"])
            mappings = self.metadata.query_rows(
                "SELECT * FROM semantic_mappings WHERE semantic_type = 'metric' AND semantic_id = ?",
                [metric_id],
            )
            return {
                "resolved_type": "metric",
                "semantic_object": {
                    "header": {
                        "metric_ref": resolved_metric.metric_ref,
                        "display_name": resolved_metric.display_name,
                        "description": resolved_metric.description,
                        "metric_contract_version": resolved_metric.metric_contract_version,
                    },
                    "identity": {
                        "metric_family": resolved_metric.metric_family,
                        "population_subject_ref": resolved_metric.population_subject_ref,
                        "observed_entity_ref": resolved_metric.observed_entity_ref,
                        "observation_grain_ref": resolved_metric.observation_grain_ref,
                        "sample_kind": resolved_metric.sample_kind,
                        "value_semantics": resolved_metric.value_semantics,
                        "aggregation_scope": resolved_metric.aggregation_scope,
                        "primary_time_ref": resolved_metric.primary_time_ref,
                        "additivity": resolved_metric.additivity,
                    },
                    "family_payload": resolved_metric.family_payload,
                    "metadata": {
                        "metric_id": metric_id,
                        "status": resolved_metric.metadata["status"],
                        "revision": resolved_metric.metadata["revision"],
                        "properties": dict(resolved_metric.metadata["properties"]),
                    },
                    "legacy": {
                        "name": resolved_metric.name,
                        "definition_sql": resolved_metric.definition_sql,
                        "dimensions": list(resolved_metric.dimensions),
                        "grain": resolved_metric.grain,
                        "measure_type": resolved_metric.measure_type,
                        "allowed_dimensions": list(resolved_metric.allowed_dimensions),
                        "lineage": list(resolved_metric.lineage),
                        "quality_expectations": dict(resolved_metric.quality_expectations),
                    },
                },
                "physical_assets": self._resolve_mappings(mappings),
                "mappings": [self._mapping_row_to_dict(mapping) for mapping in mappings],
            }

        resolved_entity = self.semantic_repository.resolve_entity(name)
        if resolved_entity is not None:
            entity_id = str(resolved_entity.metadata["entity_id"])
            mappings = self.metadata.query_rows(
                "SELECT * FROM semantic_mappings WHERE semantic_type = 'entity' AND semantic_id = ?",
                [entity_id],
            )
            return {
                "resolved_type": "entity",
                "semantic_object": {
                    "header": {
                        "entity_ref": resolved_entity.entity_ref,
                        "display_name": resolved_entity.display_name,
                        "description": resolved_entity.description,
                        "entity_contract_version": resolved_entity.entity_contract_version,
                    },
                    "identity": {
                        "key_refs": list(resolved_entity.key_refs),
                        "uniqueness_scope": resolved_entity.uniqueness_scope,
                        "id_stability": resolved_entity.id_stability,
                        "nullable_key_policy": resolved_entity.nullable_key_policy,
                    },
                    "hierarchy": {
                        "parent_entity_ref": resolved_entity.parent_entity_ref,
                        "cardinality_to_parent": resolved_entity.cardinality_to_parent,
                        "ownership_semantics": resolved_entity.ownership_semantics,
                    },
                    "primary_time_ref": resolved_entity.primary_time_ref,
                    "stable_descriptors": list(resolved_entity.stable_descriptors),
                    "metadata": {
                        "entity_id": entity_id,
                        "status": resolved_entity.metadata["status"],
                        "revision": resolved_entity.metadata["revision"],
                        "properties": dict(resolved_entity.metadata["properties"]),
                    },
                    "legacy": {
                        "name": resolved_entity.name,
                        "keys": list(resolved_entity.keys),
                        "level": resolved_entity.level,
                        "join_constraints": dict(resolved_entity.join_constraints),
                        "upstream_dependencies": list(resolved_entity.upstream_dependencies),
                        "lineage": list(resolved_entity.lineage),
                        "quality_expectations": dict(resolved_entity.quality_expectations),
                    },
                },
                "physical_assets": self._resolve_mappings(mappings),
                "mappings": [self._mapping_row_to_dict(mapping) for mapping in mappings],
            }

        raise KeyError(f"Could not resolve term: {name}")

    def planner_context(self, session_id: str) -> dict[str, Any]:
        context = self.semantic_repository.build_planner_context(session_id)
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

    def graph(self, root: str, depth: int = 2) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        visited: set[str] = set()
        self._traverse(root, depth, nodes, edges, visited)
        return {"root": root, "depth": depth, "nodes": list(nodes.values()), "edges": edges}

    def _traverse(
        self,
        node_id: str,
        remaining_depth: int,
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
        visited: set[str],
    ) -> None:
        if node_id in visited or remaining_depth < 0:
            return
        visited.add(node_id)

        node = self._identify_node(node_id)
        if node is not None:
            nodes[node_id] = node

        if remaining_depth == 0:
            return

        metric_rows = self.metadata.query_rows(
            "SELECT metric_id, name FROM semantic_metrics WHERE entity_id = ?",
            [node_id],
        )
        for metric in metric_rows:
            edges.append({"from": node_id, "to": metric["metric_id"], "edge_type": "defines"})
            self._traverse(metric["metric_id"], remaining_depth - 1, nodes, edges, visited)

        mapping_rows = self.metadata.query_rows(
            "SELECT * FROM semantic_mappings WHERE semantic_id = ?",
            [node_id],
        )
        for mapping in mapping_rows:
            edges.append({"from": node_id, "to": mapping["object_id"], "edge_type": "maps_to"})
            self._traverse(mapping["object_id"], remaining_depth - 1, nodes, edges, visited)

        child_rows = self.metadata.query_rows(
            "SELECT object_id, native_name, object_type FROM source_objects WHERE parent_id = ?",
            [node_id],
        )
        for child in child_rows:
            edges.append({"from": node_id, "to": child["object_id"], "edge_type": "contains"})
            self._traverse(child["object_id"], remaining_depth - 1, nodes, edges, visited)

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

    def _identify_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entities WHERE entity_id = ?", [node_id]
        )
        if row is not None:
            return {
                "id": node_id,
                "type": "entity",
                "name": row["name"],
                "display_name": row["display_name"],
            }

        row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE metric_id = ?", [node_id]
        )
        if row is not None:
            return {
                "id": node_id,
                "type": "metric",
                "name": row["name"],
                "display_name": row["display_name"],
            }

        row = self.metadata.query_one("SELECT * FROM source_objects WHERE object_id = ?", [node_id])
        if row is not None:
            return {
                "id": node_id,
                "type": row["object_type"],
                "name": row["native_name"],
                "fqn": row["fqn"],
            }

        return None

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
