from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.adapters.base import MAX_PREVIEW_ROWS, CatalogAdapter, PreviewFilters
from app.registry.common import now_iso
from app.registry.factories import build_catalog_adapter, validate_source_type
from app.storage.metadata import MetadataStore


class DependencyError(Exception):
    """Raised when a delete is blocked by existing dependencies."""

    def __init__(self, message: str, dependencies: list[str] | None = None) -> None:
        super().__init__(message)
        self.dependencies = dependencies or []


@dataclass(slots=True)
class SourceValidationResult:
    is_valid: bool
    readiness_status: str
    failure_code: str | None = None

    def to_dict(self, *, source_id: str) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "is_valid": self.is_valid,
            "readiness_status": self.readiness_status,
            "failure_code": self.failure_code,
        }


def _normalize_sync(sync: dict[str, Any] | None) -> dict[str, Any]:
    mode = str((sync or {}).get("mode", "selected"))
    if mode == "by_select":
        mode = "selected"
    if mode not in {"selected", "all", "none"}:
        raise ValueError("sync.mode must be 'selected', 'all', or 'none'")
    return {"mode": mode}


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {
        "allow_live_browse": True,
        "allow_sync": True,
    }
    if policy:
        normalized.update(policy)
    return normalized


def _build_intrinsic_capabilities(source_type: str) -> dict[str, Any]:
    return {
        "supports_partitions": False,
    }


def _loads_stored_json(raw: Any) -> Any:
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return None


def _normalize_authority(source_type: str, authority: dict[str, Any]) -> dict[str, Any]:
    catalog_system = str(authority.get("catalog_system", source_type))
    if catalog_system != source_type:
        raise ValueError("authority.catalog_system must match source_type")

    connection = authority.get("connection")
    if isinstance(connection, dict):
        normalized_connection = dict(connection)
    else:
        normalized_connection = {
            key: value
            for key, value in authority.items()
            if key not in {"catalog_system", "synthetic_catalog"}
        }

    synthetic_catalog = authority.get("synthetic_catalog")
    if source_type == "duckdb":
        if not isinstance(synthetic_catalog, str) or not synthetic_catalog.strip():
            raise ValueError("duckdb authority.synthetic_catalog is required")
        synthetic_catalog = synthetic_catalog.strip()
    elif synthetic_catalog is not None:
        raise ValueError("synthetic_catalog is only supported for duckdb sources")

    return {
        "catalog_system": catalog_system,
        "connection": normalized_connection,
        "synthetic_catalog": synthetic_catalog,
    }


