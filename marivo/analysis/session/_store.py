"""SQLite-backed session store for the redesigned analysis session model.

Manages the session index, current-pointer, artifacts, and jobs in a single
WAL-mode database at ``.marivo/analysis/session_store.db``.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from marivo.project import resolve_project_root
from marivo.render import Card, RenderableResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    question TEXT,
    cwd TEXT NOT NULL,
    default_calendar TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated_id
ON sessions(updated_at, id);

CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    session_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    meta_path TEXT NOT NULL,
    content_hash TEXT,
    evidence_status TEXT NOT NULL DEFAULT 'unavailable',
    created_at TEXT NOT NULL,
    produced_by_job TEXT,
    PRIMARY KEY (session_id, artifact_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    session_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    output_artifact_id TEXT,
    record_path TEXT NOT NULL,
    PRIMARY KEY (session_id, job_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

"""


@dataclass(frozen=True, repr=False)
class SessionSummary(RenderableResult):
    """Lightweight session metadata returned by list_sessions."""

    id: str
    name: str
    question: str | None
    created_at: str
    updated_at: str
    job_count: int
    frame_count: int

    def _repr_identity(self) -> str:
        return f"SessionSummary id={self.id} name={self.name}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            f"jobs={self.job_count} frames={self.frame_count} updated={self.updated_at}"
        )
        if self.question:
            card.field("question", self.question)
        return card


