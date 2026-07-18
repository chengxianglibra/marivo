"""Fresh schema-v2 evidence-store contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import marivo.analysis.evidence.store as store_module
from marivo.analysis.errors import (
    EvidenceStoreUnavailableError,
    SchemaVersionMismatchError,
    SessionLockedByAnotherProcessError,
)
from marivo.analysis.evidence.store import EXPECTED_SCHEMA_VERSION, open_evidence_store


def test_fresh_store_contains_only_v2_evidence_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    store = open_evidence_store(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == EXPECTED_SCHEMA_VERSION
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert tables == {
                "artifacts",
                "findings",
                "artifact_digests",
                "artifact_issues",
            }
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        store.close()


def test_store_rejects_future_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {EXPECTED_SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaVersionMismatchError):
        open_evidence_store(db_path)


def test_store_rejects_pre_cutover_schema_without_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 1")
    with pytest.raises(SchemaVersionMismatchError, match="requires a fresh v2 evidence store"):
        open_evidence_store(db_path)


def test_store_setup_failure_is_normalized_to_typed_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect = sqlite3.connect

    class FailingSetupConnection:
        def __init__(self) -> None:
            self._inner = real_connect(":memory:", isolation_level=None)
            self.closed = False

        @property
        def row_factory(self):
            return self._inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._inner.row_factory = value

        def execute(self, sql, params=()):
            if "journal_mode" in sql:
                raise sqlite3.OperationalError("attempt to write a readonly database")
            return self._inner.execute(sql, params)

        def close(self) -> None:
            self.closed = True
            self._inner.close()

    failing = FailingSetupConnection()
    monkeypatch.setattr(store_module.sqlite3, "connect", lambda *_args, **_kwargs: failing)

    with pytest.raises(EvidenceStoreUnavailableError, match=r"cannot open judgment\.db"):
        open_evidence_store(tmp_path / "judgment.db")
    assert failing.closed


def test_transaction_rolls_back_all_projection_rows(tmp_path: Path) -> None:
    store = open_evidence_store(tmp_path / "judgment.db")
    try:
        with pytest.raises(RuntimeError), store.transaction(immediate=True) as tx:
            tx.execute(
                """INSERT INTO artifacts
                   (artifact_id, session_id, step_type, artifact_type,
                    artifact_schema_version, subject_payload, lineage_payload,
                    evidence_status, committed_at_us)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("art_1", "sess_1", "compare", "delta_frame", "v2", "{}", "{}", "complete", 1),
            )
            raise RuntimeError("rollback")
        assert store.read().execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    finally:
        store.close()


def test_lock_contention_raises_typed(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    first = open_evidence_store(db_path)
    second = open_evidence_store(db_path, busy_timeout_ms=50)
    try:
        with (
            first.transaction(immediate=True),
            pytest.raises(SessionLockedByAnotherProcessError),
            second.transaction(immediate=True),
        ):
            pass
    finally:
        second.close()
        first.close()
