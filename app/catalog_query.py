from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.bindings import BindingService
    from app.service import SemanticLayerService


class CatalogQueryService:
    """Search, resolve, planner-context, and graph traversal over the
    semantic and physical catalog."""

    def __init__(
        self,
        metadata: MetadataStore,
        binding_service: BindingService | None = None,
    ) -> None:
        self.metadata = metadata
        self.binding_service = binding_service

    # ── Search ───────────────────────────────────────────────────

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
            for row in rows:
                results.append({
                    "type": "entity",
                    "id": row["entity_id"],
                    "name": row["name"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "status": row["status"],
                })

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
            for row in rows:
                results.append({
                    "type": "metric",
                    "id": row["metric_id"],
                    "name": row["name"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "definition_sql": row["definition_sql"],
                    "status": row["status"],
                })

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
            for row in rows:
                results.append({
                    "type": "asset",
                    "id": row["object_id"],
                    "name": row["native_name"],
                    "display_name": row["native_name"],
                    "fqn": row["fqn"],
                    "source_id": row["source_id"],
                    "synced_at": row["synced_at"],
                })

        return results

    # ── Resolve ──────────────────────────────────────────────────

    def resolve(self, name: str) -> dict[str, Any]:
        # Try metrics first
        metric_row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE name = ?", [name]
        )
        if metric_row:
            mappings = self.metadata.query_rows(
                "SELECT * FROM semantic_mappings WHERE semantic_type = 'metric' AND semantic_id = ?",
                [metric_row["metric_id"]],
            )
            physical_assets = self._resolve_mappings(mappings)
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
                "physical_assets": physical_assets,
                "mappings": [self._mapping_row_to_dict(m) for m in mappings],
            }

        # Try entities
        entity_row = self.metadata.query_one(
            "SELECT * FROM semantic_entities WHERE name = ?", [name]
        )
        if entity_row:
            mappings = self.metadata.query_rows(
                "SELECT * FROM semantic_mappings WHERE semantic_type = 'entity' AND semantic_id = ?",
                [entity_row["entity_id"]],
            )
            physical_assets = self._resolve_mappings(mappings)
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
                "physical_assets": physical_assets,
                "mappings": [self._mapping_row_to_dict(m) for m in mappings],
            }

        raise KeyError(f"Could not resolve term: {name}")

    # ── Planner context ──────────────────────────────────────────

    def planner_context(self, session_id: str, service: SemanticLayerService) -> dict[str, Any]:
        metrics = self.metadata.query_rows(
            "SELECT * FROM semantic_metrics WHERE status = 'published' ORDER BY name"
        )
        entities = self.metadata.query_rows(
            "SELECT * FROM semantic_entities WHERE status = 'published' ORDER BY name"
        )

        metric_list = []
        for m in metrics:
            metric_list.append({
                "metric_id": m["metric_id"],
                "name": m["name"],
                "display_name": m["display_name"],
                "definition_sql": m["definition_sql"],
                "dimensions": json.loads(m["dimensions_json"]),
            })

        entity_list = []
        for e in entities:
            entity_list.append({
                "entity_id": e["entity_id"],
                "name": e["name"],
                "display_name": e["display_name"],
                "keys": json.loads(e["keys_json"]),
            })

        # Available step types
        step_types = [
            "compare_watch_time",
            "analyze_qoe",
            "analyze_ads",
            "analyze_recommendation",
            "synthesize_findings",
            "compare_metric",
            "profile_table",
            "sample_rows",
        ]

        return {
            "session_id": session_id,
            "metrics": metric_list,
            "entities": entity_list,
            "available_step_types": step_types,
            "policies": [
                "Results are aggregate-only.",
                "Evidence graph keeps support and contradiction links for every claim.",
            ],
        }

    # ── Graph traversal ──────────────────────────────────────────

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

        # Try to identify the node type
        node = self._identify_node(node_id)
        if node:
            nodes[node_id] = node

        if remaining_depth == 0:
            return

        # Entity -> metric edges (metrics that belong to this entity)
        metric_rows = self.metadata.query_rows(
            "SELECT metric_id, name FROM semantic_metrics WHERE entity_id = ?", [node_id]
        )
        for m in metric_rows:
            edges.append({"from": node_id, "to": m["metric_id"], "edge_type": "defines"})
            self._traverse(m["metric_id"], remaining_depth - 1, nodes, edges, visited)

        # Semantic mapping edges
        mapping_rows = self.metadata.query_rows(
            "SELECT * FROM semantic_mappings WHERE semantic_id = ?", [node_id]
        )
        for mp in mapping_rows:
            edges.append({"from": node_id, "to": mp["object_id"], "edge_type": "maps_to"})
            self._traverse(mp["object_id"], remaining_depth - 1, nodes, edges, visited)

        # Source object hierarchy: children
        child_rows = self.metadata.query_rows(
            "SELECT object_id, native_name, object_type FROM source_objects WHERE parent_id = ?", [node_id]
        )
        for c in child_rows:
            edges.append({"from": node_id, "to": c["object_id"], "edge_type": "contains"})
            self._traverse(c["object_id"], remaining_depth - 1, nodes, edges, visited)

        # Evidence edges (for claims/observations)
        evidence_rows = self.metadata.query_rows(
            """
            SELECT edge_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type, weight
            FROM evidence_edges
            WHERE from_node_id = ? OR to_node_id = ?
            """,
            [node_id, node_id],
        )
        for ev in evidence_rows:
            other_id = ev["to_node_id"] if ev["from_node_id"] == node_id else ev["from_node_id"]
            edges.append({
                "from": ev["from_node_id"],
                "to": ev["to_node_id"],
                "edge_type": ev["edge_type"],
                "weight": ev["weight"],
            })
            self._traverse(other_id, remaining_depth - 1, nodes, edges, visited)

    def _identify_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM semantic_entities WHERE entity_id = ?", [node_id])
        if row:
            return {"id": node_id, "type": "entity", "name": row["name"], "display_name": row["display_name"]}

        row = self.metadata.query_one("SELECT * FROM semantic_metrics WHERE metric_id = ?", [node_id])
        if row:
            return {"id": node_id, "type": "metric", "name": row["name"], "display_name": row["display_name"]}

        row = self.metadata.query_one("SELECT * FROM source_objects WHERE object_id = ?", [node_id])
        if row:
            return {"id": node_id, "type": row["object_type"], "name": row["native_name"], "fqn": row["fqn"]}

        return None

    def _resolve_mappings(self, mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assets = []
        for m in mappings:
            obj = self.metadata.query_one(
                "SELECT * FROM source_objects WHERE object_id = ?", [m["object_id"]]
            )
            if obj:
                asset: dict[str, Any] = {
                    "object_id": obj["object_id"],
                    "native_name": obj["native_name"],
                    "fqn": obj["fqn"],
                    "object_type": obj["object_type"],
                    "source_id": obj["source_id"],
                    "synced_at": obj["synced_at"],
                    "properties": json.loads(obj["properties_json"]),
                }
                # Include engine info if binding service is available
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

    def _mapping_row_to_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "mapping_id": row["mapping_id"],
            "semantic_type": row["semantic_type"],
            "semantic_id": row["semantic_id"],
            "object_id": row["object_id"],
            "mapping_type": row["mapping_type"],
            "mapping_json": json.loads(row["mapping_json"]),
        }