class SourceRegistry:
    """Source registry and live-catalog access boundary."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def register_source(
        self,
        source_type: str,
        display_name: str,
        authority: dict[str, Any],
        sync: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_source_type(source_type)
        normalized_authority = _normalize_authority(source_type, authority)
        normalized_sync = _normalize_sync(sync)
        normalized_policy = _normalize_policy(policy)
        intrinsic_capabilities = _build_intrinsic_capabilities(source_type)

        source_id = f"src_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO sources (
                source_id,
                source_type,
                display_name,
                authority_json,
                sync_mode,
                intrinsic_capabilities_json,
                policy_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                source_id,
                source_type,
                display_name,
                json.dumps(normalized_authority),
                normalized_sync["mode"],
                json.dumps(intrinsic_capabilities),
                json.dumps(normalized_policy),
                now,
                now,
            ],
        )
        return self.get_source(source_id)

    def get_source(self, source_id: str, *, include_mappings: bool = True) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM sources WHERE source_id = ?", [source_id])
        if row is None:
            raise KeyError(f"Unknown source: {source_id}")
        return self._row_to_source(row, include_mappings=include_mappings)

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows("SELECT * FROM sources ORDER BY created_at")
        return [self._row_to_source(row, include_mappings=True) for row in rows]

    def ensure_source(
        self,
        source_type: str,
        display_name: str,
        authority: dict[str, Any],
        sync: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_source_type(source_type)
        existing = self.metadata.query_one(
            "SELECT * FROM sources WHERE display_name = ?",
            [display_name],
        )
        if existing is None:
            return self.register_source(
                source_type,
                display_name,
                authority,
                sync=sync,
                policy=policy,
            )

        now = now_iso()
        normalized_authority = _normalize_authority(source_type, authority)
        normalized_sync = _normalize_sync(sync)
        normalized_policy = _normalize_policy(policy)
        intrinsic_capabilities = _build_intrinsic_capabilities(source_type)
        self.metadata.execute(
            """
            UPDATE sources
            SET source_type = ?, authority_json = ?, sync_mode = ?,
                intrinsic_capabilities_json = ?, policy_json = ?, updated_at = ?
            WHERE source_id = ?
            """,
            [
                source_type,
                json.dumps(normalized_authority),
                normalized_sync["mode"],
                json.dumps(intrinsic_capabilities),
                json.dumps(normalized_policy),
                now,
                existing["source_id"],
            ],
        )
        return self.get_source(str(existing["source_id"]))

    def update_source(
        self,
        source_id: str,
        display_name: str | None = None,
        authority: dict[str, Any] | None = None,
        sync: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_source(source_id)
        updates: list[str] = []
        params: list[Any] = []

        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if authority is not None:
            existing_synthetic_catalog = existing["authority"].get("synthetic_catalog")
            next_synthetic_catalog = authority.get("synthetic_catalog")
            if (
                existing_synthetic_catalog is not None
                and next_synthetic_catalog is not None
                and next_synthetic_catalog != existing_synthetic_catalog
            ):
                raise ValueError("authority.synthetic_catalog is immutable once set")
            updates.append("authority_json = ?")
            params.append(json.dumps(_normalize_authority(existing["source_type"], authority)))
        if sync is not None:
            updates.append("sync_mode = ?")
            params.append(_normalize_sync(sync)["mode"])
        if policy is not None:
            updates.append("policy_json = ?")
            params.append(json.dumps(_normalize_policy(policy)))

        if not updates:
            return existing

        params.extend([now_iso(), source_id])
        self.metadata.execute(
            f"UPDATE sources SET {', '.join(updates)}, updated_at = ? WHERE source_id = ?",
            params,
        )
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> None:
        self.get_source(source_id)

        bindings_using_source_objects = self.metadata.query_rows(
            """
            SELECT DISTINCT b.binding_ref
            FROM typed_bindings b
            JOIN carrier_bindings cb ON cb.binding_id = b.binding_id
            JOIN source_objects o ON cb.source_object_ref = o.object_id
            WHERE o.source_id = ?
            """,
            [source_id],
        )
        if bindings_using_source_objects:
            refs = [str(row["binding_ref"]) for row in bindings_using_source_objects]
            raise DependencyError(
                f"Cannot delete source: {len(bindings_using_source_objects)} typed binding(s) depend on it",
                dependencies=refs,
            )

        mappings = self.metadata.query_rows(
            "SELECT mapping_id, engine_id FROM source_execution_mappings WHERE source_id = ?",
            [source_id],
        )
        if mappings:
            refs = [str(row["mapping_id"]) for row in mappings]
            raise DependencyError(
                f"Cannot delete source: {len(mappings)} mapping(s) depend on it",
                dependencies=refs,
            )

        self.metadata.execute("DELETE FROM sync_selections WHERE source_id = ?", [source_id])
        self.metadata.execute("DELETE FROM sync_jobs WHERE source_id = ?", [source_id])
        self.metadata.execute("DELETE FROM source_objects WHERE source_id = ?", [source_id])
        self.metadata.execute("DELETE FROM sources WHERE source_id = ?", [source_id])

    def validate_source(self, source_id: str) -> dict[str, Any]:
        source = self.get_source(source_id)
        return self.evaluate_source(source).to_dict(source_id=source_id)

    def get_source_readiness(self, source_id: str) -> dict[str, Any]:
        validation = self.validate_source(source_id)
        return {
            "source_id": source_id,
            "readiness_status": validation["readiness_status"],
            "failure_code": validation["failure_code"],
        }

    def get_adapter(self, source_id: str) -> CatalogAdapter:
        source = self.get_source(source_id)
        connection = source["authority"]["connection"]
        return build_catalog_adapter(source["source_type"], connection)

    def list_objects(
        self,
        source_id: str,
        object_type: str | None = None,
        schema_name: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM source_objects WHERE source_id = ?"
        params: list[Any] = [source_id]
        if object_type:
            sql += " AND object_type = ?"
            params.append(object_type)
        if schema_name:
            sql += " AND json_extract(authority_locator_json, '$.schema') = ?"
            params.append(schema_name)
        sql += " ORDER BY fqn"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_object(row) for row in rows]

    def get_object(self, source_id: str, object_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ? AND source_id = ?",
            [object_id, source_id],
        )
        if row is None:
            raise KeyError(f"Object {object_id!r} not found in source {source_id!r}")
        return self._row_to_object(row)

    def patch_object_properties(
        self, source_id: str, object_id: str, user_props: dict[str, Any]
    ) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ? AND source_id = ?",
            [object_id, source_id],
        )
        if row is None:
            raise KeyError(f"Object {object_id!r} not found in source {source_id!r}")
        if row["object_type"] != "column":
            raise ValueError(f"Object {object_id!r} is not a column (type={row['object_type']!r})")

        merged = {**json.loads(str(row["properties_json"])), **user_props}
        self.metadata.execute(
            "UPDATE source_objects SET properties_json = ?, updated_at = ? WHERE object_id = ?",
            [json.dumps(merged), now_iso(), object_id],
        )
        updated = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ?",
            [object_id],
        )
        if updated is None:
            raise KeyError(f"Object {object_id!r} not found in source {source_id!r}")
        return self._row_to_object(updated)

    def get_sync_mode(self, source_id: str) -> str:
        row = self.metadata.query_one(
            "SELECT sync_mode FROM sources WHERE source_id = ?",
            [source_id],
        )
        if row is None:
            raise KeyError(f"Unknown source: {source_id}")
        return str(row["sync_mode"])

    def add_sync_selection(
        self, source_id: str, schema_name: str, table_name: str
    ) -> dict[str, Any]:
        self.get_source(source_id)
        existing = self.metadata.query_one(
            "SELECT * FROM sync_selections WHERE source_id = ? AND schema_name = ? AND table_name = ?",
            [source_id, schema_name, table_name],
        )
        if existing is not None:
            return dict(existing)

        selection_id = f"sel_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO sync_selections (selection_id, source_id, schema_name, table_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [selection_id, source_id, schema_name, table_name, now],
        )
        return {
            "selection_id": selection_id,
            "source_id": source_id,
            "schema_name": schema_name,
            "table_name": table_name,
            "created_at": now,
        }

    def remove_sync_selection(self, selection_id: str) -> None:
        existing = self.metadata.query_one(
            "SELECT selection_id FROM sync_selections WHERE selection_id = ?",
            [selection_id],
        )
        if existing is None:
            raise KeyError(f"Unknown selection: {selection_id}")
        self.metadata.execute("DELETE FROM sync_selections WHERE selection_id = ?", [selection_id])

    def list_sync_selections(self, source_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM sync_selections WHERE source_id = ? ORDER BY schema_name, table_name",
            [source_id],
        )
        return [dict(row) for row in rows]

    def set_sync_selections(
        self, source_id: str, selections: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        self.get_source(source_id)
        self.metadata.execute("DELETE FROM sync_selections WHERE source_id = ?", [source_id])
        return [
            self.add_sync_selection(source_id, selection["schema_name"], selection["table_name"])
            for selection in selections
        ]

    def clear_sync_selections(self, source_id: str) -> None:
        self.get_source(source_id)
        self.metadata.execute("DELETE FROM sync_selections WHERE source_id = ?", [source_id])

    def browse_catalog_schemas(self, source_id: str) -> list[dict[str, Any]]:
        source = self.get_source(source_id)
        authority = source["authority"]
        adapter = build_catalog_adapter(source["source_type"], authority["connection"])
        catalog_name = authority.get("synthetic_catalog")
        if source["source_type"] == "trino":
            raw_catalog = authority["connection"].get("catalog")
            if isinstance(raw_catalog, str) and raw_catalog:
                catalog_name = raw_catalog
        schemas = adapter.list_schemas(catalog_name)
        return [{"name": schema.native_name, "properties": schema.properties} for schema in schemas]

    def browse_catalog_tables(self, source_id: str, schema_name: str) -> list[dict[str, Any]]:
        adapter = self.get_adapter(source_id)
        tables = adapter.list_tables(schema_name)
        return [
            {"name": table.native_name, "schema": schema_name, "properties": table.properties}
            for table in tables
        ]

    def preview_table(
        self,
        source_id: str,
        schema_name: str,
        table_name: str,
        limit: int = 100,
        columns: list[str] | None = None,
        filters: PreviewFilters | None = None,
    ) -> dict[str, Any]:
        adapter = self.get_adapter(source_id)
        result = adapter.preview_table(
            schema_name=schema_name,
            table_name=table_name,
            limit=limit,
            columns=columns,
            filters=filters,
        )
        return {
            "source_id": source_id,
            "schema_name": schema_name,
            "table_name": table_name,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "limit_requested": limit,
            "limit_applied": min(limit, MAX_PREVIEW_ROWS),
            "filters_applied": filters or {},
        }

    def evaluate_source(self, source: dict[str, Any]) -> SourceValidationResult:
        if source["status"] != "active":
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_inactive",
            )

        source_type = source["source_type"]
        try:
            validate_source_type(source_type)
        except ValueError:
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_type",
            )

        authority = source.get("authority")
        if not isinstance(authority, dict):
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_authority",
            )

        if authority.get("catalog_system") != source_type:
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_authority",
            )

        synthetic_catalog = authority.get("synthetic_catalog")
        if source_type == "duckdb" and (
            not isinstance(synthetic_catalog, str) or not synthetic_catalog.strip()
        ):
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_missing_synthetic_catalog",
            )

        connection = authority.get("connection")
        if not isinstance(connection, dict):
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_connection",
            )

        try:
            build_catalog_adapter(source_type, connection)
        except (KeyError, TypeError, ValueError):
            return SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_connection",
            )

        return SourceValidationResult(
            is_valid=True,
            readiness_status="ready",
        )

    def _row_to_source(self, row: dict[str, Any], *, include_mappings: bool) -> dict[str, Any]:
        source_type = str(row["source_type"])
        raw_authority = _loads_stored_json(row["authority_json"])
        authority_invalid = not isinstance(raw_authority, dict)
        authority = raw_authority if isinstance(raw_authority, dict) else {}
        raw_connection = authority.get("connection")
        connection = raw_connection if isinstance(raw_connection, dict) else {}
        synthetic_catalog = authority.get("synthetic_catalog")
        if synthetic_catalog is not None and not isinstance(synthetic_catalog, str):
            synthetic_catalog = None

        raw_intrinsic_capabilities = _loads_stored_json(row["intrinsic_capabilities_json"])
        intrinsic_capabilities_invalid = not isinstance(raw_intrinsic_capabilities, dict)
        intrinsic_capabilities = (
            raw_intrinsic_capabilities
            if not intrinsic_capabilities_invalid
            else _build_intrinsic_capabilities(source_type)
        )
        if "supports_partitions" not in intrinsic_capabilities:
            intrinsic_capabilities["supports_partitions"] = False

        raw_policy = _loads_stored_json(row["policy_json"])
        policy_invalid = not isinstance(raw_policy, dict)
        policy = _normalize_policy(raw_policy if not policy_invalid else None)
        source = {
            "source_id": row["source_id"],
            "source_type": source_type,
            "display_name": row["display_name"],
            "authority": {
                "catalog_system": str(authority.get("catalog_system", source_type)),
                "connection": connection,
                "synthetic_catalog": synthetic_catalog,
            },
            "sync": {"mode": str(row["sync_mode"])},
            "intrinsic_capabilities": intrinsic_capabilities,
            "policy": policy,
            "status": row["status"],
            "mappings": (
                self._list_mapping_summaries(str(row["source_id"])) if include_mappings else []
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if authority_invalid:
            validation = SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_authority",
            )
        elif intrinsic_capabilities_invalid:
            validation = SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_capabilities",
            )
        elif policy_invalid:
            validation = SourceValidationResult(
                is_valid=False,
                readiness_status="not_ready",
                failure_code="source_invalid_policy",
            )
        else:
            validation = self.evaluate_source(source)
        source["readiness_status"] = validation.readiness_status
        source["failure_code"] = validation.failure_code
        return source

    def _list_mapping_summaries(self, source_id: str) -> list[dict[str, Any]]:
        from app.registry.mapping_registry import list_mapping_summaries

        return list_mapping_summaries(self.metadata, source_id=source_id)

    def _row_to_object(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "object_id": row["object_id"],
            "source_id": row["source_id"],
            "object_type": row["object_type"],
            "parent_id": row["parent_id"],
            "native_name": row["native_name"],
            "native_id": row["native_id"],
            "fqn": row["fqn"],
            "authority_locator": json.loads(str(row["authority_locator_json"])),
            "properties": json.loads(str(row["properties_json"])),
            "sync_version": row["sync_version"],
            "synced_at": row["synced_at"],
        }
