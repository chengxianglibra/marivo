from __future__ import annotations

import json
from typing import Any

from app.semantic_runtime.semantic_metadata import entity_runtime_metadata, metric_runtime_metadata
from app.storage.metadata import MetadataStore


class PlannerContextProvider:
    """Build a lightweight planner context from persisted metadata."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        context: dict[str, Any] = {
            "metrics": self.metadata.query_rows(
                """
                SELECT
                    name, display_name, status, dimensions_json, grain, measure_type,
                    allowed_dimensions_json, lineage_json, quality_expectations_json,
                    properties_json
                FROM semantic_metrics
                WHERE status = 'published'
                ORDER BY name
                """
            ),
            "entities": self.metadata.query_rows(
                """
                SELECT
                    name, display_name, status, keys_json, level, join_constraints_json,
                    upstream_dependencies_json, lineage_json, quality_expectations_json,
                    properties_json
                FROM semantic_entities
                WHERE status = 'published'
                ORDER BY name
                """
            ),
        }
        for metric in context["metrics"]:
            metric["dimensions"] = json.loads(metric.pop("dimensions_json"))
            metric["properties"] = json.loads(metric.pop("properties_json"))
            metric.update(
                metric_runtime_metadata(
                    grain=metric["grain"],
                    measure_type=metric["measure_type"],
                    allowed_dimensions_json=metric.pop("allowed_dimensions_json"),
                    lineage_json=metric.pop("lineage_json"),
                    quality_expectations_json=metric.pop("quality_expectations_json"),
                    dimensions=metric["dimensions"],
                )
            )
        for entity in context["entities"]:
            entity["keys"] = json.loads(entity.pop("keys_json"))
            entity["properties"] = json.loads(entity.pop("properties_json"))
            entity.update(
                entity_runtime_metadata(
                    level=entity["level"],
                    join_constraints_json=entity.pop("join_constraints_json"),
                    upstream_dependencies_json=entity.pop("upstream_dependencies_json"),
                    lineage_json=entity.pop("lineage_json"),
                    quality_expectations_json=entity.pop("quality_expectations_json"),
                )
            )
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
