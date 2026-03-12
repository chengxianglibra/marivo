from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.storage.metadata import MetadataStore


@dataclass(slots=True)
class ResolvedMetric:
    name: str
    definition_sql: str | None = None
    dimensions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedEntity:
    name: str
    keys: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticResolver:
    """Resolve published semantic objects into lightweight runtime models."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def resolve_metric(self, metric_name: str) -> ResolvedMetric | None:
        row = self.metadata.query_one(
            """
            SELECT metric_id, name, display_name, description, definition_sql, dimensions_json, status, revision
            FROM semantic_metrics
            WHERE name = ? AND status = 'published'
            """,
            [metric_name],
        )
        if row is None:
            return None

        return ResolvedMetric(
            name=row["name"],
            definition_sql=row["definition_sql"],
            dimensions=json.loads(row["dimensions_json"]),
            metadata={
                "metric_id": row["metric_id"],
                "display_name": row["display_name"],
                "description": row["description"],
                "status": row["status"],
                "revision": row["revision"],
            },
        )

    def resolve_entity(self, entity_name: str) -> ResolvedEntity | None:
        row = self.metadata.query_one(
            """
            SELECT entity_id, name, display_name, description, keys_json, status, revision
            FROM semantic_entities
            WHERE name = ? AND status = 'published'
            """,
            [entity_name],
        )
        if row is None:
            return None

        return ResolvedEntity(
            name=row["name"],
            keys=json.loads(row["keys_json"]),
            metadata={
                "entity_id": row["entity_id"],
                "display_name": row["display_name"],
                "description": row["description"],
                "status": row["status"],
                "revision": row["revision"],
            },
        )
