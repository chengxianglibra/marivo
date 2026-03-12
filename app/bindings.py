from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BindingService:
    """Manages source-engine bindings — which engines can query which sources."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create_binding(
        self,
        source_id: str,
        engine_id: str,
        priority: int = 0,
        namespace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a binding between a source and an engine.

        Validates that both source and engine exist before inserting.
        Raises KeyError if source or engine is not found.
        """
        if namespace is None:
            namespace = {}

        # Validate source exists
        src = self.metadata.query_one(
            "SELECT source_id FROM sources WHERE source_id = ?", [source_id]
        )
        if src is None:
            raise KeyError(f"Unknown source: {source_id}")

        # Validate engine exists
        eng = self.metadata.query_one(
            "SELECT engine_id FROM engines WHERE engine_id = ?", [engine_id]
        )
        if eng is None:
            raise KeyError(f"Unknown engine: {engine_id}")

        binding_id = f"bind_{uuid4().hex[:12]}"
        now = _now_iso()
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
        """Idempotent binding registration keyed on (source_id, engine_id).

        Returns the existing binding if one already exists, otherwise
        creates a new one.
        """
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
        return [self._row_to_binding(r) for r in rows]

    def delete_binding(self, binding_id: str) -> None:
        """Delete a binding by ID. Raises KeyError if not found."""
        existing = self.metadata.query_one(
            "SELECT binding_id FROM source_engine_bindings WHERE binding_id = ?",
            [binding_id],
        )
        if existing is None:
            raise KeyError(f"Unknown binding: {binding_id}")
        self.metadata.execute(
            "DELETE FROM source_engine_bindings WHERE binding_id = ?",
            [binding_id],
        )

    def get_engines_for_source(self, source_id: str) -> list[dict[str, Any]]:
        """Return engines bound to a source, ordered by priority DESC.

        Each dict includes both binding info and engine info.
        """
        rows = self.metadata.query_rows(
            """
            SELECT b.binding_id, b.source_id, b.engine_id, b.priority, b.namespace_json,
                   b.status, b.created_at, b.updated_at,
                   e.engine_type, e.display_name, e.connection_json, e.capabilities_json,
                   e.status AS engine_status
            FROM source_engine_bindings b
            JOIN engines e ON b.engine_id = e.engine_id
            WHERE b.source_id = ? AND b.status = 'active'
            ORDER BY b.priority DESC, b.created_at
            """,
            [source_id],
        )
        results = []
        for r in rows:
            results.append({
                "binding_id": r["binding_id"],
                "source_id": r["source_id"],
                "engine_id": r["engine_id"],
                "priority": r["priority"],
                "namespace": json.loads(r["namespace_json"]),
                "binding_status": r["status"],
                "engine_type": r["engine_type"],
                "display_name": r["display_name"],
                "connection": json.loads(r["connection_json"]),
                "capabilities": json.loads(r["capabilities_json"]),
                "engine_status": r["engine_status"],
            })
        return results

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
