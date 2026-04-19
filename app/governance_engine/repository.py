from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.registry.common import now_iso
from app.storage.metadata import MetadataStore


class GovernanceRepository:
    """Persistence boundary for policy, quality, approval, and audit state."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create_policy(
        self,
        name: str,
        policy_type: str,
        definition: dict[str, Any],
        scope: dict[str, Any],
    ) -> dict[str, Any]:
        policy_id = f"pol_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO policies (policy_id, name, policy_type, definition_json, scope_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [policy_id, name, policy_type, json.dumps(definition), json.dumps(scope), now, now],
        )
        return self.get_policy(policy_id)

    def list_policies(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        if enabled_only:
            rows = self.metadata.query_rows(
                "SELECT * FROM policies WHERE enabled = 1 ORDER BY created_at"
            )
        else:
            rows = self.metadata.query_rows("SELECT * FROM policies ORDER BY created_at")
        return [self._deserialize_policy(row) for row in rows]

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM policies WHERE policy_id = ?", [policy_id])
        if row is None:
            raise KeyError(f"Unknown policy: {policy_id}")
        return self._deserialize_policy(row)

    def update_policy(
        self,
        policy_id: str,
        enabled: bool | None = None,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_policy(policy_id)
        now = now_iso()
        if enabled is not None:
            self.metadata.execute(
                "UPDATE policies SET enabled = ?, updated_at = ? WHERE policy_id = ?",
                [1 if enabled else 0, now, policy_id],
            )
        if definition is not None:
            self.metadata.execute(
                "UPDATE policies SET definition_json = ?, updated_at = ? WHERE policy_id = ?",
                [json.dumps(definition), now, policy_id],
            )
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str) -> dict[str, str]:
        self.get_policy(policy_id)
        self.metadata.execute("DELETE FROM policies WHERE policy_id = ?", [policy_id])
        return {"status": "deleted", "policy_id": policy_id}

    def create_quality_rule(
        self,
        name: str,
        rule_type: str,
        table_name: str,
        threshold: dict[str, Any],
        severity: str,
    ) -> dict[str, Any]:
        rule_id = f"qr_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO quality_rules (rule_id, name, rule_type, table_name, threshold_json, severity, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            [rule_id, name, rule_type, table_name, json.dumps(threshold), severity, now, now],
        )
        return self.get_quality_rule(rule_id)

    def get_quality_rule(self, rule_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM quality_rules WHERE rule_id = ?", [rule_id])
        if row is None:
            raise KeyError(f"Unknown quality rule: {rule_id}")
        return self._deserialize_rule(row)

    def list_quality_rules(self, table_name: str | None = None) -> list[dict[str, Any]]:
        if table_name:
            rows = self.metadata.query_rows(
                "SELECT * FROM quality_rules WHERE enabled = 1 AND table_name = ? ORDER BY created_at",
                [table_name],
            )
        else:
            rows = self.metadata.query_rows(
                "SELECT * FROM quality_rules WHERE enabled = 1 ORDER BY created_at"
            )
        return [self._deserialize_rule(row) for row in rows]

    def delete_quality_rule(self, rule_id: str) -> dict[str, str]:
        self.get_quality_rule(rule_id)
        self.metadata.execute("DELETE FROM quality_rules WHERE rule_id = ?", [rule_id])
        return {"status": "deleted", "rule_id": rule_id}

    def get_recommendation(self, rec_id: str) -> dict[str, Any]:
        row = self.metadata.query_one("SELECT * FROM recommendations WHERE rec_id = ?", [rec_id])
        if row is None:
            raise KeyError(f"Unknown recommendation: {rec_id}")
        return dict(row)

    def list_session_recommendations(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT rec_id, risk, type FROM recommendations WHERE session_id = ? ORDER BY created_at",
            [session_id],
        )
        return [dict(row) for row in rows]

    def find_pending_approval_request(self, rec_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM approval_requests WHERE rec_id = ? AND status = 'pending'",
            [rec_id],
        )
        return self._deserialize_request(row) if row is not None else None

    def create_approval_request(self, session_id: str, rec_id: str) -> dict[str, Any]:
        request_id = f"apr_{uuid4().hex[:12]}"
        now = now_iso()
        self.metadata.execute(
            """
            INSERT INTO approval_requests (request_id, session_id, rec_id, status, reason, reviewer, submitted_at)
            VALUES (?, ?, ?, 'pending', '', '', ?)
            """,
            [request_id, session_id, rec_id, now],
        )
        return self.get_approval_request(request_id)

    def list_approval_requests(
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
        rows = self.metadata.query_rows(query, params)
        return [self._deserialize_request(row) for row in rows]

    def get_approval_request(self, request_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            "SELECT * FROM approval_requests WHERE request_id = ?", [request_id]
        )
        if row is None:
            raise KeyError(f"Unknown approval request: {request_id}")
        return self._deserialize_request(row)

    def set_approval_decision(
        self,
        request_id: str,
        status: str,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any]:
        _ = self.get_approval_request(request_id)  # Validate request exists
        now = now_iso()
        self.metadata.execute(
            "UPDATE approval_requests SET status = ?, reviewer = ?, reason = ?, decided_at = ? WHERE request_id = ?",
            [status, reviewer, reason, now, request_id],
        )
        return self.get_approval_request(request_id)

    def record_event(
        self,
        *,
        subject_type: str,
        event_type: str,
        session_id: str | None = None,
        subject_id: str | None = None,
        actor: str = "system",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = f"govevt_{uuid4().hex[:12]}"
        created_at = now_iso()
        payload = detail or {}
        self.metadata.execute(
            """
            INSERT INTO governance_events (event_id, session_id, subject_type, subject_id, event_type, actor, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event_id,
                session_id,
                subject_type,
                subject_id,
                event_type,
                actor,
                json.dumps(payload),
                created_at,
            ],
        )
        return {
            "event_id": event_id,
            "session_id": session_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "event_type": event_type,
            "actor": actor,
            "detail": payload,
            "created_at": created_at,
        }

    def list_events(
        self,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM governance_events WHERE 1=1"
        params: list[Any] = []
        if subject_type:
            query += " AND subject_type = ?"
            params.append(subject_type)
        if subject_id:
            query += " AND subject_id = ?"
            params.append(subject_id)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY created_at"
        rows = self.metadata.query_rows(query, params)
        return [self._deserialize_event(row) for row in rows]

    def _deserialize_policy(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_id": row["policy_id"],
            "name": row["name"],
            "policy_type": row["policy_type"],
            "definition": json.loads(row["definition_json"]),
            "scope": json.loads(row["scope_json"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _deserialize_rule(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "rule_id": row["rule_id"],
            "name": row["name"],
            "rule_type": row["rule_type"],
            "table_name": row["table_name"],
            "threshold": json.loads(row["threshold_json"]),
            "severity": row["severity"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _deserialize_request(self, row: dict[str, Any]) -> dict[str, Any]:
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

    def _deserialize_event(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "session_id": row["session_id"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "detail": json.loads(row["detail_json"]),
            "created_at": row["created_at"],
        }
