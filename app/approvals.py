"""Approval workflow: approval requests for high-risk recommendations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.storage.metadata import MetadataStore


class ApprovalService:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def request_approval(self, session_id: str, rec_id: str) -> dict[str, Any]:
        # Verify recommendation exists
        rec = self.metadata.query_one("SELECT rec_id FROM recommendations WHERE rec_id = ?", [rec_id])
        if rec is None:
            raise KeyError(f"Unknown recommendation: {rec_id}")

        # Check for existing pending request
        existing = self.metadata.query_one(
            "SELECT request_id FROM approval_requests WHERE rec_id = ? AND status = 'pending'",
            [rec_id],
        )
        if existing:
            return self.get_request(existing["request_id"])

        request_id = f"apr_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            """
            INSERT INTO approval_requests (request_id, session_id, rec_id, status, reason, reviewer, submitted_at)
            VALUES (?, ?, ?, 'pending', '', '', ?)
            """,
            [request_id, session_id, rec_id, now],
        )
        return self.get_request(request_id)

    def list_requests(
        self,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM approval_requests WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY submitted_at DESC"
        return [self._deserialize(r) for r in self.metadata.query_rows(query, params)]

    def get_request(self, request_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM approval_requests WHERE request_id = ?", [request_id])
        if row is None:
            raise KeyError(f"Unknown approval request: {request_id}")
        return self._deserialize(row)

    def approve(self, request_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        request = self.get_request(request_id)
        if request["status"] != "pending":
            raise ValueError(f"Cannot approve request in '{request['status']}' status")
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            "UPDATE approval_requests SET status = 'approved', reviewer = ?, reason = ?, decided_at = ? WHERE request_id = ?",
            [reviewer, reason, now, request_id],
        )
        return self.get_request(request_id)

    def reject(self, request_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        request = self.get_request(request_id)
        if request["status"] != "pending":
            raise ValueError(f"Cannot reject request in '{request['status']}' status")
        now = datetime.now(timezone.utc).isoformat()
        self.metadata.execute(
            "UPDATE approval_requests SET status = 'rejected', reviewer = ?, reason = ?, decided_at = ? WHERE request_id = ?",
            [reviewer, reason, now, request_id],
        )
        return self.get_request(request_id)

    def auto_flag_recommendations(
        self,
        session_id: str,
        risk_threshold: str = "P0",
    ) -> list[dict[str, Any]]:
        """Auto-create approval requests for recommendations at or above risk threshold."""
        risk_levels = ["P0", "P1", "P2", "P3"]
        try:
            threshold_idx = risk_levels.index(risk_threshold)
        except ValueError:
            threshold_idx = 0
        flaggable = risk_levels[: threshold_idx + 1]

        recs = self.metadata.query_rows(
            "SELECT rec_id, risk FROM recommendations WHERE session_id = ? ORDER BY created_at",
            [session_id],
        )
        created: list[dict[str, Any]] = []
        for rec in recs:
            if rec["risk"] in flaggable:
                result = self.request_approval(session_id, rec["rec_id"])
                created.append(result)
        return created

    def _deserialize(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": row["request_id"],
            "session_id": row["session_id"],
            "rec_id": row["rec_id"],
            "status": row["status"],
            "reason": row["reason"],
            "reviewer": row["reviewer"],
            "submitted_at": row["submitted_at"],
            "decided_at": row.get("decided_at"),
        }
