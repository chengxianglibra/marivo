from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent, SessionState
from app.core.session.rebuild import rebuild_session_state


class SqliteSessionStore:
    """SQLite-backed SessionStore using WAL mode and per-request connections.

    Each append_event/load_events call opens a new connection with PRAGMAs,
    executes, and closes. For single-process local mode, connection overhead
    is negligible (<1ms).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            next_seq = (row[0] if row else 0) + 1
            conn.execute(
                "INSERT INTO session_events (session_id, seq, event_type, timestamp, payload, actor) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    next_seq,
                    event.event_type,
                    event.timestamp,
                    json.dumps(event.payload, sort_keys=True),
                    event.actor,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_id, event_type, timestamp, payload, actor "
                "FROM session_events WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
            if not rows:
                raise NotFoundError(
                    code=ErrorCode.SESSION_NOT_FOUND,
                    message=f"Session not found: {session_id}",
                )
            return [
                SessionEvent(
                    session_id=row[0],
                    event_type=row[1],
                    timestamp=row[2],
                    payload=json.loads(row[3]),
                    actor=row[4],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_id FROM session_events "
                "WHERE event_type = 'session_created' AND actor = ? "
                "ORDER BY timestamp ASC, session_id ASC",
                (str(owner),),
            ).fetchall()
        finally:
            conn.close()

        return [rebuild_session_state(self.load_events(SessionId(row[0]))) for row in rows]

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        """Proposition runtime status is not available in local mode."""
        raise NotImplementedError(
            "get_proposition_runtime_status is not available in local SQLite mode; "
            "server-mode proposition tracking requires a MetadataStore."
        )

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        """Paginated session listing is not available in local mode."""
        raise NotImplementedError(
            "list_sessions_paginated is not available in local SQLite mode; "
            "server-mode pagination requires a MetadataStore."
        )

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
                """CREATE TABLE IF NOT EXISTS session_events (
                    session_id  TEXT NOT NULL,
                    seq         INTEGER NOT NULL,
                    event_type  TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    actor       TEXT,
                    PRIMARY KEY (session_id, seq)
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_events_actor "
                "ON session_events (actor, event_type)"
            )
            conn.commit()
        finally:
            conn.close()
