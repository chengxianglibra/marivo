from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.storage.metadata import MetadataStore


class SessionManager:
    """Own session CRUD so the orchestration service can slim down incrementally."""

    def __init__(self, metadata_store: MetadataStore) -> None:
        self.metadata = metadata_store

    def create_session(
        self,
        goal: str,
        constraints: dict[str, Any],
        budget: dict[str, Any],
        policy: dict[str, Any],
        raw_filter: str | None = None,
    ) -> dict[str, Any]:
        session_id = f"sess_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status, raw_filter)
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
            [
                session_id,
                goal,
                self._dump(constraints),
                self._dump(budget),
                self._dump(policy),
                raw_filter,
            ],
        )
        return {
            "session_id": session_id,
            "goal": goal,
            "status": "open",
            "constraints": constraints,
            "budget": budget,
            "policy": policy,
            "raw_filter": raw_filter,
        }

    def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT session_id, goal, status, constraints_json, budget_json, policy_json, raw_filter, created_at FROM sessions"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        rows = self.metadata.query_rows(sql, params)
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            """
            SELECT session_id, goal, status, constraints_json, budget_json, policy_json, raw_filter, created_at
            FROM sessions
            WHERE session_id = ?
            """,
            [session_id],
        )
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return self._session_from_row(row)

    def assert_session_exists(self, session_id: str) -> None:
        row = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if row is None or row["cnt"] == 0:
            raise KeyError(f"Unknown session: {session_id}")

    def _session_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "goal": row["goal"],
            "status": row["status"],
            "constraints": json.loads(row["constraints_json"]),
            "budget": json.loads(row["budget_json"]),
            "policy": json.loads(row["policy_json"]),
            "raw_filter": row.get("raw_filter"),
            "created_at": row["created_at"],
        }

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)
