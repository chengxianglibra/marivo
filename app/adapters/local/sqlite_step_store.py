from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.ids import SessionId, StepId
from app.contracts.session import Step


class SqliteStepStore:
    """SQLite-backed StepStore. Shares the local state.db with SessionStore."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def insert_step(
        self,
        step_id: StepId,
        session_id: SessionId,
        step_type: str,
        summary: str,
        result: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
        semantic_metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO steps (step_id, session_id, step_type, summary, "
                "result, provenance, semantic_metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(step_id),
                    str(session_id),
                    step_type,
                    summary,
                    json.dumps(result, sort_keys=True),
                    json.dumps(provenance, sort_keys=True) if provenance else None,
                    json.dumps(semantic_metadata, sort_keys=True) if semantic_metadata else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def list_steps(self, session_id: SessionId) -> list[Step]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT step_id, session_id, step_type, summary, result, "
                "provenance, semantic_metadata, created_at "
                "FROM steps WHERE session_id = ? ORDER BY created_at ASC, step_id ASC",
                (str(session_id),),
            ).fetchall()
            return [
                Step(
                    step_id=StepId(row[0]),
                    session_id=SessionId(row[1]),
                    step_type=row[2],
                    summary=row[3],
                    result=json.loads(row[4]),
                    provenance=json.loads(row[5]) if row[5] else None,
                    semantic_metadata=json.loads(row[6]) if row[6] else None,
                    created_at=row[7],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS steps (
                    step_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    result TEXT NOT NULL,
                    provenance TEXT,
                    semantic_metadata TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_steps_session ON steps (session_id, created_at)"
            )
            conn.commit()
        finally:
            conn.close()
