"""SQLite-backed judgment store for analysis evidence runtime."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marivo.analysis.errors import (
    EvidenceStoreUnavailableError,
    MigrationFailedError,
    SchemaVersionMismatchError,
    SessionLockedByAnotherProcessError,
)

EXPECTED_SCHEMA_VERSION = 1

_SCHEMA_V1 = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id              TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  step_type                TEXT NOT NULL,
  artifact_type            TEXT NOT NULL,
  artifact_schema_version  TEXT NOT NULL,
  subject_payload          TEXT NOT NULL,
  lineage_payload          TEXT NOT NULL,
  confidence_scope         TEXT,
  quality_summary          TEXT,
  evidence_status          TEXT NOT NULL,
  frame_path               TEXT,
  frame_sha                TEXT,
  triggered_by_followup    TEXT,
  committed_at_us          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session_type ON artifacts(session_id, step_type);

CREATE TABLE IF NOT EXISTS findings (
  finding_id               TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  artifact_id              TEXT NOT NULL REFERENCES artifacts(artifact_id),
  finding_type             TEXT NOT NULL,
  canonical_item_key       TEXT NOT NULL,
  subject_axis             TEXT,
  subject_payload          TEXT NOT NULL,
  observed_window_start_us INTEGER,
  observed_window_end_us   INTEGER,
  quality_status           TEXT,
  payload                  TEXT NOT NULL,
  artifact_schema_version  TEXT,
  extractor_version        TEXT,
  committed_at_us          INTEGER NOT NULL,
  UNIQUE (artifact_id, finding_type, canonical_item_key)
);
CREATE INDEX IF NOT EXISTS idx_findings_session_type ON findings(session_id, finding_type);

CREATE TABLE IF NOT EXISTS propositions (
  proposition_id     TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  proposition_type   TEXT NOT NULL,
  origin_kind        TEXT NOT NULL,
  derivation_version TEXT NOT NULL,
  subject_key        TEXT NOT NULL,
  payload            TEXT NOT NULL,
  seed_finding_refs  TEXT NOT NULL,
  created_at_us      INTEGER NOT NULL,
  UNIQUE (session_id, proposition_id)
);
CREATE INDEX IF NOT EXISTS idx_propositions_session_type ON propositions(session_id, proposition_type);
CREATE INDEX IF NOT EXISTS idx_propositions_subject ON propositions(session_id, subject_key);

CREATE TABLE IF NOT EXISTS assessment_snapshots (
  snapshot_id      TEXT PRIMARY KEY,
  proposition_id   TEXT NOT NULL REFERENCES propositions(proposition_id),
  session_id       TEXT NOT NULL,
  supersedes_id    TEXT,
  status           TEXT NOT NULL,
  confidence       REAL,
  confidence_basis TEXT,
  payload          TEXT NOT NULL,
  created_at_us    INTEGER NOT NULL,
  is_latest        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_assess_latest ON assessment_snapshots(proposition_id, is_latest);

CREATE TABLE IF NOT EXISTS assessment_edges (
  snapshot_id  TEXT NOT NULL REFERENCES assessment_snapshots(snapshot_id),
  finding_id   TEXT NOT NULL REFERENCES findings(finding_id),
  role         TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, finding_id, role)
);

CREATE TABLE IF NOT EXISTS blocking_issues (
  issue_id           TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  artifact_id        TEXT NOT NULL REFERENCES artifacts(artifact_id),
  kind               TEXT NOT NULL,
  severity           TEXT NOT NULL,
  payload            TEXT NOT NULL,
  resolved_by_step_id TEXT,
  created_at_us      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocking_issues_session_kind ON blocking_issues(session_id, kind);
CREATE INDEX IF NOT EXISTS idx_blocking_issues_artifact ON blocking_issues(artifact_id);

CREATE TABLE IF NOT EXISTS followups (
  followup_id          TEXT PRIMARY KEY,
  session_id           TEXT NOT NULL,
  source_artifact_id   TEXT NOT NULL REFERENCES artifacts(artifact_id),
  category             TEXT NOT NULL,
  source_issue_id      TEXT REFERENCES blocking_issues(issue_id),
  operator             TEXT,
  payload              TEXT NOT NULL,
  executed_step_id     TEXT,
  created_at_us        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followups_session ON followups(session_id);
CREATE INDEX IF NOT EXISTS idx_followups_source ON followups(source_artifact_id);
"""


@dataclass
class _Transaction:
    conn: sqlite3.Connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Any) -> sqlite3.Cursor:
        return self.conn.executemany(sql, seq_of_params)

    @contextmanager
    def savepoint(self, name: str) -> Iterator[_Transaction]:
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield self
        except BaseException:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.conn.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self.conn.execute(f"RELEASE SAVEPOINT {name}")


class JudgmentStore:
    """Per-session SQLite store for evidence artifacts, findings, and propositions."""

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
                    details={"db_path": str(self.db_path)},
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
        """Return the underlying connection for read-only queries."""
        return self._conn

    def close(self) -> None:
        self._conn.close()


def open_judgment_store(db_path: Path, *, busy_timeout_ms: int = 5000) -> JudgmentStore:
    """Open or create a judgment.db at *db_path* with WAL mode and schema init."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=busy_timeout_ms / 1000)
    except sqlite3.OperationalError as exc:
        raise EvidenceStoreUnavailableError(
            message=f"cannot open judgment.db at {db_path}",
            details={"db_path": str(db_path), "cause": str(exc)},
        ) from exc

    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    user_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_version == 0:
        try:
            conn.executescript(_SCHEMA_V1)
            conn.execute(f"PRAGMA user_version = {EXPECTED_SCHEMA_VERSION}")
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise MigrationFailedError(
                message=f"failed to initialize schema at {db_path}",
                details={"db_path": str(db_path), "cause": str(exc)},
            ) from exc
    elif user_version > EXPECTED_SCHEMA_VERSION:
        conn.close()
        raise SchemaVersionMismatchError(
            message=(
                f"db schema version {user_version} is newer than expected "
                f"{EXPECTED_SCHEMA_VERSION}; refusing to open"
            ),
            details={"got": user_version, "expected": EXPECTED_SCHEMA_VERSION},
        )
    elif user_version < EXPECTED_SCHEMA_VERSION:
        conn.close()
        raise MigrationFailedError(
            message=f"no migration registered for v{user_version} -> v{EXPECTED_SCHEMA_VERSION}",
            details={"got": user_version, "expected": EXPECTED_SCHEMA_VERSION},
        )

    return JudgmentStore(conn, db_path)


def run_startup_gc(store: JudgmentStore, frames_dir: Path) -> None:
    """Delete .tmp orphans and frame dirs not referenced by judgment.db."""
    if not frames_dir.is_dir():
        return
    referenced: set[str] = {
        row[0] for row in store.read().execute("SELECT artifact_id FROM artifacts").fetchall()
    }
    for child in frames_dir.iterdir():
        if not child.is_dir():
            continue
        # Remove any .tmp files inside the directory
        for tmp in child.rglob("*.tmp"):
            tmp.unlink(missing_ok=True)
        # Remove orphan directories not referenced by any artifact
        if child.name not in referenced:
            shutil.rmtree(child, ignore_errors=True)


__all__ = [
    "EXPECTED_SCHEMA_VERSION",
    "JudgmentStore",
    "open_judgment_store",
    "run_startup_gc",
]
