from __future__ import annotations

import json
from typing import Any

from app.storage.metadata import MetadataStore


class PlannerContextProvider:
    """Build a lightweight planner context from persisted metadata."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        context: dict[str, Any] = {
            "metrics": self.metadata.query_rows(
                """
                SELECT name, display_name, status, dimensions_json
                FROM semantic_metrics
                ORDER BY name
                """
            ),
            "entities": self.metadata.query_rows(
                """
                SELECT name, display_name, status, keys_json
                FROM semantic_entities
                ORDER BY name
                """
            ),
        }
        for metric in context["metrics"]:
            metric["dimensions"] = json.loads(metric.pop("dimensions_json"))
        for entity in context["entities"]:
            entity["keys"] = json.loads(entity.pop("keys_json"))
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
