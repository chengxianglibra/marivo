from __future__ import annotations

import json
from typing import Any

from app.semantic_runtime.resolution import SemanticResolver
from app.storage.metadata import MetadataStore


class PlannerContextProvider:
    """Build a lightweight planner context from persisted metadata."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.semantic_resolver = SemanticResolver(metadata)

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        metric_rows = self.metadata.query_rows(
            """
            SELECT name
            FROM semantic_metrics
            WHERE status = 'published'
            ORDER BY name
            """
        )
        entity_rows = self.metadata.query_rows(
            """
            SELECT name
            FROM semantic_entities
            WHERE status = 'published'
            ORDER BY name
            """
        )
        context: dict[str, Any] = {
            "metrics": [self._metric_context(metric_name=row["name"]) for row in metric_rows],
            "entities": [self._entity_context(entity_name=row["name"]) for row in entity_rows],
        }
        if session_id is not None:
            session = self.metadata.query_one(
                """
                SELECT session_id, goal, status, constraints_json, budget_json, policy_json
                FROM sessions
                WHERE session_id = ?
                """,
                [session_id],
            )
            if session is not None:
                context["session"] = {
                    "session_id": session["session_id"],
                    "goal": session["goal"],
                    "status": session["status"],
                    "constraints": json.loads(session["constraints_json"]),
                    "budget": json.loads(session["budget_json"]),
                    "policy": json.loads(session["policy_json"]),
                }
        return context

    def _metric_context(self, metric_name: str) -> dict[str, Any]:
        resolved = self.semantic_resolver.resolve_metric(metric_name)
        if resolved is None:
            return {"metric_ref": f"metric.{metric_name}", "display_name": metric_name}
        return {
            "header": {
                "metric_ref": resolved.metric_ref,
                "display_name": resolved.display_name,
                "description": resolved.description,
                "metric_contract_version": resolved.metric_contract_version,
            },
            "identity": {
                "metric_family": resolved.metric_family,
                "population_subject_ref": resolved.population_subject_ref,
                "observed_entity_ref": resolved.observed_entity_ref,
                "observation_grain_ref": resolved.observation_grain_ref,
                "sample_kind": resolved.sample_kind,
                "value_semantics": resolved.value_semantics,
                "aggregation_scope": resolved.aggregation_scope,
                "primary_time_ref": resolved.primary_time_ref,
                "additivity": resolved.additivity,
            },
            "family_payload": resolved.family_payload,
            "metadata": {
                **resolved.metadata,
                "name": resolved.name,
            },
            "legacy": {
                "dimensions": list(resolved.dimensions),
                "grain": resolved.grain,
                "measure_type": resolved.measure_type,
                "allowed_dimensions": list(resolved.allowed_dimensions),
            },
        }

    def _entity_context(self, entity_name: str) -> dict[str, Any]:
        resolved = self.semantic_resolver.resolve_entity(entity_name)
        if resolved is None:
            return {"entity_ref": f"entity.{entity_name}", "display_name": entity_name}
        return {
            "header": {
                "entity_ref": resolved.entity_ref,
                "display_name": resolved.display_name,
                "description": resolved.description,
                "entity_contract_version": resolved.entity_contract_version,
            },
            "identity": {
                "key_refs": list(resolved.key_refs),
                "uniqueness_scope": resolved.uniqueness_scope,
                "id_stability": resolved.id_stability,
                "nullable_key_policy": resolved.nullable_key_policy,
            },
            "hierarchy": {
                "parent_entity_ref": resolved.parent_entity_ref,
                "cardinality_to_parent": resolved.cardinality_to_parent,
                "ownership_semantics": resolved.ownership_semantics,
            },
            "stable_descriptors": list(resolved.stable_descriptors),
            "primary_time_ref": resolved.primary_time_ref,
            "metadata": {
                **resolved.metadata,
                "name": resolved.name,
            },
            "legacy": {
                "keys": list(resolved.keys),
                "level": resolved.level,
                "join_constraints": dict(resolved.join_constraints),
                "upstream_dependencies": list(resolved.upstream_dependencies),
                "lineage": list(resolved.lineage),
                "quality_expectations": dict(resolved.quality_expectations),
            },
        }
