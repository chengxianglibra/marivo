from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.analysis_core import SUPPORTED_STEP_TYPES
from app.semantic_runtime.errors import (
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.repository import SemanticRuntimeRepository
from app.semantic_runtime.resolution import RuntimeSemanticAvailability
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

    def search(
        self,
        query: str,
        object_type: str | None = None,
        readiness: str = "ready",
    ) -> list[dict[str, Any]]:
        normalized_type = self._normalize_object_type_filter(object_type)
        normalized_readiness = self._normalize_readiness_filter(readiness)
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
            for row in rows:
                availability = self.semantic_repository.inspect_ref(str(row["ref"]))
                if not self._matches_readiness_filter(
                    lifecycle_status=availability.lifecycle_status,
                    readiness_status=availability.readiness_status,
                    readiness_filter=normalized_readiness,
                ):
                    continue
                results.append(self._semantic_search_row_to_summary(object_kind, row, availability))

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

    def get_catalog_object_detail(self, object_kind: str, object_id: str) -> dict[str, Any]:
        normalized_kind = self._normalize_object_type_filter(object_kind)
        if normalized_kind is None:
            raise KeyError(f"Unsupported catalog object kind: {object_kind}")
        if normalized_kind == "asset":
            return self._asset_object_detail(object_id)

        config = _SEARCH_CONFIG[normalized_kind]
        row = self.metadata.query_one(
            f"SELECT {config['ref_column']} AS ref FROM {config['table']} WHERE {config['id_column']} = ?",
            [object_id],
        )
        if row is None:
            raise KeyError(f"Catalog object {object_id!r} not found for kind {normalized_kind!r}")
        availability = self.semantic_repository.inspect_ref(str(row["ref"]))
        detail = self._availability_to_detail(availability)
        detail["detail_path"] = self._catalog_detail_path(normalized_kind, object_id)
        return detail

    def resolve(self, name: str) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise KeyError("Could not resolve empty semantic term")

        if runtime_ref_kind(normalized_name) is None:
            raise KeyError(
                "Could not resolve term: "
                f"{normalized_name}. Semantic resolve requires an explicit typed ref."
            )

        availability = self.semantic_repository.inspect_ref(normalized_name)
        if availability.lifecycle_status != "active":
            raise SemanticRuntimeUnpublishedError(
                f"Semantic ref is not active: {normalized_name}",
                semantic_ref=normalized_name,
            )
        if availability.readiness_status != "ready":
            raise SemanticRuntimeNotReadyError(
                f"Semantic ref is not ready: {normalized_name}",
                semantic_ref=normalized_name,
                object_kind=availability.resolved.object_kind,
                lifecycle_status=availability.lifecycle_status,
                readiness_status=availability.readiness_status,
                blocking_requirements=availability.blocking_requirements,
                capabilities=availability.capabilities,
                dependency_refs=availability.dependency_refs,
            )
        return self._availability_to_detail(availability)

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

        typed_entity = self.metadata.query_one(
            "SELECT entity_ref FROM semantic_entity_contracts WHERE entity_contract_id = ?",
            [node_id],
        )
        if typed_entity is not None:
            metric_rows = self.metadata.query_rows(
                """
                SELECT metric_contract_id
                FROM semantic_metric_contracts
                WHERE observed_entity_ref = ? AND status = 'published'
                """,
                [typed_entity["entity_ref"]],
            )
            for metric in metric_rows:
                edges.append(
                    {"from": node_id, "to": metric["metric_contract_id"], "edge_type": "defines"}
                )
                self._traverse(
                    metric["metric_contract_id"], remaining_depth - 1, nodes, edges, visited
                )

        typed_metric = self.metadata.query_one(
            "SELECT metric_ref FROM semantic_metric_contracts WHERE metric_contract_id = ?",
            [node_id],
        )
        if typed_metric is not None:
            binding_rows = self.metadata.query_rows(
                """
                SELECT DISTINCT cb.source_object_ref
                FROM typed_bindings b
                JOIN carrier_bindings cb ON cb.binding_id = b.binding_id
                WHERE b.bound_object_ref = ? AND b.status = 'published'
                """,
                [typed_metric["metric_ref"]],
            )
            for binding in binding_rows:
                source_object_ref = binding["source_object_ref"]
                if source_object_ref is None:
                    continue
                edges.append({"from": node_id, "to": source_object_ref, "edge_type": "maps_to"})
                self._traverse(source_object_ref, remaining_depth - 1, nodes, edges, visited)

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

    def _normalize_readiness_filter(self, readiness: str | None) -> str:
        normalized = (readiness or "ready").strip().lower()
        if normalized not in {"ready", "not_ready", "stale", "all"}:
            raise ValueError(
                "Unsupported catalog readiness filter: "
                f"{readiness}. Expected one of ['all', 'not_ready', 'ready', 'stale']."
            )
        return normalized

    def _matches_readiness_filter(
        self, *, lifecycle_status: str, readiness_status: str, readiness_filter: str
    ) -> bool:
        if lifecycle_status != "active":
            return False
        if readiness_filter == "all":
            return True
        return readiness_status == readiness_filter

    def _semantic_search_row_to_summary(
        self, object_kind: str, row: dict[str, Any], availability: RuntimeSemanticAvailability
    ) -> dict[str, Any]:
        ref = str(row["ref"])
        object_id = str(row["object_id"])
        return {
            "object_kind": object_kind,
            "object_id": object_id,
            "ref": ref,
            "name": ref.split(".", 1)[1] if "." in ref else ref,
            "display_name": row["display_name"],
            "description": row["description"],
            "contract_version": row["contract_version"],
            "status": row["status"],
            "lifecycle_status": availability.lifecycle_status,
            "readiness_status": availability.readiness_status,
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "detail_path": self._catalog_detail_path(object_kind, object_id),
            "resolve_path": f"/semantic/resolve/{ref}",
        }

    def _asset_search_row_to_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        object_id = str(row["object_id"])
        source_id = str(row["source_id"])
        return {
            "object_kind": "asset",
            "object_id": object_id,
            "ref": str(row["fqn"]),
            "name": row["native_name"],
            "display_name": row["native_name"],
            "description": None,
            "status": "synced",
            "object_type": row["object_type"],
            "source_id": source_id,
            "synced_at": row["synced_at"],
            "detail_path": self._catalog_detail_path("asset", object_id),
            "source_object_path": f"/sources/{source_id}/objects/{object_id}",
        }

    def _availability_to_detail(self, availability: RuntimeSemanticAvailability) -> dict[str, Any]:
        resolved = availability.resolved
        semantic_object = dict(resolved.semantic_object)
        semantic_object.update(
            {
                "lifecycle_status": availability.lifecycle_status,
                "readiness_status": availability.readiness_status,
                "blocking_requirements": list(availability.blocking_requirements),
                "capabilities": dict(availability.capabilities),
                "dependency_refs": list(availability.dependency_refs),
                "dependent_refs": [],
            }
        )
        return {
            "object_kind": resolved.object_kind,
            "object_id": resolved.object_id,
            "ref": resolved.ref,
            "semantic_object": semantic_object,
            "status": resolved.status,
            "revision": resolved.revision,
            "created_at": resolved.created_at,
            "updated_at": resolved.updated_at,
        }

    def _asset_object_detail(self, object_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ?", [object_id]
        )
        if row is None:
            raise KeyError(f"Catalog asset {object_id!r} not found")
        source_object = self._source_object_row_to_detail(row)
        return {
            "object_kind": "asset",
            "object_id": source_object["object_id"],
            "ref": source_object["fqn"],
            "source_object": source_object,
            "detail_path": self._catalog_detail_path("asset", object_id),
        }

    def _source_object_row_to_detail(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = row["properties_json"]
        return {
            "object_id": str(row["object_id"]),
            "source_id": str(row["source_id"]),
            "object_type": str(row["object_type"]),
            "parent_id": str(row["parent_id"]) if row["parent_id"] is not None else None,
            "native_name": str(row["native_name"]),
            "native_id": str(row["native_id"]) if row["native_id"] is not None else None,
            "fqn": str(row["fqn"]),
            "properties": {} if properties in (None, "") else json.loads(properties),
            "sync_version": str(row["sync_version"]) if row["sync_version"] is not None else None,
            "synced_at": str(row["synced_at"]) if row["synced_at"] is not None else None,
        }

    def _catalog_detail_path(self, object_kind: str, object_id: str) -> str:
        return f"/catalog/objects/{object_kind}/{object_id}"

    def _identify_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?", [node_id]
        )
        if row is not None:
            return {
                "id": node_id,
                "type": "entity",
                "name": str(row["entity_ref"]).removeprefix("entity."),
                "display_name": row["display_name"],
            }

        row = self.metadata.query_one(
            "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?", [node_id]
        )
        if row is not None:
            return {
                "id": node_id,
                "type": "metric",
                "name": str(row["metric_ref"]).removeprefix("metric."),
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
