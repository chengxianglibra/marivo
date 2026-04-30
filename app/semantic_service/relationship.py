from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from app.api.models.compatibility_profile import (
    EntityRelationshipCreateRequest,
    EntityRelationshipUpdateRequest,
)

from .common import SemanticServiceSupport, _catalog_metadata_json, now_iso


class EntityRelationshipService(SemanticServiceSupport):
    def create_relationship(self, payload: EntityRelationshipCreateRequest) -> dict[str, Any]:
        relationship_id = f"rel_{uuid4().hex[:24]}"
        created_at = now_iso()
        self.metadata.execute(
            """
            INSERT INTO semantic_entity_relationships (
                relationship_id, relationship_ref, display_name, description,
                left_entity_ref, right_entity_ref, key_alignment_json, time_alignment_json,
                cardinality, grain_compatibility_json,
                snapshot_effective_window_alignment_json, catalog_metadata_json,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
            """,
            [
                relationship_id,
                payload.relationship_ref,
                payload.display_name or payload.relationship_ref.removeprefix("relationship."),
                payload.description or "",
                payload.left_entity_ref,
                payload.right_entity_ref,
                json.dumps(payload.key_alignment.model_dump(mode="json")),
                (
                    json.dumps(payload.time_alignment.model_dump(mode="json"))
                    if payload.time_alignment is not None
                    else None
                ),
                payload.cardinality,
                (
                    json.dumps(payload.grain_compatibility.model_dump(mode="json"))
                    if payload.grain_compatibility is not None
                    else None
                ),
                (
                    json.dumps(payload.snapshot_effective_window_alignment.model_dump(mode="json"))
                    if payload.snapshot_effective_window_alignment is not None
                    else None
                ),
                _catalog_metadata_json(payload.catalog_metadata),
                created_at,
                created_at,
            ],
        )
        return self.get_relationship(relationship_id)

    def read_relationship(self, relationship_identifier: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_relationships WHERE relationship_id = ?",
            [relationship_identifier],
        )
        if row is None:
            row = self.metadata.query_one(
                "SELECT * FROM semantic_entity_relationships WHERE relationship_ref = ?",
                [relationship_identifier],
            )
        if row is None:
            raise self._not_found(f"Unknown entity relationship: {relationship_identifier}")
        return self._row_to_entity_relationship(row)

    def get_relationship(self, relationship_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM semantic_entity_relationships WHERE relationship_id = ?",
            [relationship_id],
        )
        if row is None:
            raise self._not_found(f"Unknown entity relationship: {relationship_id}")
        return self._row_to_entity_relationship(row)

    def list_relationships(
        self,
        *,
        status: str | None = None,
        lifecycle_status: str | None = None,
        readiness_status: str | None = None,
        detail: bool = False,
        left_entity_ref: str | None = None,
        right_entity_ref: str | None = None,
    ) -> dict[str, Any]:
        status = self._resolve_semantic_filters(status=status, lifecycle_status=lifecycle_status)
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if left_entity_ref is not None:
            clauses.append("left_entity_ref = ?")
            params.append(left_entity_ref)
        if right_entity_ref is not None:
            clauses.append("right_entity_ref = ?")
            params.append(right_entity_ref)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.metadata.query_rows(
            f"SELECT * FROM semantic_entity_relationships {where_sql} ORDER BY relationship_ref",
            params,
        )
        mode: Literal["list", "detail"] = "detail" if detail else "list"
        items = [
            item
            for row in rows
            if self._matches_readiness_filter(
                item := self._row_to_entity_relationship(row, mode=mode),
                readiness_status=readiness_status,
            )
        ]
        return {"items": items, "total": len(items)}

    def update_relationship(
        self, relationship_id: str, payload: EntityRelationshipUpdateRequest
    ) -> dict[str, Any]:
        current = self.get_relationship(relationship_id)
        self._require_lifecycle_action_status(
            action="activate",
            status=current["status"],
            object_label="Entity relationship",
            object_id=relationship_id,
        )
        updates: list[str] = []
        params: list[Any] = []
        for column_name, value in (
            ("display_name", payload.display_name),
            ("description", payload.description),
            ("cardinality", payload.cardinality),
        ):
            if value is not None:
                updates.append(f"{column_name} = ?")
                params.append(value)
        if payload.key_alignment is not None:
            updates.append("key_alignment_json = ?")
            params.append(json.dumps(payload.key_alignment.model_dump(mode="json")))
        if payload.time_alignment is not None:
            updates.append("time_alignment_json = ?")
            params.append(json.dumps(payload.time_alignment.model_dump(mode="json")))
        if payload.grain_compatibility is not None:
            updates.append("grain_compatibility_json = ?")
            params.append(json.dumps(payload.grain_compatibility.model_dump(mode="json")))
        if payload.snapshot_effective_window_alignment is not None:
            updates.append("snapshot_effective_window_alignment_json = ?")
            params.append(
                json.dumps(payload.snapshot_effective_window_alignment.model_dump(mode="json"))
            )
        if payload.catalog_metadata is not None:
            updates.append("catalog_metadata_json = ?")
            params.append(_catalog_metadata_json(payload.catalog_metadata))
        if not updates:
            return current
        updates.extend(["revision = revision + 1", "updated_at = ?"])
        params.extend([now_iso(), relationship_id])
        self.metadata.execute(
            f"UPDATE semantic_entity_relationships SET {', '.join(updates)} WHERE relationship_id = ?",
            params,
        )
        return self.get_relationship(relationship_id)

    def validate_relationship(self, relationship_id: str) -> dict[str, Any]:
        current = self.get_relationship(relationship_id)
        self._validate_record(
            object_id=relationship_id,
            object_label="Entity relationship",
            status=current["status"],
            reference_validator=lambda: self._validate_relationship_contract(
                current, require_published_entities=False
            ),
        )
        return self.get_relationship(relationship_id)

    def activate_relationship(self, relationship_id: str) -> dict[str, Any]:
        current = self.get_relationship(relationship_id)
        self._activate_record(
            table_name="semantic_entity_relationships",
            id_column="relationship_id",
            object_id=relationship_id,
            object_label="Entity relationship",
            status=current["status"],
            reference_validator=lambda: self._validate_relationship_contract(
                current, require_published_entities=True
            ),
        )
        return self.get_relationship(relationship_id)

    def publish_relationship(self, relationship_id: str) -> dict[str, Any]:
        return self.activate_relationship(relationship_id)

    def deprecate_relationship(self, relationship_id: str) -> dict[str, Any]:
        current = self.get_relationship(relationship_id)
        self._deprecate_record(
            table_name="semantic_entity_relationships",
            id_column="relationship_id",
            object_id=relationship_id,
            object_label="Entity relationship",
            status=current["status"],
        )
        return self.get_relationship(relationship_id)
