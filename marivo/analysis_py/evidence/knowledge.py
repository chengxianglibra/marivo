"""SessionKnowledge projection: facts + next_steps over judgment.db."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marivo.analysis_py.evidence.identity import canonical_json
from marivo.analysis_py.evidence.types import (
    ChangeFact,
    EvidenceCompleteness,
    FactKind,
    Subject,
)
from marivo.analysis_py.followups import FollowupAction


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _completeness(conn: sqlite3.Connection, session_id: str) -> EvidenceCompleteness:
    rows = conn.execute(
        "SELECT evidence_status, count(*) FROM artifacts "
        "WHERE session_id=? GROUP BY evidence_status",
        (session_id,),
    ).fetchall()
    statuses = {row[0]: row[1] for row in rows}
    if not statuses:
        return "complete"
    if "unavailable" in statuses:
        return "unavailable"
    if "partial" in statuses:
        return "partial"
    return "complete"


def _change_facts(conn: sqlite3.Connection, session_id: str) -> list[ChangeFact]:
    rows = conn.execute(
        """
        SELECT p.proposition_id, p.subject_key, p.payload AS prop_payload,
               p.seed_finding_refs,
               a.snapshot_id, a.status, a.confidence, a.confidence_basis,
               f.payload AS finding_payload, f.subject_payload, f.artifact_id
        FROM propositions p
        LEFT JOIN assessment_snapshots a
          ON a.proposition_id = p.proposition_id AND a.is_latest = 1
        LEFT JOIN findings f
          ON f.finding_id = json_extract(p.seed_finding_refs, '$[0]')
        WHERE p.session_id = ? AND p.proposition_type = 'change'
        ORDER BY p.created_at_us
        """,
        (session_id,),
    ).fetchall()
    facts: list[ChangeFact] = []
    for row in rows:
        prop_payload = json.loads(row["prop_payload"]) if row["prop_payload"] else {}
        finding_payload = (
            json.loads(row["finding_payload"]) if row["finding_payload"] else {}
        )
        subject_dict = (
            json.loads(row["subject_payload"]) if row["subject_payload"] else {}
        )
        subject = Subject.model_validate(subject_dict)
        facts.append(
            ChangeFact(
                id=row["proposition_id"],
                kind="change",
                subject=subject,
                window=None,
                status=row["status"] or "pending",
                confidence=row["confidence"],
                confidence_basis=row["confidence_basis"] or "",
                source_refs=[row["artifact_id"]] if row["artifact_id"] else [],
                latest_assessment_id=row["snapshot_id"] or "",
                direction=finding_payload.get("direction", "undefined"),
                magnitude=finding_payload.get("magnitude"),
                comparison_window=None,
                comparison_basis=prop_payload.get(
                    "comparison_basis", "left_vs_right"
                ),
                dimension_keys=prop_payload.get("dimension_keys"),
            )
        )
    return facts


def _next_steps(
    conn: sqlite3.Connection, session_id: str, top: int
) -> list[FollowupAction]:
    if top <= 0:
        return []
    rows = conn.execute(
        "SELECT payload FROM followups WHERE session_id=? "
        "AND executed_step_id IS NULL "
        "ORDER BY created_at_us, followup_id",
        (session_id,),
    ).fetchall()
    seen: set[str] = set()
    actions: list[FollowupAction] = []
    for row in rows:
        action = FollowupAction.model_validate(json.loads(row["payload"]))
        dedupe_key = canonical_json(
            {
                "operator": action.operator,
                "input_refs": sorted(action.input_refs),
                "params": action.params,
            }
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        actions.append(action)
        if len(actions) >= top:
            break
    return actions


@dataclass(frozen=True)
class SessionKnowledge:
    """Minimal projection of session evidence for agent consumption."""

    session_id: str
    snapshot_id: str
    snapshot_at: datetime
    evidence_completeness: EvidenceCompleteness
    _change_facts: list[ChangeFact] = field(default_factory=list)
    _next_steps_payload: list[FollowupAction] = field(default_factory=list)

    def facts(self, kind: FactKind | None = None) -> list[Any]:
        """Return validated facts, optionally filtered by kind."""
        if kind == "change":
            return list(self._change_facts)
        if kind is None:
            return list(self._change_facts)
        return []

    def next_steps(self, top: int = 5) -> list[FollowupAction]:
        """Return unexecuted followup actions, capped at *top*."""
        if top <= 0:
            return []
        return list(self._next_steps_payload[:top])


def build_session_knowledge(
    *, db_path: Path, session_id: str
) -> SessionKnowledge:
    """Build a SessionKnowledge snapshot from judgment.db."""
    conn = _open_readonly(db_path)
    try:
        completeness = _completeness(conn, session_id)
        facts = _change_facts(conn, session_id)
        actions = _next_steps(conn, session_id, top=100)
    finally:
        conn.close()
    snapshot_at = datetime.now(UTC)
    snapshot_id = f"snap_{session_id}_{int(snapshot_at.timestamp() * 1_000_000)}"
    return SessionKnowledge(
        session_id=session_id,
        snapshot_id=snapshot_id,
        snapshot_at=snapshot_at,
        evidence_completeness=completeness,
        _change_facts=facts,
        _next_steps_payload=actions,
    )


__all__ = ["SessionKnowledge", "build_session_knowledge"]
