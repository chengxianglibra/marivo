from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionRepository:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
        if row is None:
            return None
        return dict(row)


class JobRepository:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, job_id: str, session_id: str, job_type: str, payload: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO jobs (job_id, session_id, job_type, payload_json, status, submitted_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            [job_id, session_id, job_type, json.dumps(payload), _now_iso()],
        )

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM jobs WHERE job_id = ?", [job_id])
        if row is None:
            return None
        return self._deserialize_job(row)

    def list(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY submitted_at DESC"
        return [self._deserialize_job(row) for row in self.metadata.query_rows(query, params)]

    def mark_running(self, job_id: str) -> None:
        self.metadata.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            [_now_iso(), job_id],
        )

    def mark_completed(self, job_id: str, result: Any) -> None:
        self.metadata.execute(
            "UPDATE jobs SET status = 'completed', result_json = ?, completed_at = ? WHERE job_id = ?",
            [json.dumps(result, default=str), _now_iso(), job_id],
        )

    def mark_failed(self, job_id: str, error_message: str) -> None:
        self.metadata.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, completed_at = ? WHERE job_id = ?",
            [error_message, _now_iso(), job_id],
        )

    def mark_cancelled(self, job_id: str) -> None:
        self.metadata.execute(
            "UPDATE jobs SET status = 'cancelled', completed_at = ? WHERE job_id = ?",
            [_now_iso(), job_id],
        )

    def _deserialize_job(self, row: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": row["job_id"],
            "session_id": row["session_id"],
            "job_type": row["job_type"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "submitted_at": row["submitted_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
        }
        if row.get("result_json"):
            result["result"] = json.loads(row["result_json"])
        if row.get("error_message"):
            result["error_message"] = row["error_message"]
        return result
