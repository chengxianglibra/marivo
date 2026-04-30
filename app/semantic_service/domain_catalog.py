from __future__ import annotations

import json
from typing import Any

from app.api.models.domain import DomainCatalogCreateRequest, DomainCatalogUpdateRequest

from .common import SemanticServiceSupport, now_iso

_SEMANTIC_DOMAIN_OBJECT_TABLES: tuple[dict[str, str], ...] = (
    {
        "object_type": "entity",
        "table": "semantic_entity_contracts",
        "id_column": "entity_contract_id",
        "ref_column": "entity_ref",
        "object_kind": "entity",
        "detail_prefix": "/semantic/entities",
    },
    {
        "object_type": "metric",
        "table": "semantic_metric_contracts",
        "id_column": "metric_contract_id",
        "ref_column": "metric_ref",
        "object_kind": "metric",
        "detail_prefix": "/semantic/metrics",
    },
    {
        "object_type": "process",
        "table": "semantic_process_objects",
        "id_column": "process_contract_id",
        "ref_column": "process_ref",
        "object_kind": "process",
        "detail_prefix": "/semantic/process-objects",
    },
    {
        "object_type": "dimension",
        "table": "semantic_dimension_contracts",
        "id_column": "dimension_contract_id",
        "ref_column": "dimension_ref",
        "object_kind": "dimension",
        "detail_prefix": "/semantic/dimensions",
    },
    {
        "object_type": "time",
        "table": "semantic_time_objects",
        "id_column": "time_contract_id",
        "ref_column": "time_ref",
        "object_kind": "time",
        "detail_prefix": "/semantic/time",
    },
    {
        "object_type": "predicate",
        "table": "semantic_predicate_contracts",
        "id_column": "predicate_contract_id",
        "ref_column": "predicate_ref",
        "object_kind": "predicate",
        "detail_prefix": "/semantic/predicates",
    },
    {
        "object_type": "compatibility_profile",
        "table": "compiler_compatibility_profiles",
        "id_column": "profile_id",
        "ref_column": "profile_ref",
        "display_column": "profile_ref",
        "description_column": "''",
        "object_kind": "compiler_profile",
        "detail_prefix": "/compiler/compatibility-profiles",
    },
    {
        "object_type": "relationship",
        "table": "semantic_entity_relationships",
        "id_column": "relationship_id",
        "ref_column": "relationship_ref",
        "object_kind": "relationship",
        "detail_prefix": "/semantic/relationships",
    },
)


