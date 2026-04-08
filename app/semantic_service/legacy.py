from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .common import SemanticServiceSupport, now_iso


class LegacySemanticService(SemanticServiceSupport):
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
        created_at = now_iso()
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
                created_at,
                created_at,
            ],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entities WHERE entity_id = ?",
            [entity_id],
        )
        if row is None:
            raise self._not_found(f"Unknown entity: {entity_id}")
        return self._row_to_entity(row)

    def list_entities(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_entities WHERE status = ? ORDER BY name",
                [status],
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM semantic_entities ORDER BY name")
        return [self._row_to_entity(row) for row in rows]

    def update_entity(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        entity = self.get_entity(entity_id)
        updates: list[str] = []
        params: list[Any] = []
        for field, col in [("display_name", "display_name"), ("description", "description")]:
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
        params.append(now_iso())
        params.append(entity_id)
        self.metadata.execute(
            f"UPDATE semantic_entities SET {', '.join(updates)} WHERE entity_id = ?",
            params,
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def patch_entity_properties(
        self, entity_id: str, properties_patch: dict[str, Any]
    ) -> dict[str, Any]:
        entity = self.get_entity(entity_id)
        if entity.get("status") != "published":
            raise self._state_error(
                f"Entity '{entity_id}' is not published (status={entity.get('status')}). "
                "Only published entities may be patched."
            )
        if not properties_patch or not isinstance(properties_patch, dict):
            raise self._validation_error("properties_patch must be a non-empty dict")
        current_props: dict[str, Any] = dict(entity.get("properties") or {})
        if "fields" in properties_patch and isinstance(properties_patch["fields"], dict):
            merged_fields = dict(current_props.get("fields") or {})
            for col, col_props in properties_patch["fields"].items():
                if isinstance(col_props, dict):
                    existing = dict(merged_fields.get(col) or {})
                    existing.update(col_props)
                    merged_fields[col] = existing
                else:
                    merged_fields[col] = col_props
            current_props = {key: value for key, value in current_props.items() if key != "fields"}
            current_props["fields"] = merged_fields
            remaining_patch = {
                key: value for key, value in properties_patch.items() if key != "fields"
            }
            current_props.update(remaining_patch)
        else:
            current_props.update(properties_patch)
        updated_at = now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET properties_json = ?, revision = revision + 1, updated_at = ? WHERE entity_id = ?",
            [json.dumps(current_props), updated_at, entity_id],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def publish_entity(self, entity_id: str) -> dict[str, Any]:
        self.get_entity(entity_id)
        updated_at = now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET status = 'published', revision = revision + 1, updated_at = ? WHERE entity_id = ?",
            [updated_at, entity_id],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

    def deprecate_entity(self, entity_id: str) -> dict[str, Any]:
        self.get_entity(entity_id)
        updated_at = now_iso()
        self.metadata.execute(
            "UPDATE semantic_entities SET status = 'deprecated', updated_at = ? WHERE entity_id = ?",
            [updated_at, entity_id],
        )
        entity = self.get_entity(entity_id)
        self._sync_entity_contract(entity)
        return entity

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
        created_at = now_iso()
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
                created_at,
                created_at,
            ],
        )
        metric = self.get_metric(metric_id)
        self._sync_metric_contract(metric)
        return metric

    def get_metric(self, metric_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE metric_id = ?",
            [metric_id],
        )
        if row is None:
            raise self._not_found(f"Unknown metric: {metric_id}")
        return self._row_to_metric(row)

    def list_metrics(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.metadata.query_rows(
                "SELECT * FROM semantic_metrics WHERE status = ? ORDER BY name",
                [status],
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM semantic_metrics ORDER BY name")
        return [self._row_to_metric(row) for row in rows]

    def update_metric(self, metric_id: str, **kwargs: Any) -> dict[str, Any]:
        metric = self.get_metric(metric_id)
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
        params.append(now_iso())
        params.append(metric_id)
        self.metadata.execute(
            f"UPDATE semantic_metrics SET {', '.join(updates)} WHERE metric_id = ?",
            params,
        )
        metric = self.get_metric(metric_id)
        self._sync_metric_contract(metric)
        return metric

    def publish_metric(self, metric_id: str) -> dict[str, Any]:
        self.get_metric(metric_id)
        updated_at = now_iso()
        self.metadata.execute(
            "UPDATE semantic_metrics SET status = 'published', revision = revision + 1, updated_at = ? WHERE metric_id = ?",
            [updated_at, metric_id],
        )
        metric = self.get_metric(metric_id)
        self._sync_metric_contract(metric)
        return metric

    def create_mapping(
        self,
        semantic_type: str,
        semantic_id: str,
        object_id: str,
        mapping_type: str,
        mapping_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mapping_id = f"map_{uuid4().hex[:12]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO legacy_semantic_mappings
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
                created_at,
                created_at,
            ],
        )
        result = self._get_mapping(mapping_id)
        assert result is not None
        return result

    def delete_mapping(self, mapping_id: str) -> None:
        existing = self._get_mapping(mapping_id)
        if existing is None:
            raise self._not_found(f"Unknown mapping: {mapping_id}")
        self.metadata.execute(
            "DELETE FROM legacy_semantic_mappings WHERE mapping_id = ?",
            [mapping_id],
        )

    def list_mappings(
        self,
        semantic_type: str | None = None,
        semantic_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM legacy_semantic_mappings WHERE 1=1"
        params: list[Any] = []
        if semantic_type:
            sql += " AND semantic_type = ?"
            params.append(semantic_type)
        if semantic_id:
            sql += " AND semantic_id = ?"
            params.append(semantic_id)
        sql += " ORDER BY created_at"
        rows = self.metadata.query_rows(sql, params)
        return [self._row_to_mapping(row) for row in rows]

    def _get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM legacy_semantic_mappings WHERE mapping_id = ?",
            [mapping_id],
        )
        if row is None:
            return None
        return self._row_to_mapping(row)
