from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.execution.capabilities import (
    EngineCapabilityProfile,
    build_engine_capability_profile,
)
from app.registry.common import now_iso
from app.registry.factories import build_analytics_engine
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


class EngineRegistry:
    """Engine registry and analytics factory boundary."""

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
        now = now_iso()
        caps = build_engine_capability_profile(engine_type, capabilities).to_dict()
        self.metadata.execute(
            """
            INSERT INTO engines (engine_id, engine_type, display_name, connection_json, capabilities_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                engine_id,
                engine_type,
                display_name,
                json.dumps(connection),
                json.dumps(caps),
                now,
                now,
            ],
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
        return [self._row_to_engine(row) for row in rows]

    def ensure_engine(
        self,
        engine_type: str,
        display_name: str,
        connection: dict[str, Any],
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.metadata.query_one(
            "SELECT * FROM engines WHERE display_name = ?",
            [display_name],
        )
        if existing is not None:
            return self._row_to_engine(existing)
        return self.register_engine(
            engine_type, display_name, connection, capabilities=capabilities
        )

    def build_analytics_engine(self, engine_id: str) -> AnalyticsEngine:
        engine = self.get_engine(engine_id)
        return build_analytics_engine(engine["engine_type"], engine["connection"])

    def get_capability_profile(self, engine_id: str) -> EngineCapabilityProfile:
        engine = self.get_engine(engine_id)
        return build_engine_capability_profile(
            engine["engine_type"],
            engine["capabilities"],
        )

    def _row_to_engine(self, row: dict[str, Any]) -> dict[str, Any]:
        capabilities = build_engine_capability_profile(
            row["engine_type"],
            json.loads(row["capabilities_json"]),
        ).to_dict()
        return {
            "engine_id": row["engine_id"],
            "engine_type": row["engine_type"],
            "display_name": row["display_name"],
            "connection": json.loads(row["connection_json"]),
            "capabilities": capabilities,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
