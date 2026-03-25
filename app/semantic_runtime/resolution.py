from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.semantic_runtime.semantic_metadata import entity_runtime_metadata, metric_runtime_metadata
from app.storage.metadata import MetadataStore


@dataclass(slots=True)
class ResolvedMetric:
    name: str
    definition_sql: str | None = None
    dimensions: list[str] = field(default_factory=list)
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    desired_direction: str | None = None


@dataclass(slots=True)
class ResolvedEntity:
    name: str
    keys: list[str] = field(default_factory=list)
    level: str | None = None
    join_constraints: dict[str, Any] = field(default_factory=dict)
    upstream_dependencies: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticResolver:
    """Resolve published semantic objects into lightweight runtime models."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def resolve_metric(self, metric_name: str) -> ResolvedMetric | None:
        row = self.metadata.query_one(
            """
            SELECT
                metric_id, name, display_name, description, definition_sql, dimensions_json,
                grain, measure_type, allowed_dimensions_json, lineage_json,
                quality_expectations_json, properties_json, desired_direction, status, revision
            FROM semantic_metrics
            WHERE name = ? AND status = 'published'
            """,
            [metric_name],
        )
        if row is None:
            return None

        dimensions = json.loads(row["dimensions_json"])
        properties = json.loads(row["properties_json"])
        runtime_metadata = metric_runtime_metadata(
            grain=row["grain"],
            measure_type=row["measure_type"],
            allowed_dimensions_json=row["allowed_dimensions_json"],
            lineage_json=row["lineage_json"],
            quality_expectations_json=row["quality_expectations_json"],
            dimensions=dimensions,
        )

        return ResolvedMetric(
            name=row["name"],
            definition_sql=row["definition_sql"],
            dimensions=dimensions,
            grain=runtime_metadata["grain"],
            measure_type=runtime_metadata["measure_type"],
            allowed_dimensions=runtime_metadata["allowed_dimensions"],
            lineage=runtime_metadata["lineage"],
            quality_expectations=runtime_metadata["quality_expectations"],
            desired_direction=row.get("desired_direction"),
            metadata={
                "metric_id": row["metric_id"],
                "display_name": row["display_name"],
                "description": row["description"],
                "properties": properties,
                "status": row["status"],
                "revision": row["revision"],
            },
        )

    def resolve_entity(self, entity_name: str) -> ResolvedEntity | None:
        row = self.metadata.query_one(
            """
            SELECT
                entity_id, name, display_name, description, keys_json, level,
                join_constraints_json, upstream_dependencies_json, lineage_json,
                quality_expectations_json, properties_json, status, revision
            FROM semantic_entities
            WHERE name = ? AND status = 'published'
            """,
            [entity_name],
        )
        if row is None:
            return None

        properties = json.loads(row["properties_json"])
        runtime_metadata = entity_runtime_metadata(
            level=row["level"],
            join_constraints_json=row["join_constraints_json"],
            upstream_dependencies_json=row["upstream_dependencies_json"],
            lineage_json=row["lineage_json"],
            quality_expectations_json=row["quality_expectations_json"],
        )

        return ResolvedEntity(
            name=row["name"],
            keys=json.loads(row["keys_json"]),
            level=runtime_metadata["level"],
            join_constraints=runtime_metadata["join_constraints"],
            upstream_dependencies=runtime_metadata["upstream_dependencies"],
            lineage=runtime_metadata["lineage"],
            quality_expectations=runtime_metadata["quality_expectations"],
            metadata={
                "entity_id": row["entity_id"],
                "display_name": row["display_name"],
                "description": row["description"],
                "properties": properties,
                "status": row["status"],
                "revision": row["revision"],
            },
        )
