from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.adapters.base import CatalogAdapter
from app.registry.common import now_iso
from app.registry.factories import build_catalog_adapter
from app.storage.metadata import MetadataStore


class DependencyError(Exception):
    """Raised when a delete is blocked by existing dependencies."""

    def __init__(self, message: str, dependencies: list[str] | None = None) -> None:
        super().__init__(message)
        self.dependencies = dependencies or []


class SourceRegistry:
    """Source registry and live-catalog access boundary."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def register_source(
        self,
        source_type: str,
        display_name: str,
        connection: dict[str, Any],
        capabilities: dict[str, Any] | None = None,
        sync_mode: str = "all",
    ) -> dict[str, Any]:
        source_id = f"src_{uuid4().hex[:12]}"
        now = now_iso()
        caps = capabilities or {}
        self.metadata.execute(
            """
            INSERT INTO sources (source_id, source_type, display_name, connection_json, capabilities_json, sync_mode, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                source_id,
                source_type,
                display_name,
                json.dumps(connection),
                json.dumps(caps),
                sync_mode,
                now,
                now,
            ],
        )
        return {
            "source_id": source_id,
            "source_type": source_type,
            "display_name": display_name,
            "connection": connection,
            "capabilities": caps,
            "sync_mode": sync_mode,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    def get_source(self, source_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM sources WHERE source_id = ?", [source_id])
        if row is None:
            raise KeyError(f"Unknown source: {source_id}")
        return self._row_to_source(row)

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows("SELECT * FROM sources ORDER BY created_at")
        return [self._row_to_source(r) for r in rows]

    def ensure_source(
        self,
        source_type: str,
        display_name: str,
        connection: dict[str, Any],
        sync_mode: str = "all",
    ) -> dict[str, Any]:
        existing = self.metadata.query_one(
            "SELECT * FROM sources WHERE display_name = ?",
            [display_name],
        )
        if existing is not None:
            now = now_iso()
            self.metadata.execute(
                """
                UPDATE sources
                SET source_type = ?, connection_json = ?, sync_mode = ?, updated_at = ?
                WHERE source_id = ?
                """,
                [source_type, json.dumps(connection), sync_mode, now, existing["source_id"]],
            )
            return self.get_source(existing["source_id"])
        return self.register_source(source_type, display_name, connection, sync_mode=sync_mode)

    def update_source(
        self,
        source_id: str,
        display_name: str | None = None,
        connection: dict[str, Any] | None = None,
        sync_mode: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_source(source_id)  # raises KeyError if missing
        now = now_iso()
        updates: list[str] = []
        params: list[Any] = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if connection is not None:
            updates.append("connection_json = ?")
            params.append(json.dumps(connection))
        if sync_mode is not None:
            updates.append("sync_mode = ?")
            params.append(sync_mode)
        if not updates:
            return existing
        updates.append("updated_at = ?")
        params.append(now)
        params.append(source_id)
        self.metadata.execute(
            f"UPDATE sources SET {', '.join(updates)} WHERE source_id = ?",
            params,
        )
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> None:
        self.get_source(source_id)  # raises KeyError if missing

        # Block if typed bindings reference this source's objects.
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

        # Block if bindings reference this source
        bindings = self.metadata.query_rows(
            "SELECT binding_id, engine_id FROM source_engine_bindings WHERE source_id = ?",
            [source_id],
        )
        if bindings:
            refs = [r["binding_id"] for r in bindings]
            raise DependencyError(
                f"Cannot delete source: {len(bindings)} binding(s) depend on it",
                dependencies=refs,
            )

        # Safe to delete — only owned/orphan data remains
        self.metadata.execute("DELETE FROM sync_selections WHERE source_id = ?", [source_id])
        self.metadata.execute("DELETE FROM sync_jobs WHERE source_id = ?", [source_id])
        self.metadata.execute("DELETE FROM source_objects WHERE source_id = ?", [source_id])
        self.metadata.execute("DELETE FROM sources WHERE source_id = ?", [source_id])

    def get_adapter(self, source_id: str) -> CatalogAdapter:
        source = self.get_source(source_id)
        return build_catalog_adapter(source["source_type"], source["connection"])

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
            sql += " AND fqn LIKE ?"
            params.append(f"%.{schema_name}.%")
        sql += " ORDER BY fqn"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_object(r) for r in rows]

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
        """Merge user_props into an existing column source_object's properties_json."""
        row = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ? AND source_id = ?",
            [object_id, source_id],
        )
        if row is None:
            raise KeyError(f"Object {object_id!r} not found in source {source_id!r}")
        if row["object_type"] != "column":
            raise ValueError(f"Object {object_id!r} is not a column (type={row['object_type']!r})")
        existing_props = json.loads(row["properties_json"])
        merged = {**existing_props, **user_props}
        now = now_iso()
        self.metadata.execute(
            "UPDATE source_objects SET properties_json = ?, updated_at = ? WHERE object_id = ?",
            [json.dumps(merged), now, object_id],
        )
        updated = self.metadata.query_one(
            "SELECT * FROM source_objects WHERE object_id = ?", [object_id]
        )
        assert updated is not None
        return self._row_to_object(updated)

    def get_sync_mode(self, source_id: str) -> str:
        row = self.metadata.query_one(
            "SELECT sync_mode FROM sources WHERE source_id = ?",
            [source_id],
        )
        if row is None:
            raise KeyError(f"Unknown source: {source_id}")
        return str(row.get("sync_mode", "all"))

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
            "INSERT INTO sync_selections (selection_id, source_id, schema_name, table_name, created_at) VALUES (?, ?, ?, ?, ?)",
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
        return [dict(r) for r in rows]

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
        adapter = build_catalog_adapter(source["source_type"], source["connection"])
        catalog_name = None
        if source["source_type"] == "trino":
            raw_catalog = source["connection"].get("catalog")
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

    def _row_to_source(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "source_type": row["source_type"],
            "display_name": row["display_name"],
            "connection": json.loads(row["connection_json"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "sync_mode": row.get("sync_mode", "all"),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_object(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "object_id": row["object_id"],
            "source_id": row["source_id"],
            "object_type": row["object_type"],
            "parent_id": row["parent_id"],
            "native_name": row["native_name"],
            "native_id": row["native_id"],
            "fqn": row["fqn"],
            "properties": json.loads(row["properties_json"]),
            "sync_version": row["sync_version"],
            "synced_at": row["synced_at"],
        }