def _gen_session_id() -> str:
    return f"sess_{secrets.token_hex(12)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    """SQLite-backed store for analysis session metadata.

    Each project has one store rooted at
    ``<project_root>/.marivo/analysis/session_store.db``.

    Args:
        project_root: Project root directory. When ``None``, resolved via
            :func:`resolve_project_root`.

    Example:
        >>> store = SessionStore(project_root=Path("/my/project"))
        >>> row = store.get_or_insert_session(
        ...     name="exploration", question="Why did revenue drop?",
        ...     cwd=Path.cwd(), default_calendar="fiscal",
        ... )
    """

    def __init__(self, project_root: str | Path | None = None) -> None:
        if project_root is None:
            self._project_root = resolve_project_root()
        else:
            self._project_root = Path(project_root).resolve()
        # Eagerly ensure the database and directory exist.
        with self._connect():
            pass

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def db_path(self) -> Path:
        return self._project_root / ".marivo" / "analysis" / "session_store.db"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with WAL, busy_timeout, and foreign keys enabled."""
        path = self.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        artifact_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        if "evidence_status" not in artifact_columns:
            conn.execute(
                "ALTER TABLE artifacts ADD COLUMN evidence_status TEXT "
                "NOT NULL DEFAULT 'unavailable'"
            )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _fetchone(
        conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()
    ) -> sqlite3.Row | None:
        """Execute a SELECT and return one row, typed as sqlite3.Row | None.

        This helper exists because ``cursor.fetchone()`` is typed as returning
        ``Any`` when ``row_factory`` is set at runtime, which triggers
        ``no-any-return`` on every return path. The cast is locally justified:
        the stdlib stubs cannot model the dynamic ``row_factory`` contract.
        """
        return cast("sqlite3.Row | None", conn.execute(sql, params).fetchone())

    @staticmethod
    def _fetchall(
        conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()
    ) -> list[sqlite3.Row]:
        """Execute a SELECT and return all rows, typed as list[sqlite3.Row].

        Same justification as :meth:`_fetchone`.
        """
        return cast("list[sqlite3.Row]", conn.execute(sql, params).fetchall())

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def get_or_insert_session(
        self,
        *,
        name: str,
        question: str | None,
        cwd: Path,
        default_calendar: str | None,
    ) -> sqlite3.Row:
        """Return the existing session row for *name*, or insert a new one.

        If a session with *name* already exists:
        - The original *question* is preserved.
        - If *default_calendar* is not ``None``, it updates the persisted
          calendar and bumps ``updated_at``.
        - If *default_calendar* is ``None``, the persisted calendar is
          restored (left unchanged).

        Race handling: when a concurrent insert wins on the UNIQUE name
        constraint, the method catches the error and loads the existing row.

        Args:
            name: Unique session name.
            question: The analysis question. Preserved on re-insert.
            cwd: Working directory at session creation time.
            default_calendar: Calendar to persist for the session.

        Returns:
            The session row (as :class:`sqlite3.Row`).
        """
        with self._connect() as conn:
            existing = self._fetchone(conn, "SELECT * FROM sessions WHERE name = ?", (name,))
            if existing is not None:
                if (
                    default_calendar is not None
                    and default_calendar != existing["default_calendar"]
                ):
                    now = _now_iso()
                    conn.execute(
                        "UPDATE sessions SET default_calendar = ?, updated_at = ? WHERE id = ?",
                        (default_calendar, now, existing["id"]),
                    )
                    updated = self._fetchone(
                        conn, "SELECT * FROM sessions WHERE id = ?", (existing["id"],)
                    )
                    assert updated is not None  # just updated the row, must exist
                    return updated
                return existing

            sid = _gen_session_id()
            now = _now_iso()
            cwd_str = str(cwd)
            try:
                conn.execute(
                    "INSERT INTO sessions (id, name, question, cwd, default_calendar, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, name, question, cwd_str, default_calendar, now, now),
                )
            except sqlite3.IntegrityError:
                # Race: another process inserted the same name
                row = self._fetchone(conn, "SELECT * FROM sessions WHERE name = ?", (name,))
                if row is None:
                    raise
                return row
            inserted = self._fetchone(conn, "SELECT * FROM sessions WHERE id = ?", (sid,))
            assert inserted is not None  # just inserted the row, must exist
            return inserted

    def get_session_by_name(self, name: str) -> sqlite3.Row | None:
        """Look up a session row by name.

        Args:
            name: The session name.

        Returns:
            The matching row, or ``None`` when not found.
        """
        with self._connect() as conn:
            return self._fetchone(conn, "SELECT * FROM sessions WHERE name = ?", (name,))

    def get_session_by_id(self, session_id: str) -> sqlite3.Row | None:
        """Look up a session row by id.

        Args:
            session_id: The session id (``sess_...``).

        Returns:
            The matching row, or ``None`` when not found.
        """
        with self._connect() as conn:
            return self._fetchone(conn, "SELECT * FROM sessions WHERE id = ?", (session_id,))

    def list_sessions(self) -> list[SessionSummary]:
        """Return summaries for all sessions, including live counts.

        ``job_count`` and ``frame_count`` are computed from the ``jobs`` and
        ``artifacts`` tables at list time.

        Returns:
            A list of :class:`SessionSummary` instances.
        """
        with self._connect() as conn:
            rows = self._fetchall(conn, "SELECT * FROM sessions ORDER BY created_at")
            summaries: list[SessionSummary] = []
            for row in rows:
                sid = row["id"]
                job_count = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE session_id = ?", (sid,)
                ).fetchone()[0]
                frame_count = conn.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE session_id = ?", (sid,)
                ).fetchone()[0]
                summaries.append(
                    SessionSummary(
                        id=sid,
                        name=row["name"],
                        question=row["question"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        job_count=job_count,
                        frame_count=frame_count,
                    )
                )
            return summaries

    def page_sessions(
        self,
        *,
        limit: int,
        after: tuple[str, str] | None,
    ) -> list[SessionSummary]:
        """Return at most ``limit + 1`` recently updated session summaries.

        Rows use newest-first keyset order over ``(updated_at, id)``. Counts
        are calculated in the same query so the page does not grow an N+1
        read pattern as the project accumulates sessions.
        """
        clauses: list[str] = []
        params: list[object] = []
        if after is not None:
            clauses.append("(s.updated_at < ? OR (s.updated_at = ? AND s.id < ?))")
            params.extend((after[0], after[0], after[1]))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit + 1)
        with self._connect() as conn:
            rows = self._fetchall(
                conn,
                "SELECT s.*, "
                "(SELECT COUNT(*) FROM jobs j WHERE j.session_id = s.id) AS job_count, "
                "(SELECT COUNT(*) FROM artifacts a WHERE a.session_id = s.id) AS frame_count "
                f"FROM sessions s {where} "
                "ORDER BY s.updated_at DESC, s.id DESC LIMIT ?",
                tuple(params),
            )
        return [
            SessionSummary(
                id=row["id"],
                name=row["name"],
                question=row["question"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                job_count=row["job_count"],
                frame_count=row["frame_count"],
            )
            for row in rows
        ]

    def session_summary(self, name: str) -> SessionSummary | None:
        """Return one exact session summary without touching session state."""
        with self._connect() as conn:
            row = self._fetchone(
                conn,
                "SELECT s.*, "
                "(SELECT COUNT(*) FROM jobs j WHERE j.session_id = s.id) AS job_count, "
                "(SELECT COUNT(*) FROM artifacts a WHERE a.session_id = s.id) AS frame_count "
                "FROM sessions s WHERE s.name = ?",
                (name,),
            )
        if row is None:
            return None
        return SessionSummary(
            id=row["id"],
            name=row["name"],
            question=row["question"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            job_count=row["job_count"],
            frame_count=row["frame_count"],
        )

    def touch_session(self, session_id: str) -> str:
        """Bump ``updated_at`` for a session.

        Args:
            session_id: The session id to touch.

        Returns:
            The new ``updated_at`` timestamp string.
        """
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        return now

    def update_default_calendar(self, session_id: str, default_calendar: str | None) -> str:
        """Update the default calendar for a session.

        Args:
            session_id: The session id.
            default_calendar: New calendar value, or ``None`` to clear it.

        Returns:
            The new ``updated_at`` timestamp string.
        """
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET default_calendar = ?, updated_at = ? WHERE id = ?",
                (default_calendar, now, session_id),
            )
        return now

    # ------------------------------------------------------------------
    # Current pointer
    # ------------------------------------------------------------------

    def get_current_session_id(self) -> str | None:
        """Return the id of the current session, or ``None`` if unset."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE key = 'current_session_id'"
            ).fetchone()
            return row["value"] if row else None

    def set_current_session_id(self, session_id: str) -> None:
        """Persist the current session id.

        Args:
            session_id: The session id to mark as current.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state (key, value) VALUES ('current_session_id', ?)",
                (session_id,),
            )

    def clear_current_session_id(self) -> None:
        """Remove the current session pointer."""
        with self._connect() as conn:
            conn.execute("DELETE FROM runtime_state WHERE key = 'current_session_id'")

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_session_rows(self, name: str) -> sqlite3.Row | None:
        """Delete a session and its related rows by name.

        Removes rows from ``sessions``, ``artifacts``, and ``jobs``. Does
        **not** remove any files on disk.

        Args:
            name: The session name to delete.

        Returns:
            The deleted session row, or ``None`` if no such session exists.
        """
        with self._connect() as conn:
            row = self._fetchone(conn, "SELECT * FROM sessions WHERE name = ?", (name,))
            if row is None:
                return None
            sid = row["id"]
            # Explicit child-table deletes before parent; CASCADE is defense-in-depth.
            conn.execute("DELETE FROM jobs WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM artifacts WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            return row

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def record_artifact(
        self,
        *,
        session_id: str,
        artifact_id: str,
        kind: str,
        path: str,
        meta_path: str,
        content_hash: str | None,
        produced_by_job: str | None,
        evidence_status: str = "unavailable",
    ) -> None:
        """Insert an artifact row.

        Args:
            session_id: Owning session.
            artifact_id: Unique artifact identifier within the session.
            kind: Artifact kind (e.g. ``"frame"``).
            path: Project-relative path to the artifact data.
            meta_path: Project-relative path to the artifact metadata.
            content_hash: Optional content hash for integrity checks.
            produced_by_job: Optional job id that produced this artifact.
        """
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifacts (session_id, artifact_id, kind, path, meta_path, "
                "content_hash, evidence_status, created_at, produced_by_job) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    artifact_id,
                    kind,
                    path,
                    meta_path,
                    content_hash,
                    evidence_status,
                    now,
                    produced_by_job,
                ),
            )

    def get_artifact(self, session_id: str, artifact_id: str) -> sqlite3.Row | None:
        """Look up an artifact by session and artifact id.

        Args:
            session_id: Owning session.
            artifact_id: Artifact identifier.

        Returns:
            The matching row, or ``None`` when not found.
        """
        with self._connect() as conn:
            return self._fetchone(
                conn,
                "SELECT * FROM artifacts WHERE session_id = ? AND artifact_id = ?",
                (session_id, artifact_id),
            )

    def list_artifacts(self, session_id: str) -> list[sqlite3.Row]:
        """Return all artifact rows for a session.

        Args:
            session_id: Owning session.

        Returns:
            A list of artifact rows.
        """
        with self._connect() as conn:
            return self._fetchall(
                conn,
                "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )

    def page_artifacts(
        self,
        session_id: str,
        *,
        kind: str | None,
        evidence_status: str | None,
        limit: int,
        after: tuple[str, str] | None,
    ) -> list[sqlite3.Row]:
        """Return at most ``limit + 1`` newest artifact rows for keyset paging."""
        clauses = ["session_id = ?"]
        params: list[object] = [session_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if evidence_status is not None:
            clauses.append("evidence_status = ?")
            params.append(evidence_status)
        if after is not None:
            clauses.append("(created_at < ? OR (created_at = ? AND artifact_id < ?))")
            params.extend((after[0], after[0], after[1]))
        params.append(limit + 1)
        with self._connect() as conn:
            return self._fetchall(
                conn,
                f"SELECT * FROM artifacts WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
                tuple(params),
            )

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def record_job(
        self,
        *,
        session_id: str,
        job_id: str,
        intent: str,
        status: str,
        started_at: str,
        finished_at: str | None,
        output_artifact_id: str | None,
        record_path: str,
    ) -> None:
        """Insert a job row.

        Args:
            session_id: Owning session.
            job_id: Unique job identifier within the session.
            intent: The intent name (e.g. ``"observe"``).
            status: Job status (e.g. ``"completed"``).
            started_at: ISO-8601 timestamp when the job started.
            finished_at: ISO-8601 timestamp when the job finished, or ``None``.
            output_artifact_id: Artifact id produced by this job, or ``None``.
            record_path: Project-relative path to the job record file.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (session_id, job_id, intent, status, started_at, "
                "finished_at, output_artifact_id, record_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    job_id,
                    intent,
                    status,
                    started_at,
                    finished_at,
                    output_artifact_id,
                    record_path,
                ),
            )

    def get_job(self, session_id: str, job_id: str) -> sqlite3.Row | None:
        """Look up a job by session and job id.

        Args:
            session_id: Owning session.
            job_id: Job identifier.

        Returns:
            The matching row, or ``None`` when not found.
        """
        with self._connect() as conn:
            return self._fetchone(
                conn,
                "SELECT * FROM jobs WHERE session_id = ? AND job_id = ?",
                (session_id, job_id),
            )

    def list_jobs(self, session_id: str) -> list[sqlite3.Row]:
        """Return all job rows for a session.

        Args:
            session_id: Owning session.

        Returns:
            A list of job rows ordered by started_at.
        """
        with self._connect() as conn:
            return self._fetchall(
                conn,
                "SELECT * FROM jobs WHERE session_id = ? ORDER BY started_at",
                (session_id,),
            )
