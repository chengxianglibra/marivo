from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.analysis_core import SUPPORTED_STEP_TYPES
from app.semantic_runtime.errors import (
    SemanticRuntimeError,
)
from app.semantic_runtime.repository import SemanticRuntimeRepository
from app.semantic_runtime.semantic_metadata import runtime_ref_kind
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.bindings import BindingService


_SEARCH_CONFIG: dict[str, dict[str, str]] = {
    "entity": {
        "table": "semantic_entity_contracts",
        "id_column": "entity_contract_id",
        "ref_column": "entity_ref",
        "version_column": "entity_contract_version",
    },
    "metric": {
        "table": "semantic_metric_contracts",
        "id_column": "metric_contract_id",
        "ref_column": "metric_ref",
        "version_column": "metric_contract_version",
    },
    "process": {
        "table": "semantic_process_objects",
        "id_column": "process_contract_id",
        "ref_column": "process_ref",
        "version_column": "process_contract_version",
    },
    "dimension": {
        "table": "semantic_dimension_contracts",
        "id_column": "dimension_contract_id",
        "ref_column": "dimension_ref",
        "version_column": "dimension_contract_version",
    },
    "time": {
        "table": "semantic_time_objects",
        "id_column": "time_contract_id",
        "ref_column": "time_ref",
        "version_column": "time_contract_version",
    },
    "binding": {
        "table": "typed_bindings",
        "id_column": "binding_id",
        "ref_column": "binding_ref",
        "version_column": "binding_contract_version",
    },
}
_SEARCHABLE_OBJECT_TYPES = frozenset({*_SEARCH_CONFIG.keys(), "asset"})
_ALIASABLE_OBJECT_KINDS = ("metric", "entity")


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
        normalized_type = self._normalize_object_type_filter(object_type)
        results: list[dict[str, Any]] = []
        pattern = f"%{query}%"

        for object_kind, config in _SEARCH_CONFIG.items():
            if normalized_type is not None and normalized_type != object_kind:
                continue
            rows = self.metadata.query_rows(
                f"""
                SELECT
                    {config["id_column"]} AS object_id,
                    {config["ref_column"]} AS ref,
                    display_name,
                    description,
                    {config["version_column"]} AS contract_version,
                    status,
                    revision,
                    created_at,
                    updated_at
                FROM {config["table"]}
                WHERE status = 'published'
                  AND (
                    {config["ref_column"]} LIKE ?
                    OR COALESCE(display_name, '') LIKE ?
                    OR COALESCE(description, '') LIKE ?
                  )
                ORDER BY {config["ref_column"]}
                """,
                [pattern, pattern, pattern],
            )
            results.extend(self._semantic_search_row_to_summary(object_kind, row) for row in rows)

        if normalized_type is None or normalized_type == "asset":
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
            results.extend(self._asset_search_row_to_summary(row) for row in rows)

        return results

    def resolve(self, name: str) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise KeyError("Could not resolve empty semantic term")

        if runtime_ref_kind(normalized_name) is not None:
            resolved = self.semantic_repository.resolve_ref(normalized_name)
            return self._resolved_object_to_detail(resolved)

        for object_kind in _ALIASABLE_OBJECT_KINDS:
            try:
                resolved = self.semantic_repository.resolve_ref(f"{object_kind}.{normalized_name}")
                return self._resolved_object_to_detail(resolved)
            except SemanticRuntimeError:
                continue

        raise KeyError(
            "Could not resolve term: "
            f"{normalized_name}. Bare-name aliases are supported only for metric/entity; "
            "use a typed ref for other object kinds."
        )

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

    def _normalize_object_type_filter(self, object_type: str | None) -> str | None:
        if object_type is None:
            return None
        normalized = object_type.strip()
        if normalized not in _SEARCHABLE_OBJECT_TYPES:
            raise ValueError(
                "Unsupported catalog object type filter: "
                f"{object_type}. Expected one of {sorted(_SEARCHABLE_OBJECT_TYPES)}."
            )
        return normalized

    def _semantic_search_row_to_summary(
        self, object_kind: str, row: dict[str, Any]
    ) -> dict[str, Any]:
        ref = str(row["ref"])
        return {
            "object_kind": object_kind,
            "object_id": str(row["object_id"]),
            "ref": ref,
            "name": ref.split(".", 1)[1] if "." in ref else ref,
            "display_name": row["display_name"],
            "description": row["description"],
            "contract_version": row["contract_version"],
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _asset_search_row_to_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "object_kind": "asset",
            "object_id": str(row["object_id"]),
            "ref": str(row["fqn"]),
            "name": row["native_name"],
            "display_name": row["native_name"],
            "description": None,
            "status": "synced",
            "object_type": row["object_type"],
            "source_id": row["source_id"],
            "synced_at": row["synced_at"],
        }

    def _resolved_object_to_detail(self, resolved: Any) -> dict[str, Any]:
        return {
            "object_kind": resolved.object_kind,
            "object_id": resolved.object_id,
            "ref": resolved.ref,
            "semantic_object": resolved.semantic_object,
            "status": resolved.status,
            "revision": resolved.revision,
            "created_at": resolved.created_at,
            "updated_at": resolved.updated_at,
        }

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
