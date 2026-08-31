"""SQLite-backed session store for the redesigned analysis session model.

Manages the session index, current-pointer, artifacts, and Runs in a single
WAL-mode database at ``.marivo/analysis/session_store.db``.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from marivo._compat import UTC
from marivo.analysis.errors import (
    AnalysisRepair,
    RunNotFoundError,
    SchemaVersionMismatchError,
    SessionLockedByAnotherProcessError,
    SessionNotFoundError,
    SessionStateError,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.project import resolve_project_root
from marivo.render import Card, RenderableResult

_STORE_SCHEMA_VERSION = 1
_RUN_PAYLOAD_SCHEMA = "marivo.analysis_run/v1"


def _run_abandoned_failure(run_id: str) -> dict[str, object]:
    return {
        "error_type": "RunAbandoned",
        "message": "The incomplete Run was explicitly abandoned after execution stopped.",
        "expected": None,
        "received": None,
        "location": f"Run {run_id!r}",
        "repair": None,
    }


class _RuntimeSnapshotTooLargeError(RuntimeError):
    def __init__(self, count: int) -> None:
        super().__init__(f"runtime snapshot contains {count} records")
        self.count = count


@dataclass(frozen=True, slots=True)
class _FocusedRuntimeRows:
    runs: tuple[sqlite3.Row, ...]
    inputs: tuple[sqlite3.Row, ...]
    artifacts: tuple[sqlite3.Row, ...]
    boundary_artifact_refs: tuple[str, ...]
    boundary_run_ids: tuple[str, ...]
    non_head_artifact_refs: tuple[str, ...]
    missing_artifact_refs: tuple[str, ...]
    missing_run_ids: tuple[str, ...]
    truncated: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    question TEXT,
    cwd TEXT NOT NULL,
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
    artifact_schema_version TEXT NOT NULL,
    evidence_status TEXT NOT NULL DEFAULT 'unavailable',
    finding_count INTEGER NOT NULL CHECK (finding_count >= 0),
    created_at TEXT NOT NULL,
    produced_by_job TEXT,
    PRIMARY KEY (session_id, artifact_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    payload_schema TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('incomplete', 'succeeded', 'failed')),
    capability_id TEXT NOT NULL,
    analysis_purpose TEXT,
    arguments_json TEXT NOT NULL,
    omitted_argument_names_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    output_artifact_ref TEXT,
    output_mode TEXT CHECK (output_mode IN ('produced', 'reused')),
    failure_json TEXT,
    PRIMARY KEY (session_id, run_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_session_started
ON runs(session_id, started_at, run_id);

CREATE TABLE IF NOT EXISTS run_inputs (
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (session_id, run_id, position),
    FOREIGN KEY (session_id, run_id)
        REFERENCES runs(session_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_inputs_artifact
ON run_inputs(session_id, artifact_ref, run_id);
"""


@dataclass(frozen=True, repr=False, kw_only=True)
class SessionSummary(RenderableResult):
    """Lightweight session metadata returned by page_sessions / session_summary."""

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
        card = Card(identity=self._repr_identity(), available=(".show()",)).status(
            f"jobs={self.job_count} frames={self.frame_count} updated={self.updated_at}"
        )
        if self.question:
            card.field("question", self.question)
        return card


