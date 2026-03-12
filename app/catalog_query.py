from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.semantic_runtime import CatalogRuntimeService
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.bindings import BindingService


class CatalogQueryService:
    """Search, resolve, planner-context, and graph traversal over the
    semantic and physical catalog."""

    def __init__(
        self,
        metadata: MetadataStore,
        binding_service: BindingService | None = None,
    ) -> None:
        self.metadata = metadata
        self.runtime = CatalogRuntimeService(metadata, binding_service)

    # ── Search ───────────────────────────────────────────────────

    def search(self, query: str, object_type: str | None = None) -> list[dict[str, Any]]:
        return self.runtime.search(query, object_type=object_type)

    # ── Resolve ──────────────────────────────────────────────────

    def resolve(self, name: str) -> dict[str, Any]:
        return self.runtime.resolve(name)

    # ── Planner context ──────────────────────────────────────────

    def planner_context(self, session_id: str, service: object) -> dict[str, Any]:
        del service
        return self.runtime.planner_context(session_id)

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
