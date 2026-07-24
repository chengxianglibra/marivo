"""SQLite persistence for typed findings, digests, and artifact-local issues."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marivo.analysis.errors import (
    EvidenceStoreUnavailableError,
    SchemaVersionMismatchError,
    SessionLockedByAnotherProcessError,
)

EXPECTED_SCHEMA_VERSION = 4

_SCHEMA_V4 = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id              TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  step_type                TEXT NOT NULL,
  artifact_type            TEXT NOT NULL,
  artifact_schema_version  TEXT NOT NULL,
  subject_payload          TEXT NOT NULL,
  lineage_payload          TEXT NOT NULL,
  analysis_scope           TEXT,
  quality_summary          TEXT,
  evidence_status          TEXT NOT NULL,
  frame_path               TEXT,
  frame_sha                TEXT,
  committed_at_us          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session_commit
  ON artifacts(session_id, committed_at_us DESC, artifact_id DESC);

CREATE TABLE IF NOT EXISTS findings (
  finding_id               TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  artifact_id              TEXT NOT NULL REFERENCES artifacts(artifact_id),
  finding_type             TEXT NOT NULL,
  epistemic_kind           TEXT NOT NULL,
  canonical_item_key       TEXT NOT NULL,
  subject_axis             TEXT,
  subject_payload          TEXT NOT NULL,
  observed_window_payload  TEXT,
  quality_status           TEXT,
  value_kind               TEXT NOT NULL,
  value_payload            TEXT NOT NULL,
  derivation_payload       TEXT NOT NULL,
  source_refs_payload      TEXT NOT NULL,
  artifact_schema_version  TEXT NOT NULL,
  extractor_version        TEXT NOT NULL,
  committed_at_us          INTEGER NOT NULL,
  UNIQUE (artifact_id, finding_type, canonical_item_key)
);
CREATE INDEX IF NOT EXISTS idx_findings_session_commit
  ON findings(session_id, committed_at_us DESC, finding_id DESC);
CREATE INDEX IF NOT EXISTS idx_findings_artifact
  ON findings(artifact_id, finding_id);

CREATE TABLE IF NOT EXISTS artifact_digests (
  artifact_id       TEXT PRIMARY KEY REFERENCES artifacts(artifact_id),
  session_id        TEXT NOT NULL,
  operator          TEXT NOT NULL,
  subject_key       TEXT NOT NULL,
  digest_payload    TEXT NOT NULL,
  fingerprint       TEXT NOT NULL,
  committed_at_us   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_digests_session_commit
  ON artifact_digests(session_id, committed_at_us DESC, artifact_id DESC);

CREATE TABLE IF NOT EXISTS artifact_issues (
  issue_id          TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  artifact_id       TEXT NOT NULL REFERENCES artifacts(artifact_id),
  kind              TEXT NOT NULL,
  severity          TEXT NOT NULL,
  issue_payload     TEXT NOT NULL,
  created_at_us     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifact_issues_artifact
  ON artifact_issues(artifact_id, issue_id);
"""


@dataclass
class _Transaction:
    conn: sqlite3.Connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Any) -> sqlite3.Cursor:
        return self.conn.executemany(sql, seq_of_params)


class EvidenceStore:
    """Per-session store for immutable typed evidence projections."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self._conn = conn
        self.db_path = db_path

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[_Transaction]:
        try:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise SessionLockedByAnotherProcessError(
                    message=f"judgment.db locked: {self.db_path}",
                    context={"db_path": str(self.db_path)},
                ) from exc
            raise
        tx = _Transaction(self._conn)
        try:
            yield tx
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def read(self) -> sqlite3.Connection:
        """Return the connection used by bounded read adapters."""
        return self._conn

    def close(self) -> None:
        self._conn.close()


def _initialize_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V4)
    conn.execute(f"PRAGMA user_version = {EXPECTED_SCHEMA_VERSION}")


def open_evidence_store(db_path: Path, *, busy_timeout_ms: int = 5000) -> EvidenceStore:
    """Open or create the project-local ``judgment.db`` evidence store."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_store = not db_path.exists()
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        user_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version == EXPECTED_SCHEMA_VERSION or (user_version == 0 and is_new_store):
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
        if user_version == 0 and is_new_store:
            _initialize_v4(conn)
            user_version = EXPECTED_SCHEMA_VERSION
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            conn.close()
        raise EvidenceStoreUnavailableError(
            message=f"cannot open judgment.db at {db_path}",
            context={"db_path": str(db_path), "cause": str(exc)},
        ) from exc
    if user_version != EXPECTED_SCHEMA_VERSION:
        conn.close()
        raise SchemaVersionMismatchError(
            message=(
                f"judgment.db schema version {user_version} is unsupported; "
                f"this release requires a fresh v{EXPECTED_SCHEMA_VERSION} evidence store"
            ),
            hint="Remove the incompatible analysis session and run the analysis again.",
            context={"got": user_version, "expected": EXPECTED_SCHEMA_VERSION},
        )
    return EvidenceStore(conn, db_path)


__all__ = ["EXPECTED_SCHEMA_VERSION", "EvidenceStore", "open_evidence_store"]