def _gen_session_id() -> str:
    return f"sess_{secrets.token_hex(12)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


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
        ...     cwd=Path.cwd(),
        ... )
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if project_root is None:
            self._project_root = resolve_project_root()
        else:
            self._project_root = Path(project_root).resolve()
        self._busy_timeout_ms = busy_timeout_ms
        self._initialize_or_validate()

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def db_path(self) -> Path:
        return self._project_root / ".marivo" / "analysis" / "session_store.db"

    @staticmethod
    def _schema_repair() -> AnalysisRepair:
        return AnalysisRepair(
            kind="environment",
            action=(
                "Preserve or move the existing .marivo/analysis directory, then create "
                "a new named Session in a fresh analysis store; do not migrate or reuse "
                "the incompatible state."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.sessions"),
        )

    def _raise_store_schema_mismatch(self, received: int) -> None:
        raise SchemaVersionMismatchError(
            message="the project Session Store is incompatible with this runtime",
            expected=f"Session Store user_version={_STORE_SCHEMA_VERSION}",
            received=f"Session Store user_version={received}",
            location=str(self.db_path),
            repair=self._schema_repair(),
            context={
                "expected_schema": _STORE_SCHEMA_VERSION,
                "received_schema": received,
                "db_path": str(self.db_path),
            },
        )

    def _initialize_or_validate(self) -> None:
        """Create one fresh v1 store or validate an existing store read-only first."""
        path = self.db_path
        if path.exists():
            uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
            read_conn = sqlite3.connect(uri, uri=True)
            try:
                received = int(read_conn.execute("PRAGMA user_version").fetchone()[0])
            finally:
                read_conn.close()
            if received != _STORE_SCHEMA_VERSION:
                self._raise_store_schema_mismatch(received)
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(path), timeout=self._busy_timeout_ms / 1000)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={_STORE_SCHEMA_VERSION}")
            conn.commit()
        except BaseException:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with WAL, busy_timeout, and foreign keys enabled."""
        path = self.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                str(path),
                timeout=self._busy_timeout_ms / 1000,
            )
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except BaseException as exc:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            if isinstance(exc, sqlite3.OperationalError) and _is_lock_error(exc):
                raise SessionLockedByAnotherProcessError(
                    message=f"session_store.db locked: {path}",
                    context={"db_path": str(path), "cause": str(exc)},
                ) from exc
            raise
        finally:
            if conn is not None:
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
    ) -> sqlite3.Row:
        """Return the existing session row for *name*, or insert a new one.

        If a session with *name* already exists, the original question and
        project identity are preserved.

        Race handling: when a concurrent insert wins on the UNIQUE name
        constraint, the method catches the error and loads the existing row.

        Args:
            name: Unique session name.
            question: The analysis question. Preserved on re-insert.
            cwd: Working directory at session creation time.

        Returns:
            The session row (as :class:`sqlite3.Row`).
        """
        with self._connect() as conn:
            existing = self._fetchone(conn, "SELECT * FROM sessions WHERE name = ?", (name,))
            if existing is not None:
                return existing

            sid = _gen_session_id()
            now = _now_iso()
            cwd_str = str(cwd)
            try:
                conn.execute(
                    "INSERT INTO sessions (id, name, question, cwd, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sid, name, question, cwd_str, now, now),
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

    @contextmanager
    def activate_session(
        self,
        *,
        session_id: str,
        question_update: str | None,
    ) -> Iterator[sqlite3.Row]:
        """Publish one session activation in a single database transaction.

        The transaction holds an immediate write lock while the caller publishes
        derived filesystem metadata. If that publication raises, the question,
        timestamp, and current pointer all roll back together. An omitted
        ``question_update`` touches the session without replacing its question.

        Args:
            session_id: Immutable session id.
            question_update: Explicit current guiding question, including an
                empty string, or ``None`` to preserve the persisted value.

        Yields:
            The refreshed session row to publish to derived metadata.

        Raises:
            SessionNotFoundError: The session no longer exists.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._fetchone(conn, "SELECT * FROM sessions WHERE id = ?", (session_id,))
            if existing is None:
                raise SessionNotFoundError(
                    message=f"session {session_id!r} was not found while activating it",
                    context={"session_id": session_id},
                )

            now = _now_iso()
            if question_update is None:
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET question = ?, updated_at = ? WHERE id = ?",
                    (question_update, now, session_id),
                )
            row = self._fetchone(conn, "SELECT * FROM sessions WHERE id = ?", (session_id,))
            assert row is not None
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state (key, value) "
                "VALUES ('current_session_id', ?)",
                (session_id,),
            )
            yield row

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

    def get_sessions_by_identity(
        self,
        identity: str,
        *,
        by: Literal["name", "id"] | None = None,
    ) -> tuple[sqlite3.Row, ...]:
        """Look up exact session id and name matches in one database snapshot.

        Args:
            identity: Exact session id or name.
            by: Optional exact identity column to select.

        Returns:
            Zero, one, or two distinct matching rows. An id match is ordered
            before a name match when the identity resolves to different rows.
        """
        with self._connect() as conn:
            if by == "name":
                rows = self._fetchall(
                    conn,
                    "SELECT * FROM sessions WHERE name = ?",
                    (identity,),
                )
            elif by == "id":
                rows = self._fetchall(
                    conn,
                    "SELECT * FROM sessions WHERE id = ?",
                    (identity,),
                )
            else:
                rows = self._fetchall(
                    conn,
                    "SELECT * FROM sessions WHERE id = ? OR name = ? "
                    "ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, id",
                    (identity, identity, identity),
                )
        return tuple(rows)

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
                "(SELECT COUNT(*) FROM runs r WHERE r.session_id = s.id "
                "AND r.lifecycle = 'succeeded') AS job_count, "
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
                "(SELECT COUNT(*) FROM runs r WHERE r.session_id = s.id "
                "AND r.lifecycle = 'succeeded') AS job_count, "
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

        Removes rows from ``sessions``, ``artifacts``, and ``runs``. Does
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
            conn.execute("DELETE FROM runs WHERE session_id = ?", (sid,))
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
        artifact_schema_version: str = "analysis-artifact/v13",
        finding_count: int = 0,
        created_at: str | None = None,
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
            evidence_status: Canonical evidence availability state.
            artifact_schema_version: Exact persisted Artifact metadata schema.
            finding_count: Exact non-negative number of committed Findings.
            created_at: Canonical artifact creation time. Defaults to the
                registration time for callers without persisted metadata.
        """
        if finding_count < 0:
            raise ValueError("finding_count must be non-negative")
        committed_at = created_at or _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifacts (session_id, artifact_id, kind, path, meta_path, "
                "content_hash, artifact_schema_version, evidence_status, finding_count, "
                "created_at, produced_by_job) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    artifact_id,
                    kind,
                    path,
                    meta_path,
                    content_hash,
                    artifact_schema_version,
                    evidence_status,
                    finding_count,
                    committed_at,
                    produced_by_job,
                ),
            )

    def record_recovered_artifact(
        self,
        *,
        session_id: str,
        artifact_id: str,
        kind: str,
        path: str,
        meta_path: str,
        content_hash: str | None,
        produced_by_job: str | None,
        evidence_status: str,
        artifact_schema_version: str,
        finding_count: int,
        created_at: str,
    ) -> None:
        """Register a validated recovery Artifact and reconcile its Run atomically."""
        if artifact_schema_version != "analysis-artifact/v13" or finding_count < 0:
            raise SessionStateError(
                message="recovery Artifact metadata is not exact-current",
                expected="analysis-artifact/v13 with non-negative finding_count",
                received=f"{artifact_schema_version}, finding_count={finding_count}",
                location=f"Artifact {artifact_id!r} recovery",
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._fetchone(
                conn,
                "SELECT 1 FROM artifacts WHERE session_id = ? AND artifact_id = ?",
                (session_id, artifact_id),
            )
            if existing is not None:
                raise SessionStateError(
                    message="recovery Artifact is already registered",
                    expected="one absent Session Artifact registration",
                    received=artifact_id,
                    location=f"Artifact {artifact_id!r} recovery",
                )
            conn.execute(
                "INSERT INTO artifacts (session_id, artifact_id, kind, path, meta_path, "
                "content_hash, artifact_schema_version, evidence_status, finding_count, "
                "created_at, produced_by_job) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    artifact_id,
                    kind,
                    path,
                    meta_path,
                    content_hash,
                    artifact_schema_version,
                    evidence_status,
                    finding_count,
                    created_at,
                    produced_by_job,
                ),
            )
            if produced_by_job is None:
                return
            run = self._fetchone(
                conn,
                "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                (session_id, produced_by_job),
            )
            if run is None:
                return
            self._validate_payload_schema(run, location=f"Run {produced_by_job!r}")
            if run["lifecycle"] == "incomplete":
                conn.execute(
                    "UPDATE runs SET lifecycle = 'succeeded', finished_at = ?, "
                    "output_artifact_ref = ?, output_mode = 'produced', failure_json = NULL "
                    "WHERE session_id = ? AND run_id = ?",
                    (created_at, artifact_id, session_id, produced_by_job),
                )
                return
            if not (
                run["lifecycle"] == "succeeded"
                and run["output_artifact_ref"] == artifact_id
                and run["output_mode"] == "produced"
            ):
                raise SessionStateError(
                    message="recovery Artifact producer conflicts with a terminal Run",
                    expected="matching succeeded produced Run or one incomplete Run",
                    received=str(run["lifecycle"]),
                    location=f"Run {produced_by_job!r}",
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

    def delete_artifact(self, session_id: str, artifact_id: str) -> None:
        """Delete one artifact registration owned by a session."""

        with self._connect() as conn:
            conn.execute(
                "DELETE FROM artifacts WHERE session_id = ? AND artifact_id = ?",
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
        if kind is None:
            # Linked component and coverage frames are internal sidecars, not
            # independent materialization results. They remain directly
            # loadable and explicitly queryable by kind.
            clauses.append("kind NOT IN ('component_frame', 'coverage_frame')")
        else:
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
    # Canonical Run lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_payload_schema(row: sqlite3.Row, *, location: str) -> None:
        received = row["payload_schema"]
        if received != _RUN_PAYLOAD_SCHEMA:
            raise SchemaVersionMismatchError(
                message="persisted analysis Run uses an incompatible payload schema",
                expected=_RUN_PAYLOAD_SCHEMA,
                received=str(received),
                location=location,
                repair=SessionStore._schema_repair(),
            )

    def validate_session_runtime_read_schema(self, session_id: str) -> None:
        """Fail before reads when the Store or indexed runtime schema is incompatible."""
        with self._connect() as conn:
            received_store_schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if received_store_schema != _STORE_SCHEMA_VERSION:
                self._raise_store_schema_mismatch(received_store_schema)
            rows = self._fetchall(
                conn,
                "SELECT run_id, payload_schema FROM runs WHERE session_id = ?",
                (session_id,),
            )
            for row in rows:
                self._validate_payload_schema(
                    row,
                    location=f"Session {session_id!r} Run {row['run_id']!r}",
                )
            artifact = self._fetchone(
                conn,
                "SELECT artifact_id, artifact_schema_version FROM artifacts "
                "WHERE session_id = ? AND artifact_schema_version != ? LIMIT 1",
                (session_id, "analysis-artifact/v13"),
            )
        if artifact is not None:
            from marivo.analysis.errors import FrameMetaInvalidError

            raise FrameMetaInvalidError(
                message="persisted Artifact metadata is incompatible with this runtime",
                expected="analysis-artifact/v13",
                received=str(artifact["artifact_schema_version"]),
                location=f"Artifact {artifact['artifact_id']!r} metadata schema",
                repair=self._schema_repair(),
            )

    def validate_session_runtime_schema(self, session_id: str) -> None:
        """Fail before activation when one target Session is not exact-current."""
        self.validate_session_runtime_read_schema(session_id)
        with self._connect() as conn:
            artifact_rows = self._fetchall(
                conn,
                "SELECT artifact_id, meta_path FROM artifacts WHERE session_id = ?",
                (session_id,),
            )
        from marivo.analysis.errors import FrameMetaInvalidError

        for artifact_row in artifact_rows:
            meta_path = self.project_root / str(artifact_row["meta_path"])
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                received = payload.get("artifact_schema_version")
            except (OSError, ValueError) as exc:
                raise FrameMetaInvalidError(
                    message="persisted Artifact metadata cannot be validated before activation",
                    expected="readable analysis-artifact/v13 metadata",
                    received=type(exc).__name__,
                    location=str(meta_path),
                    repair=self._schema_repair(),
                ) from exc
            if received != "analysis-artifact/v13":
                raise FrameMetaInvalidError(
                    message="persisted Artifact metadata is incompatible with this runtime",
                    expected="analysis-artifact/v13",
                    received=str(received),
                    location=str(meta_path),
                    repair=self._schema_repair(),
                )

    def begin_run(
        self,
        *,
        session_id: str,
        run_id: str,
        capability_id: str,
        analysis_purpose: str | None,
        arguments: list[dict[str, object]],
        omitted_argument_names: tuple[str, ...],
        input_artifact_refs: tuple[str, ...],
        started_at: str,
    ) -> None:
        """Atomically persist one admitted incomplete Run and its input index."""
        arguments_json = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        omitted_json = json.dumps(
            list(omitted_argument_names), ensure_ascii=False, separators=(",", ":")
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for artifact_ref in input_artifact_refs:
                owned = self._fetchone(
                    conn,
                    "SELECT 1 FROM artifacts WHERE session_id = ? AND artifact_id = ?",
                    (session_id, artifact_ref),
                )
                if owned is None:
                    raise SessionStateError(
                        message="Run input Artifact is absent from the owning Session",
                        expected=f"Artifact owned by Session {session_id!r}",
                        received=artifact_ref,
                        location=f"Run {run_id!r} admission",
                    )
            conn.execute(
                "INSERT INTO runs (session_id, run_id, payload_schema, lifecycle, "
                "capability_id, analysis_purpose, arguments_json, "
                "omitted_argument_names_json, started_at) "
                "VALUES (?, ?, ?, 'incomplete', ?, ?, ?, ?, ?)",
                (
                    session_id,
                    run_id,
                    _RUN_PAYLOAD_SCHEMA,
                    capability_id,
                    analysis_purpose,
                    arguments_json,
                    omitted_json,
                    started_at,
                ),
            )
            conn.executemany(
                "INSERT INTO run_inputs (session_id, run_id, artifact_ref, position) "
                "VALUES (?, ?, ?, ?)",
                (
                    (session_id, run_id, artifact_ref, position)
                    for position, artifact_ref in enumerate(input_artifact_refs)
                ),
            )

    def complete_run(
        self,
        *,
        session_id: str,
        run_id: str,
        output_artifact_ref: str,
        output_mode: str,
        finished_at: str,
        arguments: list[dict[str, object]] | None = None,
        omitted_argument_names: tuple[str, ...] | None = None,
    ) -> None:
        """Finalize effective arguments and succeed a Run in one write transaction."""
        if output_mode not in {"produced", "reused"}:
            raise ValueError("output_mode must be 'produced' or 'reused'")
        if (arguments is None) != (omitted_argument_names is None):
            raise ValueError("arguments and omitted_argument_names must be supplied together")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetchone(
                conn,
                "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                (session_id, run_id),
            )
            if row is None or row["lifecycle"] != "incomplete":
                received = "absent" if row is None else str(row["lifecycle"])
                raise SessionStateError(
                    message="illegal persisted Run transition",
                    expected="incomplete -> succeeded",
                    received=received,
                    location=f"Run {run_id!r}",
                )
            self._validate_payload_schema(row, location=f"Run {run_id!r}")
            artifact = self._fetchone(
                conn,
                "SELECT produced_by_job FROM artifacts WHERE session_id = ? AND artifact_id = ?",
                (session_id, output_artifact_ref),
            )
            if artifact is None:
                raise SessionStateError(
                    message="cannot complete a Run before its Artifact registration exists",
                    expected="one committed same-Session Artifact registration",
                    received=output_artifact_ref,
                    location=f"Run {run_id!r} terminal transition",
                )
            producer = artifact["produced_by_job"]
            if output_mode == "produced" and producer != run_id:
                raise SessionStateError(
                    message="produced Artifact does not identify the completing Run",
                    expected=run_id,
                    received=str(producer),
                    location=f"Artifact {output_artifact_ref!r} producer",
                )
            if output_mode == "reused":
                producer_run = (
                    self._fetchone(
                        conn,
                        "SELECT lifecycle FROM runs WHERE session_id = ? AND run_id = ?",
                        (session_id, producer),
                    )
                    if isinstance(producer, str) and producer != run_id
                    else None
                )
                if producer_run is None or producer_run["lifecycle"] != "succeeded":
                    raise SessionStateError(
                        message="reused Artifact requires another succeeded canonical producing Run",
                        expected="a different succeeded canonical producing Run in the same Session",
                        received=repr(producer),
                        location=f"Artifact {output_artifact_ref!r} producer",
                    )
            assignments = [
                "lifecycle = 'succeeded'",
                "finished_at = ?",
                "output_artifact_ref = ?",
                "output_mode = ?",
                "failure_json = NULL",
            ]
            values: list[object] = [finished_at, output_artifact_ref, output_mode]
            if arguments is not None and omitted_argument_names is not None:
                assignments.extend(["arguments_json = ?", "omitted_argument_names_json = ?"])
                values.extend(
                    [
                        json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(
                            list(omitted_argument_names),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ]
                )
            conn.execute(
                f"UPDATE runs SET {', '.join(assignments)} WHERE session_id = ? AND run_id = ?",
                (*values, session_id, run_id),
            )

    def fail_run(
        self,
        *,
        session_id: str,
        run_id: str,
        failure: dict[str, object],
        failed_at: str,
    ) -> None:
        """Transition one incomplete Run to a sanitized failed variant."""
        failure_json = json.dumps(failure, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetchone(
                conn,
                "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                (session_id, run_id),
            )
            if row is None or row["lifecycle"] != "incomplete":
                received = "absent" if row is None else str(row["lifecycle"])
                raise SessionStateError(
                    message="illegal persisted Run transition",
                    expected="incomplete -> failed",
                    received=received,
                    location=f"Run {run_id!r}",
                )
            self._validate_payload_schema(row, location=f"Run {run_id!r}")
            conn.execute(
                "UPDATE runs SET lifecycle = 'failed', finished_at = ?, "
                "failure_json = ?, output_artifact_ref = NULL, output_mode = NULL "
                "WHERE session_id = ? AND run_id = ?",
                (failed_at, failure_json, session_id, run_id),
            )

    def abandon_run(self, *, session_id: str, run_id: str) -> None:
        """Atomically abandon one stopped incomplete Run and its unconsumed outputs."""
        failure_json = json.dumps(
            _run_abandoned_failure(run_id),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = self._fetchone(
                conn,
                "SELECT id FROM sessions WHERE id = ?",
                (session_id,),
            )
            if session is None:
                candidates = tuple(
                    str(row["id"])
                    for row in self._fetchall(
                        conn,
                        "SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT 10",
                    )
                )
                raise SessionNotFoundError(
                    message=f"analysis session id {session_id!r} was not found in the current project",
                    expected="an existing immutable session id from mv.session.recent().items",
                    received=session_id,
                    location="mv.session.abandon_run(session_id=...)",
                    repair=AnalysisRepair(
                        kind="inspect",
                        action="Read mv.session.recent() and retry one returned immutable session id.",
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id="session.recent",
                        ),
                        candidates=candidates,
                    ),
                )
            run = self._fetchone(
                conn,
                "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                (session_id, run_id),
            )
            if run is None:
                candidates = tuple(
                    str(row["run_id"])
                    for row in self._fetchall(
                        conn,
                        "SELECT run_id FROM runs WHERE session_id = ? "
                        "AND lifecycle = 'incomplete' "
                        "ORDER BY started_at DESC, run_id DESC LIMIT 10",
                        (session_id,),
                    )
                )
                raise RunNotFoundError(
                    message=f"Run {run_id!r} does not exist in Session {session_id!r}",
                    expected="an exact incomplete Run id owned by the selected Session",
                    received=run_id,
                    location="mv.session.abandon_run(run_id=...)",
                    repair=AnalysisRepair(
                        kind="retry",
                        action="Retry with one exact incomplete Run id from the recovery error.",
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id="session.abandon_run",
                        ),
                        candidates=candidates,
                    ),
                )
            self._validate_payload_schema(run, location=f"Run {run_id!r}")
            if run["lifecycle"] == "failed":
                try:
                    persisted_failure = json.loads(str(run["failure_json"]))
                except (TypeError, ValueError):
                    persisted_failure = None
                if persisted_failure == _run_abandoned_failure(run_id):
                    return
            if run["lifecycle"] != "incomplete":
                received = str(run["lifecycle"])
                if run["lifecycle"] == "failed":
                    received = "failed by another failure"
                raise SessionStateError(
                    message="only an incomplete Run can be explicitly abandoned",
                    expected="incomplete or the same already-abandoned Run",
                    received=received,
                    location=f"Run {run_id!r}",
                    repair=AnalysisRepair(
                        kind="inspect",
                        action="Leave this terminal Run unchanged and inspect its persisted state.",
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id="runtime.runs",
                        ),
                    ),
                )
            consumers = self._fetchall(
                conn,
                "SELECT i.run_id, i.artifact_ref FROM run_inputs i "
                "JOIN artifacts a ON a.session_id = i.session_id "
                "AND a.artifact_id = i.artifact_ref "
                "WHERE a.session_id = ? AND a.produced_by_job = ? "
                "AND i.run_id != ? "
                "ORDER BY i.artifact_ref, i.run_id LIMIT 11",
                (session_id, run_id, run_id),
            )
            if consumers:
                candidates = tuple(
                    f"{row['run_id']} <- {row['artifact_ref']}" for row in consumers[:10]
                )
                raise SessionStateError(
                    message="cannot abandon a Run whose registered output has downstream consumers",
                    expected="no other Run references an Artifact registered to the abandoned Run",
                    received=str(candidates),
                    location=f"Run {run_id!r} abandonment",
                    repair=AnalysisRepair(
                        kind="environment",
                        action=(
                            "Preserve this Session unchanged and create a new named Session; "
                            "abandoning the producer would invalidate persisted downstream lineage."
                        ),
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id="runtime.sessions",
                        ),
                        candidates=candidates,
                    ),
                )
            conn.execute(
                "DELETE FROM artifacts WHERE session_id = ? AND produced_by_job = ?",
                (session_id, run_id),
            )
            conn.execute(
                "UPDATE runs SET lifecycle = 'failed', finished_at = ?, "
                "failure_json = ?, output_artifact_ref = NULL, output_mode = NULL "
                "WHERE session_id = ? AND run_id = ?",
                (_now_iso(), failure_json, session_id, run_id),
            )

    def get_run(self, session_id: str, run_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            row = self._fetchone(
                conn,
                "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                (session_id, run_id),
            )
        if row is not None:
            self._validate_payload_schema(row, location=f"Run {run_id!r}")
        return row

    def list_runs(self, session_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = self._fetchall(
                conn,
                "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at, run_id",
                (session_id,),
            )
        for row in rows:
            self._validate_payload_schema(row, location=f"Run {row['run_id']!r}")
        return rows

    def page_runs(
        self,
        session_id: str,
        *,
        status: str | None,
        capability_id: str | None,
        limit: int,
        after: tuple[str, str] | None,
    ) -> list[sqlite3.Row]:
        """Return at most ``limit + 1`` newest exact-current Run rows."""
        clauses = ["session_id = ?"]
        params: list[object] = [session_id]
        if status is not None:
            clauses.append("lifecycle = ?")
            params.append(status)
        if capability_id is not None:
            clauses.append("capability_id = ?")
            params.append(capability_id)
        if after is not None:
            clauses.append("(started_at < ? OR (started_at = ? AND run_id < ?))")
            params.extend((after[0], after[0], after[1]))
        params.append(limit + 1)
        with self._connect() as conn:
            rows = self._fetchall(
                conn,
                f"SELECT * FROM runs WHERE {' AND '.join(clauses)} "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                tuple(params),
            )
        for row in rows:
            self._validate_payload_schema(row, location=f"Run {row['run_id']!r}")
        return rows

    def run_input_refs(self, session_id: str, run_id: str) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = self._fetchall(
                conn,
                "SELECT artifact_ref FROM run_inputs WHERE session_id = ? AND run_id = ? "
                "ORDER BY position",
                (session_id, run_id),
            )
        return tuple(str(row["artifact_ref"]) for row in rows)

    def runtime_snapshot(
        self,
        session_id: str,
        *,
        max_records: int | None = None,
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
        """Read Runs, inputs, and Artifacts from one SQLite snapshot."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            if max_records is not None:
                count_row = self._fetchone(
                    conn,
                    "SELECT "
                    "(SELECT COUNT(*) FROM runs WHERE session_id = ?) + "
                    "(SELECT COUNT(*) FROM artifacts WHERE session_id = ?) AS record_count",
                    (session_id, session_id),
                )
                record_count = int(count_row["record_count"]) if count_row is not None else 0
                if record_count > max_records:
                    raise _RuntimeSnapshotTooLargeError(record_count)
            runs = self._fetchall(
                conn,
                "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at, run_id",
                (session_id,),
            )
            inputs = self._fetchall(
                conn,
                "SELECT * FROM run_inputs WHERE session_id = ? ORDER BY run_id, position",
                (session_id,),
            )
            artifacts = self._fetchall(
                conn,
                "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at, artifact_id",
                (session_id,),
            )
        for row in runs:
            self._validate_payload_schema(row, location=f"Run {row['run_id']!r}")
        return runs, inputs, artifacts

    def focused_runtime_snapshot(
        self,
        session_id: str,
        *,
        artifact_ref: str,
        direction: str,
        max_nodes: int,
    ) -> _FocusedRuntimeRows:
        """Read one index-driven bounded Artifact traversal in a single snapshot."""
        selected_runs: dict[str, sqlite3.Row] = {}
        selected_artifacts: dict[str, sqlite3.Row] = {}
        boundary_artifacts: set[str] = set()
        boundary_runs: set[str] = set()
        missing_artifacts: set[str] = set()
        missing_runs: set[str] = set()
        frontier: dict[tuple[str, str], set[str]] = {("artifact", artifact_ref): set()}
        visited: set[tuple[str, str]] = set()
        truncated = False

        with self._connect() as conn:
            conn.execute("BEGIN")

            def artifact_row(ref: str) -> sqlite3.Row | None:
                return self._fetchone(
                    conn,
                    "SELECT * FROM artifacts WHERE session_id = ? AND artifact_id = ?",
                    (session_id, ref),
                )

            def run_row(run_id: str) -> sqlite3.Row | None:
                return self._fetchone(
                    conn,
                    "SELECT * FROM runs WHERE session_id = ? AND run_id = ?",
                    (session_id, run_id),
                )

            row_cache: dict[tuple[str, str], sqlite3.Row | None] = {}

            def runtime_row(kind: str, identity: str) -> sqlite3.Row | None:
                key = (kind, identity)
                if key not in row_cache:
                    row_cache[key] = (
                        artifact_row(identity) if kind == "artifact" else run_row(identity)
                    )
                row = row_cache[key]
                if row is None:
                    if kind == "artifact":
                        missing_artifacts.add(identity)
                    else:
                        missing_runs.add(identity)
                return row

            def timestamp_key(kind: str, row: sqlite3.Row) -> str:
                field = "created_at" if kind == "artifact" else "started_at"
                raw = str(row[field])
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    return raw
                if parsed.tzinfo is None:
                    return raw
                return parsed.astimezone(UTC).isoformat()

            while frontier:
                next_frontier: dict[tuple[str, str], set[str]] = {}
                ranked: list[tuple[str, str, str, sqlite3.Row, set[str]]] = []
                for (kind, identity), owners in frontier.items():
                    row = runtime_row(kind, identity)
                    if row is not None:
                        ranked.append((timestamp_key(kind, row), identity, kind, row, owners))
                ranked.sort(key=lambda item: (item[0], item[1], item[2]))
                for _timestamp, identity, kind, row, owners in ranked:
                    key = (kind, identity)
                    if key in visited:
                        continue
                    visited.add(key)
                    if len(selected_runs) + len(selected_artifacts) >= max_nodes:
                        truncated = True
                        if kind == "artifact":
                            boundary_runs.update(owners)
                        else:
                            boundary_artifacts.update(owners)
                        continue
                    if kind == "artifact":
                        selected_artifacts[identity] = row
                        if direction == "ancestors":
                            producer = row["produced_by_job"]
                            if producer is not None:
                                next_frontier.setdefault(("run", str(producer)), set()).add(
                                    identity
                                )
                        else:
                            consumers = self._fetchall(
                                conn,
                                "SELECT DISTINCT run_id FROM run_inputs "
                                "WHERE session_id = ? AND artifact_ref = ?",
                                (session_id, identity),
                            )
                            for consumer in consumers:
                                next_frontier.setdefault(
                                    ("run", str(consumer["run_id"])), set()
                                ).add(identity)
                    else:
                        self._validate_payload_schema(row, location=f"Run {identity!r}")
                        selected_runs[identity] = row
                        if direction == "ancestors":
                            input_rows = self._fetchall(
                                conn,
                                "SELECT artifact_ref FROM run_inputs "
                                "WHERE session_id = ? AND run_id = ? ORDER BY position",
                                (session_id, identity),
                            )
                            for input_row in input_rows:
                                next_frontier.setdefault(
                                    ("artifact", str(input_row["artifact_ref"])), set()
                                ).add(identity)
                        elif row["lifecycle"] == "succeeded" and row["output_artifact_ref"]:
                            next_frontier.setdefault(
                                ("artifact", str(row["output_artifact_ref"])), set()
                            ).add(identity)
                frontier = {
                    key: owners for key, owners in next_frontier.items() if key not in visited
                }

            run_ids = tuple(selected_runs)
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                inputs = self._fetchall(
                    conn,
                    f"SELECT * FROM run_inputs WHERE session_id = ? "
                    f"AND run_id IN ({placeholders}) ORDER BY run_id, position",
                    (session_id, *run_ids),
                )
            else:
                inputs = []
            for input_row in inputs:
                runtime_row("artifact", str(input_row["artifact_ref"]))
            for run in selected_runs.values():
                if run["lifecycle"] == "succeeded" and run["output_artifact_ref"] is not None:
                    runtime_row("artifact", str(run["output_artifact_ref"]))
            for artifact in selected_artifacts.values():
                if artifact["produced_by_job"] is not None:
                    runtime_row("run", str(artifact["produced_by_job"]))
            non_heads: list[str] = []
            for ref in selected_artifacts:
                consumer_match = self._fetchone(
                    conn,
                    "SELECT 1 FROM run_inputs i JOIN runs r "
                    "ON r.session_id = i.session_id AND r.run_id = i.run_id "
                    "WHERE i.session_id = ? AND i.artifact_ref = ? "
                    "AND r.lifecycle = 'succeeded' LIMIT 1",
                    (session_id, ref),
                )
                if consumer_match is not None:
                    non_heads.append(ref)

        return _FocusedRuntimeRows(
            runs=tuple(selected_runs.values()),
            inputs=tuple(inputs),
            artifacts=tuple(selected_artifacts.values()),
            boundary_artifact_refs=tuple(sorted(boundary_artifacts)),
            boundary_run_ids=tuple(sorted(boundary_runs)),
            non_head_artifact_refs=tuple(sorted(non_heads)),
            missing_artifact_refs=tuple(sorted(missing_artifacts)),
            missing_run_ids=tuple(sorted(missing_runs)),
            truncated=truncated,
        )

    def runtime_recap(self, session_id: str) -> dict[str, object]:
        """Read exact Session runtime counts, heads, and attention in one transaction."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            count_row = self._fetchone(
                conn,
                "SELECT "
                "(SELECT COUNT(*) FROM artifacts WHERE session_id = ? "
                " AND kind NOT IN ('component_frame', 'coverage_frame')) AS artifact_count, "
                "(SELECT COUNT(*) FROM artifacts a WHERE a.session_id = ? "
                " AND a.kind NOT IN ('component_frame', 'coverage_frame') "
                " AND NOT EXISTS ("
                "  SELECT 1 FROM run_inputs i JOIN runs r "
                "  ON r.session_id = i.session_id AND r.run_id = i.run_id "
                "  WHERE i.session_id = a.session_id AND i.artifact_ref = a.artifact_id "
                "  AND r.lifecycle = 'succeeded'"
                " )) AS head_artifact_count, "
                "(SELECT COUNT(*) FROM runs WHERE session_id = ? AND lifecycle = 'succeeded') "
                " AS succeeded_count, "
                "(SELECT COUNT(*) FROM runs WHERE session_id = ? AND lifecycle = 'failed') "
                " AS failed_count, "
                "(SELECT COUNT(*) FROM runs WHERE session_id = ? AND lifecycle = 'incomplete') "
                " AS incomplete_count, "
                "(SELECT COUNT(*) FROM artifacts WHERE session_id = ? "
                " AND kind NOT IN ('component_frame', 'coverage_frame') "
                " AND evidence_status = 'complete') AS evidence_complete_count, "
                "(SELECT COUNT(*) FROM artifacts WHERE session_id = ? "
                " AND kind NOT IN ('component_frame', 'coverage_frame') "
                " AND evidence_status = 'partial') AS evidence_partial_count, "
                "(SELECT COUNT(*) FROM artifacts WHERE session_id = ? "
                " AND kind NOT IN ('component_frame', 'coverage_frame') "
                " AND evidence_status = 'unavailable') AS evidence_unavailable_count, "
                "(SELECT COUNT(*) FROM artifacts WHERE session_id = ?) + "
                "(SELECT COUNT(*) FROM runs WHERE session_id = ?) AS graph_record_count",
                (session_id,) * 10,
            )
            heads = self._fetchall(
                conn,
                "SELECT a.artifact_id FROM artifacts a "
                "WHERE a.session_id = ? "
                "AND a.kind NOT IN ('component_frame', 'coverage_frame') "
                "AND NOT EXISTS ("
                " SELECT 1 FROM run_inputs i JOIN runs r "
                " ON r.session_id = i.session_id AND r.run_id = i.run_id "
                " WHERE i.session_id = a.session_id AND i.artifact_ref = a.artifact_id "
                " AND r.lifecycle = 'succeeded'"
                ") ORDER BY a.created_at DESC, a.artifact_id DESC LIMIT 3",
                (session_id,),
            )
            attention = self._fetchall(
                conn,
                "SELECT run_id FROM runs WHERE session_id = ? "
                "AND lifecycle IN ('failed', 'incomplete') "
                "ORDER BY started_at DESC, run_id DESC LIMIT 3",
                (session_id,),
            )
        assert count_row is not None
        count_values = dict(count_row)
        return {
            **{key: int(value) for key, value in count_values.items()},
            "head_artifact_refs": tuple(str(row["artifact_id"]) for row in heads),
            "attention_run_ids": tuple(str(row["run_id"]) for row in attention),
        }

    # Private Slice-1 compatibility projection for the unreleased public cutover.
    def get_job(self, session_id: str, job_id: str) -> sqlite3.Row | None:
        row = self.get_run(session_id, job_id)
        return row if row is not None and row["lifecycle"] == "succeeded" else None

    def list_jobs(self, session_id: str) -> list[sqlite3.Row]:
        return [row for row in self.list_runs(session_id) if row["lifecycle"] == "succeeded"]

    def delete_job(self, session_id: str, job_id: str) -> None:
        # Legacy rollback paths still call this after undoing Artifact state.
        # Canonical Runs are append-only lifecycle truth and must survive that
        # rollback so the admission context can record FailedRun.
        return None
