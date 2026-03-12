from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EngineService:
    """Manages the engine registry and analytics-engine factory."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def register_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine_id = f"eng_{uuid4().hex[:12]}"
        now = _now_iso()
        caps = capabilities or {}
        self.metadata.execute(
            """
            INSERT INTO engines (engine_id, engine_type, display_name, connection_json, capabilities_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [engine_id, engine_type, display_name, json.dumps(connection), json.dumps(caps), now, now],
        )
        return {
            "engine_id": engine_id,
            "engine_type": engine_type,
            "display_name": display_name,
            "connection": connection,
            "capabilities": caps,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    def get_engine(self, engine_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM engines WHERE engine_id = ?", [engine_id])
        if row is None:
            raise KeyError(f"Unknown engine: {engine_id}")
        return self._row_to_engine(row)

    def list_engines(self) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows("SELECT * FROM engines ORDER BY created_at")
        return [self._row_to_engine(r) for r in rows]

    def ensure_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
    ) -> dict[str, Any]:
        """Idempotent engine registration keyed on *display_name*."""
        existing = self.metadata.query_one(
            "SELECT * FROM engines WHERE display_name = ?",
            [display_name],
        )
        if existing is not None:
            return self._row_to_engine(existing)
        return self.register_engine(engine_type, display_name, connection)

    def build_analytics_engine(self, engine_id: str) -> AnalyticsEngine:
        engine = self.get_engine(engine_id)
        return _build_analytics_engine(engine["engine_type"], engine["connection"])

    def _row_to_engine(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "engine_id": row["engine_id"],
            "engine_type": row["engine_type"],
            "display_name": row["display_name"],
            "connection": json.loads(row["connection_json"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _build_analytics_engine(engine_type: str, connection: dict[str, Any]) -> AnalyticsEngine:
    if engine_type == "duckdb":
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        return DuckDBAnalyticsEngine(connection["path"])
    if engine_type == "trino":
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        return TrinoAnalyticsEngine(
            host=connection["host"],
            port=connection.get("port", 8080),
            user=connection.get("user", "omnidb"),
            catalog=connection.get("catalog", "hive"),
            schema=connection.get("schema", "default"),
        )
    if engine_type == "spark_connect":
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine

        return SparkConnectAnalyticsEngine(remote=connection["remote"])
    if engine_type == "spark_thrift":
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine

        return SparkThriftAnalyticsEngine(
            host=connection["host"],
            port=connection.get("port", 10009),
            username=connection.get("username", "omnidb"),
            database=connection.get("database", "default"),
            auth=connection.get("auth", "NOSASL"),
        )
    raise ValueError(f"Unsupported engine type: {engine_type}")