class DomainCatalogService(SemanticServiceSupport):
    """Discovery-only domain catalog registry.

    Domains group semantic catalog objects for browsing and search. They are not
    compiler compatibility truth or a permission source; authorization remains
    in governance policy, data access grants, and execution-engine ACLs.
    """

    def create_domain(self, payload: DomainCatalogCreateRequest) -> dict[str, Any]:
        existing = self.metadata.query_one(
            "SELECT domain_ref FROM semantic_domain_catalog WHERE domain_ref = ?",
            [payload.domain_ref],
        )
        if existing is not None:
            raise self._conflict_error(f"Domain already exists: {payload.domain_ref}")
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_domain_catalog (
                domain_ref, display_name, description, status, aliases_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?)
            """,
            [
                payload.domain_ref,
                payload.display_name,
                payload.description,
                json.dumps(payload.aliases),
                created_at,
                created_at,
            ],
        )
        return self.read_domain(payload.domain_ref)

    def read_domain(self, domain_ref: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_domain_catalog WHERE domain_ref = ?",
            [domain_ref],
        )
        if row is None:
            raise self._not_found(f"Unknown semantic domain: {domain_ref}")
        return self._row_to_domain(row)

    def list_domains(self, *, status: str | None = None, q: str | None = None) -> dict[str, Any]:
        if status is not None and status not in {"active", "deprecated"}:
            raise self._validation_error("Domain status must be 'active' or 'deprecated'")
        rows = self.metadata.query_rows(
            "SELECT * FROM semantic_domain_catalog ORDER BY domain_ref"
            if status is None
            else "SELECT * FROM semantic_domain_catalog WHERE status = ? ORDER BY domain_ref",
            None if status is None else [status],
        )
        query = q.strip().lower() if q else None
        items = [self._row_to_domain(row) for row in rows]
        if query:
            items = [item for item in items if self._domain_matches_text(item, query)]
        return {"items": items, "total": len(items)}

    def update_domain(self, domain_ref: str, payload: DomainCatalogUpdateRequest) -> dict[str, Any]:
        self.read_domain(domain_ref)
        updates: list[str] = []
        params: list[Any] = []
        if payload.display_name is not None:
            updates.append("display_name = ?")
            params.append(payload.display_name)
        if payload.description is not None:
            updates.append("description = ?")
            params.append(payload.description)
        if payload.aliases is not None:
            updates.append("aliases_json = ?")
            params.append(json.dumps(payload.aliases))
        if not updates:
            return self.read_domain(domain_ref)
        updates.append("updated_at = ?")
        params.extend([now_iso(), domain_ref])
        self.metadata.execute(
            f"UPDATE semantic_domain_catalog SET {', '.join(updates)} WHERE domain_ref = ?",
            params,
        )
        return self.read_domain(domain_ref)

    def deprecate_domain(self, domain_ref: str) -> dict[str, Any]:
        self.read_domain(domain_ref)
        self.metadata.execute(
            """
            UPDATE semantic_domain_catalog
            SET status = 'deprecated', updated_at = ?
            WHERE domain_ref = ?
            """,
            [now_iso(), domain_ref],
        )
        return self.read_domain(domain_ref)

    def search_semantic_objects_by_domain(
        self,
        *,
        domain_ref: str | None = None,
        object_type: str | None = None,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        q: str | None = None,
        related_domain_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        if readiness_status is not None and readiness_status not in {"not_ready", "ready", "stale"}:
            raise self._validation_error(
                "Semantic object readiness_status must be 'not_ready', 'ready', or 'stale'"
            )
        normalized_related_domain_refs = [
            ref.strip() for ref in (related_domain_refs or []) if ref.strip()
        ]
        for ref in normalized_related_domain_refs:
            if not ref.startswith("domain."):
                raise self._validation_error(
                    "related_domain_refs must contain domain.* refs",
                    field_path="related_domain_refs",
                )
        object_specs = [
            spec
            for spec in _SEMANTIC_DOMAIN_OBJECT_TABLES
            if object_type is None or spec["object_type"] == object_type
        ]
        if object_type is not None and not object_specs:
            raise self._validation_error(f"Unsupported semantic object_type: {object_type}")
        query = q.strip().lower() if q else None
        items: list[dict[str, Any]] = []
        list_context = self._list_context()
        for spec in object_specs:
            display_column = spec.get("display_column", "display_name")
            description_column = spec.get("description_column", "description")
            rows = self.metadata.query_rows(
                f"""
                SELECT {spec["id_column"]} AS object_id,
                       {spec["ref_column"]} AS ref,
                       {display_column} AS display_name,
                       {description_column} AS description,
                       status,
                       catalog_metadata_json
                FROM {spec["table"]}
                {"" if status is None else "WHERE status = ?"}
                ORDER BY {spec["ref_column"]}
                """,
                None if status is None else [status],
            )
            for row in rows:
                metadata = self._catalog_metadata_from_row(row)
                metadata_domain_ref = str(metadata.get("domain_ref") or "")
                metadata_related_refs = set(metadata.get("related_domain_refs") or [])
                related_filter_refs = set(normalized_related_domain_refs)
                if (domain_ref is not None and metadata_domain_ref != domain_ref) or (
                    related_filter_refs and not related_filter_refs.issubset(metadata_related_refs)
                ):
                    continue
                snapshot = list_context.load_dependency_snapshot(str(row["ref"]))
                if snapshot is None:
                    continue
                readiness = list_context.readiness_for(snapshot)
                if readiness_status is not None and (
                    readiness.get("readiness_status") != readiness_status
                ):
                    continue
                item = {
                    "object_type": spec["object_type"],
                    "object_id": row["object_id"],
                    "ref": row["ref"],
                    "display_name": row["display_name"] or row["ref"],
                    "description": row["description"] or "",
                    "status": row["status"],
                    "lifecycle_status": readiness["lifecycle_status"],
                    "readiness_status": readiness["readiness_status"],
                    "blocker_count": len(readiness.get("blocking_requirements") or []),
                    "catalog_metadata": metadata,
                    "detail_path": f"{spec['detail_prefix']}/{row['object_id']}",
                }
                if query and not self._semantic_object_matches_text(item, query):
                    continue
                items.append(item)
        return {"items": items, "total": len(items)}

    @staticmethod
    def _row_to_domain(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain_ref": row["domain_ref"],
            "display_name": row["display_name"],
            "description": row["description"] or "",
            "status": row["status"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _domain_matches_text(item: dict[str, Any], query: str) -> bool:
        haystack = " ".join(
            [
                str(item.get("domain_ref") or ""),
                str(item.get("display_name") or ""),
                str(item.get("description") or ""),
                " ".join(str(alias) for alias in item.get("aliases") or []),
            ]
        ).lower()
        return query in haystack

    @staticmethod
    def _catalog_metadata_from_row(row: dict[str, Any]) -> dict[str, Any]:
        raw = json.loads(row["catalog_metadata_json"] or "{}")
        return {
            "domain_ref": raw.get("domain_ref"),
            "related_domain_refs": list(raw.get("related_domain_refs") or []),
            "aliases": list(raw.get("aliases") or []),
        }

    @staticmethod
    def _semantic_object_matches_text(item: dict[str, Any], query: str) -> bool:
        metadata = item.get("catalog_metadata") or {}
        haystack = " ".join(
            [
                str(item.get("ref") or ""),
                str(item.get("display_name") or ""),
                str(item.get("description") or ""),
                " ".join(str(alias) for alias in metadata.get("aliases") or []),
            ]
        ).lower()
        return query in haystack
