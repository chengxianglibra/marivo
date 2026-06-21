"""SQLite judgment.db schema, migration, WAL, lock, GC, SAVEPOINT helper."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from marivo.analysis.errors import (
    SchemaVersionMismatchError,
    SessionLockedByAnotherProcessError,
)
from marivo.analysis.evidence.store import (
    EXPECTED_SCHEMA_VERSION,
    open_judgment_store,
)


def test_open_creates_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    store = open_judgment_store(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert user_version == EXPECTED_SCHEMA_VERSION
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {
                "artifacts",
                "findings",
                "propositions",
                "assessment_snapshots",
                "assessment_edges",
                "blocking_issues",
                "followups",
            }.issubset(tables)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
    finally:
        store.close()


def test_open_rejects_higher_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {EXPECTED_SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaVersionMismatchError):
        open_judgment_store(db_path)


def test_savepoint_helper_rollback_preserves_outer_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    store = open_judgment_store(db_path)
    try:
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO artifacts(artifact_id, session_id, step_type, "
                "artifact_type, artifact_schema_version, subject_payload, "
                "lineage_payload, evidence_status, committed_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                ("art_1", "sess_1", "compare", "delta_frame", "v1", "{}", "{}", "complete", 1),
            )
            try:
                with tx.savepoint("sp_evidence"):
                    tx.execute(
                        "INSERT INTO findings(finding_id, session_id, artifact_id, "
                        "finding_type, canonical_item_key, subject_payload, "
                        "payload, committed_at_us) VALUES (?,?,?,?,?,?,?,?)",
                        ("fnd_1", "sess_1", "art_1", "delta", "value", "{}", "{}", 1),
                    )
                    raise RuntimeError("simulate seeding failure")
            except RuntimeError:
                pass
        with sqlite3.connect(db_path) as conn:
            artifacts = conn.execute("SELECT artifact_id FROM artifacts").fetchall()
            findings = conn.execute("SELECT finding_id FROM findings").fetchall()
        assert artifacts == [("art_1",)]
        assert findings == []
    finally:
        store.close()


def test_lock_contention_raises_typed(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    store_a = open_judgment_store(db_path)
    try:
        with store_a.transaction(immediate=True), pytest.raises(SessionLockedByAnotherProcessError):
            store_b = open_judgment_store(db_path, busy_timeout_ms=50)
            try:
                with store_b.transaction(immediate=True):
                    pass
            finally:
                store_b.close()
    finally:
        store_a.close()
