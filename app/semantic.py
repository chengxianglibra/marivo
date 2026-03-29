from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.semantic_runtime.semantic_metadata import (
    entity_runtime_metadata,
    metric_runtime_metadata,
)
from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
        level: str | None = None,
        join_constraints: dict[str, Any] | None = None,
        upstream_dependencies: list[str] | None = None,
        lineage: list[str] | None = None,
        quality_expectations: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entity_id = f"ent_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_entities
                (
                    entity_id, name, display_name, description, keys_json, level,
                    join_constraints_json, upstream_dependencies_json, lineage_json,
                    quality_expectations_json, properties_json, status, revision, created_at, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                entity_id,
                name,
                display_name,
                description,
                json.dumps(keys),
                level,
                json.dumps(join_constraints or {}),
                json.dumps(upstream_dependencies or []),
                json.dumps(lineage or []),
                json.dumps(quality_expectations or {}),
                json.dumps(properties or {}),
                now,
                now,
            ],
        )
        return self.get_entity(entity_id)

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entities WHERE entity_id = ?", [entity_id]
        )
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
        if "level" in kwargs:
            updates.append("level = ?")
            params.append(kwargs["level"])
        if "join_constraints" in kwargs:
            updates.append("join_constraints_json = ?")
            params.append(json.dumps(kwargs["join_constraints"]))
        if "upstream_dependencies" in kwargs:
            updates.append("upstream_dependencies_json = ?")
            params.append(json.dumps(kwargs["upstream_dependencies"]))
        if "lineage" in kwargs:
            updates.append("lineage_json = ?")
            params.append(json.dumps(kwargs["lineage"]))
        if "quality_expectations" in kwargs:
            updates.append("quality_expectations_json = ?")
            params.append(json.dumps(kwargs["quality_expectations"]))
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

    def patch_entity_properties(
        self, entity_id: str, properties_patch: dict[str, Any]
    ) -> dict[str, Any]:
        """G-5d: Incrementally merge properties_patch into a published entity's properties_json.

        Only published entities may be patched (draft entities must go through
        publish first).  Bumps revision and updated_at.

        Raises:
            KeyError: entity not found.
            ValueError: entity is not published, or properties_patch is empty/invalid.
        """
        entity = self.get_entity(entity_id)  # raises KeyError if missing
        if entity.get("status") != "published":
            raise ValueError(
                f"Entity '{entity_id}' is not published (status={entity.get('status')}). "
                "Only published entities may be patched."
            )
        if not properties_patch or not isinstance(properties_patch, dict):
            raise ValueError("properties_patch must be a non-empty dict")

        current_props: dict[str, Any] = dict(entity.get("properties") or {})
        # Deep merge: if both sides have a "fields" dict, merge field-by-field
        # so patching one column's unit doesn't wipe other columns.
        if "fields" in properties_patch and isinstance(properties_patch["fields"], dict):
            merged_fields = dict(current_props.get("fields") or {})
            for col, col_props in properties_patch["fields"].items():
                if isinstance(col_props, dict):
                    existing = dict(merged_fields.get(col) or {})
                    existing.update(col_props)
                    merged_fields[col] = existing
                else:
                    merged_fields[col] = col_props
            current_props = {k: v for k, v in current_props.items() if k != "fields"}
            current_props["fields"] = merged_fields
            remaining_patch = {k: v for k, v in properties_patch.items() if k != "fields"}
            current_props.update(remaining_patch)
        else:
            current_props.update(properties_patch)
        now = _now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET properties_json = ?, revision = revision + 1, updated_at = ? WHERE entity_id = ?",
            [json.dumps(current_props), now, entity_id],
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
        grain: str | None = None,
        measure_type: str | None = None,
        allowed_dimensions: list[str] | None = None,
        lineage: list[str] | None = None,
        quality_expectations: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        desired_direction: str | None = None,
    ) -> dict[str, Any]:
        metric_id = f"met_{uuid4().hex[:12]}"
        now = _now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_metrics
                (
                    metric_id, name, display_name, description, definition_sql, dimensions_json,
                    entity_id, grain, measure_type, allowed_dimensions_json, lineage_json,
                    quality_expectations_json, properties_json, desired_direction,
                    status, revision, created_at, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                metric_id,
                name,
                display_name,
                description,
                definition_sql,
                json.dumps(dimensions),
                entity_id,
                grain,
                measure_type,
                json.dumps(allowed_dimensions or []),
                json.dumps(lineage or []),
                json.dumps(quality_expectations or {}),
                json.dumps(properties or {}),
                desired_direction,
                now,
                now,
            ],
        )
        return self.get_metric(metric_id)

    def get_metric(self, metric_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE metric_id = ?", [metric_id]
        )
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
        if "grain" in kwargs:
            updates.append("grain = ?")
            params.append(kwargs["grain"])
        if "measure_type" in kwargs:
            updates.append("measure_type = ?")
            params.append(kwargs["measure_type"])
        if "allowed_dimensions" in kwargs:
            updates.append("allowed_dimensions_json = ?")
            params.append(json.dumps(kwargs["allowed_dimensions"]))
        if "lineage" in kwargs:
            updates.append("lineage_json = ?")
            params.append(json.dumps(kwargs["lineage"]))
        if "quality_expectations" in kwargs:
            updates.append("quality_expectations_json = ?")
            params.append(json.dumps(kwargs["quality_expectations"]))
        if "properties" in kwargs:
            updates.append("properties_json = ?")
            params.append(json.dumps(kwargs["properties"]))
        if "desired_direction" in kwargs:
            updates.append("desired_direction = ?")
            params.append(kwargs["desired_direction"])
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
            [
                mapping_id,
                semantic_type,
                semantic_id,
                object_id,
                mapping_type,
                json.dumps(mapping_json or {}),
                now,
                now,
            ],
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
        row = self.metadata.query_one(
            "SELECT * FROM semantic_mappings WHERE mapping_id = ?", [mapping_id]
        )
        if row is None:
            return None
        return self._row_to_mapping(row)

    # ── Row converters ───────────────────────────────────────────

    def _row_to_entity(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = json.loads(row["properties_json"])
        semantic_metadata = entity_runtime_metadata(
            level=row["level"],
            join_constraints_json=row["join_constraints_json"],
            upstream_dependencies_json=row["upstream_dependencies_json"],
            lineage_json=row["lineage_json"],
            quality_expectations_json=row["quality_expectations_json"],
        )
        return {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "keys": json.loads(row["keys_json"]),
            "properties": properties,
            **semantic_metadata,
            "status": row["status"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _row_to_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = json.loads(row["properties_json"])
        dimensions = json.loads(row["dimensions_json"])
        semantic_metadata = metric_runtime_metadata(
            grain=row["grain"],
            measure_type=row["measure_type"],
            allowed_dimensions_json=row["allowed_dimensions_json"],
            lineage_json=row["lineage_json"],
            quality_expectations_json=row["quality_expectations_json"],
            dimensions=dimensions,
        )
        return {
            "metric_id": row["metric_id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "definition_sql": row["definition_sql"],
            "dimensions": dimensions,
            "entity_id": row["entity_id"],
            "desired_direction": row.get("desired_direction"),
            "properties": properties,
            **semantic_metadata,
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
