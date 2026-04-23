from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.registry.common import now_iso
from app.registry.engine_registry import EngineRegistry
from app.registry.source_registry import SourceRegistry
from app.storage.metadata import MetadataStore

_SUPPORTED_STATUSES = {"active", "inactive", "deprecated"}


@dataclass(slots=True)
class MappingValidationResult:
    is_valid: bool
    readiness_status: str
    failure_code: str | None = None

    def to_dict(self, *, mapping_id: str) -> dict[str, Any]:
        return {
            "mapping_id": mapping_id,
            "is_valid": self.is_valid,
            "readiness_status": self.readiness_status,
            "failure_code": self.failure_code,
        }


class MappingRegistry:
    """Registry for source-to-execution mapping contracts."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.source_registry = SourceRegistry(metadata)
        self.engine_registry = EngineRegistry(metadata)

    def create_mapping(
        self,
        source_id: str,
        engine_id: str,
        *,
        priority: int = 0,
        catalog_mappings: list[dict[str, Any]] | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        self.source_registry.get_source(source_id)
        self.engine_registry.get_engine(engine_id)
        normalized_status = self._normalize_status(status)
        normalized_catalog_mappings = self._normalize_catalog_mappings(catalog_mappings or [])
        mapping_id = f"map_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO source_execution_mappings (
                mapping_id, source_id, engine_id, priority, catalog_mappings_json,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                mapping_id,
                source_id,
                engine_id,
                priority,
                json.dumps(normalized_catalog_mappings),
                normalized_status,
                now,
                now,
            ],
        )
        return self.get_mapping(mapping_id)

    def get_mapping(self, mapping_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_execution_mappings WHERE mapping_id = ?",
            [mapping_id],
        )
        if row is None:
            raise KeyError(f"Unknown mapping: {mapping_id}")
        return self._row_to_mapping(row)

    def list_mappings(
        self,
        *,
        source_id: str | None = None,
        engine_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM source_execution_mappings WHERE 1=1"
        params: list[Any] = []
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if engine_id is not None:
            sql += " AND engine_id = ?"
            params.append(engine_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(self._normalize_status(status))
        sql += " ORDER BY priority DESC, created_at"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_mapping(row) for row in rows]

    def update_mapping(
        self,
        mapping_id: str,
        *,
        priority: int | None = None,
        catalog_mappings: list[dict[str, Any]] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_mapping(mapping_id)
        next_priority = current["priority"] if priority is None else priority
        next_catalog_mappings = (
            current["catalog_mappings"]
            if catalog_mappings is None
            else self._normalize_catalog_mappings(catalog_mappings)
        )
        next_status = current["status"] if status is None else self._normalize_status(status)
        self.metadata.execute(
            """
            UPDATE source_execution_mappings
            SET priority = ?, catalog_mappings_json = ?, status = ?, updated_at = ?
            WHERE mapping_id = ?
            """,
            [
                next_priority,
                json.dumps(next_catalog_mappings),
                next_status,
                now_iso(),
                mapping_id,
            ],
        )
        return self.get_mapping(mapping_id)

    def delete_mapping(self, mapping_id: str) -> None:
        self.get_mapping(mapping_id)
        self.metadata.execute(
            "DELETE FROM source_execution_mappings WHERE mapping_id = ?",
            [mapping_id],
        )

    def validate_mapping(self, mapping_id: str) -> dict[str, Any]:
        mapping = self.get_mapping(mapping_id)
        return self.evaluate_mapping(mapping).to_dict(mapping_id=mapping_id)

    def get_mapping_readiness(self, mapping_id: str) -> dict[str, Any]:
        validation = self.validate_mapping(mapping_id)
        return {
            "mapping_id": mapping_id,
            "readiness_status": validation["readiness_status"],
            "failure_code": validation["failure_code"],
        }

    def ensure_mapping(
        self,
        source_id: str,
        engine_id: str,
        *,
        priority: int = 0,
        catalog_mappings: list[dict[str, Any]] | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        existing = self.metadata.query_one(
            """
            SELECT mapping_id
            FROM source_execution_mappings
            WHERE source_id = ? AND engine_id = ?
            """,
            [source_id, engine_id],
        )
        if existing is None:
            return self.create_mapping(
                source_id,
                engine_id,
                priority=priority,
                catalog_mappings=catalog_mappings,
                status=status,
            )
        return self.update_mapping(
            str(existing["mapping_id"]),
            priority=priority,
            catalog_mappings=catalog_mappings,
            status=status,
        )

    def evaluate_mapping(self, mapping: dict[str, Any]) -> MappingValidationResult:
        if mapping["status"] != "active":
            return MappingValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="mapping_inactive",
            )
        source = self.source_registry.get_source(str(mapping["source_id"]))
        engine = self.engine_registry.get_engine(str(mapping["engine_id"]))
        if source["status"] != "active" or engine["status"] != "active":
            return MappingValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="mapping_inactive_dependency",
            )

        mapped_catalogs = {
            str(item["authority_catalog"])
            for item in mapping.get("catalog_mappings", [])
            if item.get("authority_catalog")
        }
        if not mapped_catalogs:
            return MappingValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="mapping_incomplete",
            )

        source_catalogs = self._current_source_authority_catalogs(str(mapping["source_id"]))
        if source_catalogs and not source_catalogs.issubset(mapped_catalogs):
            return MappingValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="mapping_incomplete",
            )

        for item in mapping["catalog_mappings"]:
            execution_catalog = item.get("execution_catalog")
            default_schema = item.get("default_schema")
            if not isinstance(execution_catalog, str) or not execution_catalog.strip():
                return MappingValidationResult(
                    is_valid=False,
                    readiness_status="not_ready",
                    failure_code="mapping_invalid_namespace",
                )
            if default_schema is not None and (
                not isinstance(default_schema, str) or not default_schema.strip()
            ):
                return MappingValidationResult(
                    is_valid=False,
                    readiness_status="not_ready",
                    failure_code="mapping_invalid_namespace",
                )

        return MappingValidationResult(
            is_valid=True,
            readiness_status="ready",
        )

    def _row_to_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "mapping_id": row["mapping_id"],
            "source_id": row["source_id"],
            "engine_id": row["engine_id"],
            "priority": row["priority"],
            "catalog_mappings": json.loads(str(row["catalog_mappings_json"])),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        validation = self.evaluate_mapping(mapping)
        mapping["readiness_status"] = validation.readiness_status
        mapping["failure_code"] = validation.failure_code
        return mapping

    def _normalize_status(self, status: str) -> str:
        normalized = status.strip()
        if normalized not in _SUPPORTED_STATUSES:
            supported = ", ".join(sorted(_SUPPORTED_STATUSES))
            raise ValueError(f"mapping status must be one of: {supported}")
        return normalized

    def _normalize_catalog_mappings(
        self,
        catalog_mappings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_authority_catalogs: set[str] = set()
        for item in catalog_mappings:
            authority_catalog = str(item.get("authority_catalog", "")).strip()
            execution_catalog = str(item.get("execution_catalog", "")).strip()
            default_schema_raw = item.get("default_schema")
            default_schema = None
            if default_schema_raw is not None:
                default_schema = str(default_schema_raw).strip()
            if not authority_catalog:
                raise ValueError("catalog_mappings[].authority_catalog is required")
            if not execution_catalog:
                raise ValueError("catalog_mappings[].execution_catalog is required")
            if default_schema_raw is not None and not default_schema:
                raise ValueError("catalog_mappings[].default_schema must not be blank")
            if authority_catalog in seen_authority_catalogs:
                raise ValueError(
                    f"catalog_mappings contains duplicate authority_catalog: {authority_catalog}"
                )
            seen_authority_catalogs.add(authority_catalog)
            normalized.append(
                {
                    "authority_catalog": authority_catalog,
                    "execution_catalog": execution_catalog,
                    "default_schema": default_schema,
                }
            )
        return normalized

    def _current_source_authority_catalogs(self, source_id: str) -> set[str]:
        rows = self.metadata.query_rows(
            """
            SELECT authority_locator_json
            FROM source_objects
            WHERE source_id = ? AND object_type = 'table'
            """,
            [source_id],
        )
        catalogs: set[str] = set()
        for row in rows:
            locator = json.loads(str(row["authority_locator_json"]))
            catalog = locator.get("catalog")
            if isinstance(catalog, str) and catalog:
                catalogs.add(catalog)
        return catalogs
