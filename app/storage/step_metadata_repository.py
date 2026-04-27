from __future__ import annotations

import json
from typing import Any

from app.storage.metadata import MetadataStore


class StepMetadataRepository:
    """Repository for typed semantic metadata associated with executed steps."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def upsert(
        self,
        *,
        step_id: str,
        metadata_kind: str,
        semantic_snapshot: dict[str, Any],
    ) -> None:
        payload = json.dumps(semantic_snapshot)
        self.metadata.upsert_by_key(
            "step_metadata",
            ["step_id", "metadata_kind", "semantic_snapshot_json"],
            [step_id, metadata_kind, payload],
            ["step_id"],
            ["metadata_kind", "semantic_snapshot_json"],
            updated_at_column="updated_at",
        )

    def get(self, step_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            """
            SELECT step_id, metadata_kind, semantic_snapshot_json, created_at, updated_at
            FROM step_metadata
            WHERE step_id = ?
            """,
            [step_id],
        )
        if row is None:
            return None
        return {
            "step_id": row["step_id"],
            "metadata_kind": row["metadata_kind"],
            "semantic_snapshot": json.loads(row["semantic_snapshot_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
