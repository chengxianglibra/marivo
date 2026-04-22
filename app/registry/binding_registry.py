from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.registry.common import now_iso
from app.storage.metadata import MetadataStore


class BindingRegistry:
    """Binding registry between sources and engines."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create_binding(
        self,
        source_id: str,
        engine_id: str,
        priority: int = 0,
        namespace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        namespace = namespace or {}
        source = self.metadata.query_one(
            "SELECT source_id FROM sources WHERE source_id = ?", [source_id]
        )
        if source is None:
            raise KeyError(f"Unknown source: {source_id}")
        engine = self.metadata.query_one(
            "SELECT engine_id FROM engines WHERE engine_id = ?", [engine_id]
        )
        if engine is None:
            raise KeyError(f"Unknown engine: {engine_id}")

        binding_id = f"bind_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO source_engine_bindings
                (binding_id, source_id, engine_id, priority, namespace_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [binding_id, source_id, engine_id, priority, json.dumps(namespace), now, now],
        )
        return {
            "binding_id": binding_id,
            "source_id": source_id,
            "engine_id": engine_id,
            "priority": priority,
            "namespace": namespace,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    def ensure_binding(
        self,
        source_id: str,
        engine_id: str,
        priority: int = 0,
        namespace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.metadata.query_one(
            "SELECT * FROM source_engine_bindings WHERE source_id = ? AND engine_id = ?",
            [source_id, engine_id],
        )
        if existing is not None:
            return self._row_to_binding(existing)
        return self.create_binding(source_id, engine_id, priority, namespace)

    def get_binding(self, binding_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM source_engine_bindings WHERE binding_id = ?",
            [binding_id],
        )
        if row is None:
            raise KeyError(f"Unknown binding: {binding_id}")
        return self._row_to_binding(row)

    def list_bindings(
        self,
        source_id: str | None = None,
        engine_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM source_engine_bindings WHERE 1=1"
        params: list[Any] = []
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if engine_id is not None:
            sql += " AND engine_id = ?"
            params.append(engine_id)
        sql += " ORDER BY priority DESC, created_at"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_binding(row) for row in rows]

    def delete_binding(self, binding_id: str) -> None:
        existing = self.metadata.query_one(
            "SELECT binding_id FROM source_engine_bindings WHERE binding_id = ?",
            [binding_id],
        )
        if existing is None:
            raise KeyError(f"Unknown binding: {binding_id}")
        self.metadata.execute(
            "DELETE FROM source_engine_bindings WHERE binding_id = ?", [binding_id]
        )

    def get_engines_for_source(self, source_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT b.binding_id, b.source_id, b.engine_id, b.priority, b.namespace_json,
                   b.status, b.created_at, b.updated_at,
                   e.engine_type, e.display_name, e.connection_json,
                   e.default_namespace_json, e.intrinsic_capabilities_json,
                   e.deployment_capabilities_json, e.policy_json,
                   e.status AS engine_status
            FROM source_engine_bindings b
            JOIN engines e ON b.engine_id = e.engine_id
            WHERE b.source_id = ? AND b.status = 'active'
            ORDER BY b.priority DESC, b.created_at
            """,
            [source_id],
        )
        return [
            {
                "binding_id": row["binding_id"],
                "source_id": row["source_id"],
                "engine_id": row["engine_id"],
                "priority": row["priority"],
                "namespace": json.loads(row["namespace_json"]),
                "binding_status": row["status"],
                "engine_type": row["engine_type"],
                "display_name": row["display_name"],
                "connection": json.loads(row["connection_json"]),
                "default_namespace": json.loads(row["default_namespace_json"]),
                "intrinsic_capabilities": json.loads(row["intrinsic_capabilities_json"]),
                "deployment_capabilities": json.loads(row["deployment_capabilities_json"]),
                "policy": json.loads(row["policy_json"]),
                "engine_status": row["engine_status"],
            }
            for row in rows
        ]

    def _row_to_binding(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "binding_id": row["binding_id"],
            "source_id": row["source_id"],
            "engine_id": row["engine_id"],
            "priority": row["priority"],
            "namespace": json.loads(row["namespace_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
