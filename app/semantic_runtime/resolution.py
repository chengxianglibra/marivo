from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.storage.metadata import MetadataStore


@dataclass(slots=True)
class ResolvedMetric:
    name: str
    metric_ref: str = ""
    display_name: str = ""
    description: str = ""
    metric_family: str = ""
    population_subject_ref: str | None = None
    observed_entity_ref: str = ""
    observation_grain_ref: str = ""
    sample_kind: str = ""
    value_semantics: str = ""
    aggregation_scope: str | None = None
    primary_time_ref: str | None = None
    additivity: str = ""
    metric_contract_version: str = ""
    family_payload: dict[str, Any] = field(default_factory=dict)
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
    entity_ref: str = ""
    display_name: str = ""
    description: str = ""
    entity_contract_version: str = ""
    key_refs: list[str] = field(default_factory=list)
    uniqueness_scope: str = ""
    id_stability: str = ""
    nullable_key_policy: str = ""
    parent_entity_ref: str | None = None
    cardinality_to_parent: str | None = None
    ownership_semantics: str | None = None
    primary_time_ref: str | None = None
    stable_descriptors: list[dict[str, Any]] = field(default_factory=list)
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
        legacy_row = self.metadata.query_one(
            """
            SELECT
                metric_id, name, display_name, description, definition_sql, dimensions_json,
                entity_id, grain, measure_type, allowed_dimensions_json, lineage_json,
                quality_expectations_json, properties_json, desired_direction, status, revision,
                created_at, updated_at
            FROM semantic_metrics
            WHERE name = ? AND status = 'published'
            """,
            [metric_name],
        )
        if legacy_row is None:
            return None

        contract_row = self.metadata.query_one(
            """
            SELECT
                metric_contract_id, metric_ref, display_name, description, metric_family,
                population_subject_ref, observed_entity_ref, observation_grain_ref,
                sample_kind, value_semantics, aggregation_scope, primary_time_ref,
                additivity, metric_contract_version, family_payload_json, status, revision,
                created_at, updated_at
            FROM semantic_metric_contracts
            WHERE metric_contract_id = ? AND status = 'published'
            """,
            [legacy_row["metric_id"]],
        )
        if contract_row is None:
            return None

        family_payload = json.loads(contract_row["family_payload_json"] or "{}")
        dimensions = list(
            family_payload.get("dimensions") or json.loads(legacy_row["dimensions_json"])
        )
        properties = json.loads(legacy_row["properties_json"])

        return ResolvedMetric(
            name=legacy_row["name"],
            metric_ref=str(contract_row["metric_ref"]),
            display_name=str(contract_row["display_name"]),
            description=str(contract_row["description"]),
            metric_family=str(contract_row["metric_family"]),
            population_subject_ref=contract_row["population_subject_ref"],
            observed_entity_ref=str(contract_row["observed_entity_ref"]),
            observation_grain_ref=str(contract_row["observation_grain_ref"]),
            sample_kind=str(contract_row["sample_kind"]),
            value_semantics=str(contract_row["value_semantics"]),
            aggregation_scope=contract_row["aggregation_scope"],
            primary_time_ref=contract_row["primary_time_ref"],
            additivity=str(contract_row["additivity"]),
            metric_contract_version=str(contract_row["metric_contract_version"]),
            family_payload=family_payload,
            definition_sql=family_payload.get("definition_sql", legacy_row["definition_sql"]),
            dimensions=dimensions,
            grain=family_payload.get("grain", legacy_row["grain"]),
            measure_type=family_payload.get("measure_type", legacy_row["measure_type"]),
            allowed_dimensions=list(
                family_payload.get("allowed_dimensions")
                or json.loads(legacy_row["allowed_dimensions_json"] or "[]")
            ),
            lineage=list(json.loads(legacy_row["lineage_json"] or "[]")),
            quality_expectations=dict(json.loads(legacy_row["quality_expectations_json"] or "{}")),
            desired_direction=family_payload.get(
                "desired_direction", legacy_row.get("desired_direction")
            ),
            metadata={
                "metric_id": legacy_row["metric_id"],
                "display_name": contract_row["display_name"],
                "description": contract_row["description"],
                "status": contract_row["status"],
                "revision": contract_row["revision"],
                "properties": properties,
                "created_at": contract_row["created_at"],
                "updated_at": contract_row["updated_at"],
            },
        )

    def resolve_entity(self, entity_name: str) -> ResolvedEntity | None:
        legacy_row = self.metadata.query_one(
            """
            SELECT
                entity_id, name, display_name, description, keys_json, level,
                join_constraints_json, upstream_dependencies_json, lineage_json,
                quality_expectations_json, properties_json, status, revision,
                created_at, updated_at
            FROM semantic_entities
            WHERE name = ? AND status = 'published'
            """,
            [entity_name],
        )
        if legacy_row is None:
            return None

        contract_row = self.metadata.query_one(
            """
            SELECT
                entity_contract_id, entity_ref, display_name, description,
                entity_contract_version, uniqueness_scope, id_stability,
                nullable_key_policy, parent_entity_ref, cardinality_to_parent,
                ownership_semantics, primary_time_ref, status, revision,
                created_at, updated_at
            FROM semantic_entity_contracts
            WHERE entity_contract_id = ? AND status = 'published'
            """,
            [legacy_row["entity_id"]],
        )
        if contract_row is None:
            return None

        key_rows = self.metadata.query_rows(
            """
            SELECT key_ref
            FROM semantic_entity_key_refs
            WHERE entity_contract_id = ?
            ORDER BY position
            """,
            [legacy_row["entity_id"]],
        )
        descriptor_rows = self.metadata.query_rows(
            """
            SELECT dimension_ref, cardinality
            FROM semantic_entity_stable_descriptors
            WHERE entity_contract_id = ?
            ORDER BY position
            """,
            [legacy_row["entity_id"]],
        )
        properties = json.loads(legacy_row["properties_json"])
        upstream_dependencies = list(json.loads(legacy_row["upstream_dependencies_json"] or "[]"))
        join_constraints = dict(json.loads(legacy_row["join_constraints_json"] or "{}"))
        lineage = list(json.loads(legacy_row["lineage_json"] or "[]"))
        quality_expectations = dict(json.loads(legacy_row["quality_expectations_json"] or "{}"))

        return ResolvedEntity(
            name=legacy_row["name"],
            entity_ref=str(contract_row["entity_ref"]),
            display_name=str(contract_row["display_name"]),
            description=str(contract_row["description"]),
            entity_contract_version=str(contract_row["entity_contract_version"]),
            key_refs=[str(row["key_ref"]) for row in key_rows],
            uniqueness_scope=str(contract_row["uniqueness_scope"]),
            id_stability=str(contract_row["id_stability"]),
            nullable_key_policy=str(contract_row["nullable_key_policy"]),
            parent_entity_ref=contract_row["parent_entity_ref"],
            cardinality_to_parent=contract_row["cardinality_to_parent"],
            ownership_semantics=contract_row["ownership_semantics"],
            primary_time_ref=contract_row["primary_time_ref"],
            stable_descriptors=[
                {
                    "dimension_ref": row["dimension_ref"],
                    "cardinality": row["cardinality"],
                }
                for row in descriptor_rows
            ],
            keys=list(json.loads(legacy_row["keys_json"])),
            level=legacy_row["level"],
            join_constraints=join_constraints,
            upstream_dependencies=upstream_dependencies,
            lineage=lineage,
            quality_expectations=quality_expectations,
            metadata={
                "entity_id": legacy_row["entity_id"],
                "display_name": contract_row["display_name"],
                "description": contract_row["description"],
                "properties": properties,
                "status": contract_row["status"],
                "revision": contract_row["revision"],
                "created_at": contract_row["created_at"],
                "updated_at": contract_row["updated_at"],
            },
        )
