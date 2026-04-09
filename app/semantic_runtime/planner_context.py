from __future__ import annotations

import json
import logging
from typing import Any

from app.semantic_runtime.errors import (
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.resolution import SemanticResolver
from app.storage.metadata import MetadataStore

logger = logging.getLogger(__name__)

_PLANNER_CONTEXT_TABLES: dict[str, tuple[str, str]] = {
    "metric": ("semantic_metric_contracts", "metric_ref"),
    "entity": ("semantic_entity_contracts", "entity_ref"),
}


class PlannerContextProvider:
    """Build a lightweight planner context from persisted metadata."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.semantic_resolver = SemanticResolver(metadata)

    def build_planner_context(self, session_id: str | None = None) -> dict[str, Any]:
        context: dict[str, Any] = {
            "metrics": self._semantic_contexts("metric"),
            "entities": self._semantic_contexts("entity"),
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

    def _semantic_contexts(self, object_kind: str) -> list[dict[str, Any]]:
        if object_kind not in _PLANNER_CONTEXT_TABLES:
            raise ValueError(f"Unsupported object kind for planner context: {object_kind!r}")
        table_name, ref_column = _PLANNER_CONTEXT_TABLES[object_kind]
        ref_rows = self.metadata.query_rows(
            f"""
            SELECT {ref_column} AS semantic_ref
            FROM {table_name}
            WHERE status = 'published'
            ORDER BY {ref_column}
            """
        )
        contexts: list[dict[str, Any]] = []
        for row in ref_rows:
            try:
                resolved = self.semantic_resolver.resolve_ref(str(row["semantic_ref"]))
                contexts.append(dict(resolved.semantic_object))
            except (
                SemanticRuntimeInvalidRefError,
                SemanticRuntimeNotFoundError,
                SemanticRuntimeUnpublishedError,
            ) as e:
                logger.warning("Skipping invalid semantic ref %s: %s", row["semantic_ref"], e)
        return contexts
