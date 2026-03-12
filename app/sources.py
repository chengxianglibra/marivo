from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.adapters.base import CatalogAdapter
from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceService:
    """Manages the source registry and adapter factory."""

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
        now = _now_iso()
        caps = capabilities or {}
        self.metadata.execute(
            """
            INSERT INTO sources (source_id, source_type, display_name, connection_json, capabilities_json, sync_mode, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [source_id, source_type, display_name, json.dumps(connection), json.dumps(caps), sync_mode, now, now],
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
        """Idempotent source registration keyed on *display_name*.

        Returns the existing source if one with the same display_name
        already exists (updating connection and sync_mode from the
        supplied values), otherwise registers a new one.
        """
        existing = self.metadata.query_one(
            "SELECT * FROM sources WHERE display_name = ?",
            [display_name],
        )
        if existing is not None:
            now = _now_iso()
            self.metadata.execute(
                """
                UPDATE sources
                SET connection_json = ?, sync_mode = ?, updated_at = ?
                WHERE source_id = ?
                """,
                [json.dumps(connection), sync_mode, now, existing["source_id"]],
            )
            source = self._row_to_source(existing)
            source["connection"] = connection
            source["sync_mode"] = sync_mode
            source["updated_at"] = now
            return source
        return self.register_source(source_type, display_name, connection, sync_mode=sync_mode)

    def get_adapter(self, source_id: str) -> CatalogAdapter:
        source = self.get_source(source_id)
        return _build_adapter(source["source_type"], source["connection"])

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

    # ── Sync mode helpers ───────────────────────────────────────

    def get_sync_mode(self, source_id: str) -> str:
        row = self.metadata.query_one(
            "SELECT sync_mode FROM sources WHERE source_id = ?", [source_id],
        )
        if row is None:
            raise KeyError(f"Unknown source: {source_id}")
        return row.get("sync_mode", "all")

    # ── Sync selection CRUD ─────────────────────────────────────

    def add_sync_selection(self, source_id: str, schema_name: str, table_name: str) -> dict[str, Any]:
        """Idempotent insert of a sync selection."""
        self.get_source(source_id)  # verify exists
        existing = self.metadata.query_one(
            "SELECT * FROM sync_selections WHERE source_id = ? AND schema_name = ? AND table_name = ?",
            [source_id, schema_name, table_name],
        )
        if existing is not None:
            return dict(existing)
        sel_id = f"sel_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            "INSERT INTO sync_selections (selection_id, source_id, schema_name, table_name, created_at) VALUES (?, ?, ?, ?, ?)",
            [sel_id, source_id, schema_name, table_name, now],
        )
        return {"selection_id": sel_id, "source_id": source_id, "schema_name": schema_name, "table_name": table_name, "created_at": now}

    def remove_sync_selection(self, selection_id: str) -> None:
        existing = self.metadata.query_one(
            "SELECT selection_id FROM sync_selections WHERE selection_id = ?", [selection_id],
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

    def set_sync_selections(self, source_id: str, selections: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Replace all selections for a source atomically."""
        self.get_source(source_id)  # verify exists
        self.metadata.execute("DELETE FROM sync_selections WHERE source_id = ?", [source_id])
        results = []
        for sel in selections:
            results.append(self.add_sync_selection(source_id, sel["schema_name"], sel["table_name"]))
        return results

    def clear_sync_selections(self, source_id: str) -> None:
        self.get_source(source_id)  # verify exists
        self.metadata.execute("DELETE FROM sync_selections WHERE source_id = ?", [source_id])

    # ── Live catalog browsing ───────────────────────────────────

    def browse_catalog_schemas(self, source_id: str) -> list[dict[str, Any]]:
        """Call the adapter to list schemas live (no persistence)."""
        adapter = self.get_adapter(source_id)
        schemas = adapter.list_schemas()
        return [{"name": s.native_name, "properties": s.properties} for s in schemas]

    def browse_catalog_tables(self, source_id: str, schema_name: str) -> list[dict[str, Any]]:
        """Call the adapter to list tables live (no persistence)."""
        adapter = self.get_adapter(source_id)
        tables = adapter.list_tables(schema_name)
        return [{"name": t.native_name, "schema": schema_name, "properties": t.properties} for t in tables]


def _build_adapter(source_type: str, connection: dict[str, Any]) -> CatalogAdapter:
    if source_type in ("local", "duckdb"):
        from app.adapters.duckdb_adapter import DuckDBCatalogAdapter
        return DuckDBCatalogAdapter(connection["path"])
    if source_type == "hive_metastore":
        from app.adapters.hive_adapter import HiveMetastoreAdapter
        return HiveMetastoreAdapter(
            host=connection["host"],
            port=connection.get("port", 9083),
        )
    if source_type == "unity_catalog":
        from app.adapters.unity_adapter import UnityCatalogAdapter
        return UnityCatalogAdapter(
            host=connection["host"],
            token=connection.get("token", ""),
            catalog_name=connection.get("catalog", "main"),
        )
    if source_type == "aws_glue":
        from app.adapters.glue_adapter import GlueCatalogAdapter
        return GlueCatalogAdapter(
            region=connection.get("region", "us-east-1"),
            catalog_id=connection.get("catalog_id"),
        )
    if source_type == "polaris":
        from app.adapters.polaris_adapter import PolarisAdapter
        return PolarisAdapter(
            host=connection["host"],
            token=connection.get("token", ""),
            warehouse=connection.get("warehouse", "default"),
        )
    if source_type == "trino":
        from app.adapters.trino_adapter import TrinoCatalogAdapter
        return TrinoCatalogAdapter(
            host=connection["host"],
            port=connection.get("port", 8080),
            user=connection.get("user", "omnidb"),
            password=connection.get("password"),
            http_scheme=connection.get("http_scheme", "http"),
            catalog=connection.get("catalog", "hive"),
            schema=connection.get("schema", "default"),
        )
    raise ValueError(f"Unsupported source type: {source_type}")
