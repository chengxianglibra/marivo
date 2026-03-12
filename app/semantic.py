from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SemanticService:
    """CRUD for semantic entities, metrics, and mappings with revision
    tracking and draft/published lifecycle."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    # ── Entity CRUD ──────────────────────────────────────────────

    def create_entity(
        self,
        name: str,
        display_name: str,
        keys: list[str],
        description: str = "",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entity_id = f"ent_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_entities
                (entity_id, name, display_name, description, keys_json, properties_json, status, revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [entity_id, name, display_name, description, json.dumps(keys), json.dumps(properties or {}), now, now],
        )
        return self.get_entity(entity_id)

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM semantic_entities WHERE entity_id = ?", [entity_id])
        if row is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        return self._row_to_entity(row)

    def list_entities(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entities WHERE status = ? ORDER BY name", [status]
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM semantic_entities ORDER BY name")
        return [self._row_to_entity(r) for r in rows]

    def update_entity(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        entity = self.get_entity(entity_id)  # verify exists
        now = _now_iso()
        updates: list[str] = []
        params: list[Any] = []
        for field, col in [
            ("display_name", "display_name"),
            ("description", "description"),
        ]:
            if field in kwargs:
                updates.append(f"{col} = ?")
                params.append(kwargs[field])
        if "keys" in kwargs:
            updates.append("keys_json = ?")
            params.append(json.dumps(kwargs["keys"]))
        if "properties" in kwargs:
            updates.append("properties_json = ?")
            params.append(json.dumps(kwargs["properties"]))
        if not updates:
            return entity
        updates.append("updated_at = ?")
        params.append(now)
        params.append(entity_id)
        self.metadata.execute(
            f"UPDATE semantic_entities SET {', '.join(updates)} WHERE entity_id = ?",
            params,
        )
        return self.get_entity(entity_id)

    def publish_entity(self, entity_id: str) -> dict[str, Any]:
        entity = self.get_entity(entity_id)
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET status = 'published', revision = revision + 1, updated_at = ? WHERE entity_id = ?",
            [now, entity_id],
        )
        return self.get_entity(entity_id)

    def deprecate_entity(self, entity_id: str) -> dict[str, Any]:
        self.get_entity(entity_id)  # verify exists
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET status = 'deprecated', updated_at = ? WHERE entity_id = ?",
            [now, entity_id],
        )
        return self.get_entity(entity_id)

    # ── Metric CRUD ──────────────────────────────────────────────

    def create_metric(
        self,
        name: str,
        display_name: str,
        definition_sql: str,
        dimensions: list[str],
        description: str = "",
        entity_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metric_id = f"met_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_metrics
                (metric_id, name, display_name, description, definition_sql, dimensions_json,
                 entity_id, properties_json, status, revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                metric_id, name, display_name, description, definition_sql,
                json.dumps(dimensions), entity_id, json.dumps(properties or {}), now, now,
            ],
        )
        return self.get_metric(metric_id)

    def get_metric(self, metric_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM semantic_metrics WHERE metric_id = ?", [metric_id])
        if row is None:
            raise KeyError(f"Unknown metric: {metric_id}")
        return self._row_to_metric(row)

    def list_metrics(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metrics WHERE status = ? ORDER BY name", [status]
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM semantic_metrics ORDER BY name")
        return [self._row_to_metric(r) for r in rows]

    def update_metric(self, metric_id: str, **kwargs: Any) -> dict[str, Any]:
        metric = self.get_metric(metric_id)
        now = _now_iso()
        updates: list[str] = []
        params: list[Any] = []
        for field, col in [
            ("display_name", "display_name"),
            ("description", "description"),
            ("definition_sql", "definition_sql"),
            ("entity_id", "entity_id"),
        ]:
            if field in kwargs:
                updates.append(f"{col} = ?")
                params.append(kwargs[field])
        if "dimensions" in kwargs:
            updates.append("dimensions_json = ?")
            params.append(json.dumps(kwargs["dimensions"]))
        if "properties" in kwargs:
            updates.append("properties_json = ?")
            params.append(json.dumps(kwargs["properties"]))
        if not updates:
            return metric
        updates.append("updated_at = ?")
        params.append(now)
        params.append(metric_id)
        self.metadata.execute(
            f"UPDATE semantic_metrics SET {', '.join(updates)} WHERE metric_id = ?",
            params,
        )
        return self.get_metric(metric_id)

    def publish_metric(self, metric_id: str) -> dict[str, Any]:
        self.get_metric(metric_id)
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_metrics SET status = 'published', revision = revision + 1, updated_at = ? WHERE metric_id = ?",
            [now, metric_id],
        )
        return self.get_metric(metric_id)

    # ── Mapping CRUD ─────────────────────────────────────────────

    def create_mapping(
        self,
        semantic_type: str,
        semantic_id: str,
        object_id: str,
        mapping_type: str,
        mapping_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mapping_id = f"map_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_mappings
                (mapping_id, semantic_type, semantic_id, object_id, mapping_type, mapping_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [mapping_id, semantic_type, semantic_id, object_id, mapping_type, json.dumps(mapping_json or {}), now, now],
        )
        return self._get_mapping(mapping_id)

    def delete_mapping(self, mapping_id: str) -> None:
        existing = self._get_mapping(mapping_id)
        if existing is None:
            raise KeyError(f"Unknown mapping: {mapping_id}")
        self.metadata.execute("DELETE FROM semantic_mappings WHERE mapping_id = ?", [mapping_id])

    def list_mappings(
        self,
        semantic_type: str | None = None,
        semantic_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM semantic_mappings WHERE 1=1"
        params: list[Any] = []
        if semantic_type:
            sql += " AND semantic_type = ?"
            params.append(semantic_type)
        if semantic_id:
            sql += " AND semantic_id = ?"
            params.append(semantic_id)
        sql += " ORDER BY created_at"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_mapping(r) for r in rows]

    def _get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM semantic_mappings WHERE mapping_id = ?", [mapping_id])
        if row is None:
            return None
        return self._row_to_mapping(row)

    # ── Row converters ───────────────────────────────────────────

    def _row_to_entity(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "keys": json.loads(row["keys_json"]),
            "properties": json.loads(row["properties_json"]),
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric_id": row["metric_id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "definition_sql": row["definition_sql"],
            "dimensions": json.loads(row["dimensions_json"]),
            "entity_id": row["entity_id"],
            "properties": json.loads(row["properties_json"]),
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "mapping_id": row["mapping_id"],
            "semantic_type": row["semantic_type"],
            "semantic_id": row["semantic_id"],
            "object_id": row["object_id"],
            "mapping_type": row["mapping_type"],
            "mapping_json": json.loads(row["mapping_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
