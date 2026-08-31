from __future__ import annotations

import sqlite3

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import FrameMetaInvalidError, SchemaVersionMismatchError
from marivo.analysis.session._runs import reconcile_incomplete_runs
from marivo.analysis.session._store import SessionStore
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.shared_fixtures import connect_sales_orders, sales_backends


def test_fresh_store_is_exact_v1_with_runtime_tables(tmp_path) -> None:
    store = SessionStore(project_root=tmp_path)
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"sessions", "runtime_state", "artifacts", "runs", "run_inputs"} <= tables
    assert "jobs" not in tables


@pytest.mark.parametrize("version", [0, 2, 99])
def test_incompatible_existing_store_fails_read_only_without_mutation(tmp_path, version) -> None:
    db_path = tmp_path / ".marivo" / "analysis" / "session_store.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve')")
        connection.execute(f"PRAGMA user_version={version}")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for suffix in ("-wal", "-shm"):
        db_path.with_name(f"{db_path.name}{suffix}").unlink(missing_ok=True)
    before = db_path.read_bytes()
    before_names = sorted(path.name for path in db_path.parent.iterdir())
    with pytest.raises(SchemaVersionMismatchError) as exc_info:
        SessionStore(project_root=tmp_path)
    assert exc_info.value.received == f"Session Store user_version={version}"
    assert "Preserve or move" in exc_info.value.repair.action
    assert db_path.read_bytes() == before
    assert sorted(path.name for path in db_path.parent.iterdir()) == before_names


def test_run_payload_schema_mismatch_fails_without_rewrite(tmp_path) -> None:
    store = SessionStore(project_root=tmp_path)
    row = store.get_or_insert_session(name="schema", question=None, cwd=tmp_path)
    store.begin_run(
        session_id=row["id"],
        run_id="run_bad",
        capability_id="observe",
        analysis_purpose=None,
        arguments=[],
        omitted_argument_names=(),
        input_artifact_refs=(),
        started_at="2026-08-30T00:00:00+00:00",
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET payload_schema = 'marivo.analysis_run/v2' WHERE run_id = 'run_bad'"
        )
    before = store.db_path.read_bytes()
    with pytest.raises(SchemaVersionMismatchError) as exc_info:
        store.validate_session_runtime_schema(str(row["id"]))
    assert exc_info.value.received == "marivo.analysis_run/v2"
    assert store.db_path.read_bytes() == before


@pytest.mark.parametrize("schema", ["analysis-artifact/v12", "analysis-artifact/v99"])
def test_registered_non_v13_artifact_fails_before_sidecar_read(tmp_path, schema) -> None:
    store = SessionStore(project_root=tmp_path)
    row = store.get_or_insert_session(name="schema", question=None, cwd=tmp_path)
    store.record_artifact(
        session_id=row["id"],
        artifact_id="art_old",
        kind="metric_frame",
        path="frames/art_old/data.parquet",
        meta_path="frames/art_old/meta.json",
        content_hash=None,
        produced_by_job=None,
        artifact_schema_version=schema,
    )
    before = store.db_path.read_bytes()
    with pytest.raises(FrameMetaInvalidError) as exc_info:
        store.validate_session_runtime_schema(str(row["id"]))
    assert exc_info.value.received == schema
    assert store.db_path.read_bytes() == before


def test_recovery_registers_committed_artifact_and_completes_incomplete_run(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    connection = connect_sales_orders()
    session = mv.session.get_or_create(
        name="recover-committed-artifact",
        backends=sales_backends(connection),
    )
    frame = session.observe(make_ref("sales.revenue", SemanticKind.METRIC))
    run_id = frame.meta.produced_by_job
    assert run_id is not None

    session._store.delete_artifact(session.id, frame.ref)
    with session._store._connect() as store_connection:
        store_connection.execute(
            "UPDATE runs SET lifecycle = 'incomplete', finished_at = NULL, "
            "output_artifact_ref = NULL, output_mode = NULL, failure_json = NULL "
            "WHERE session_id = ? AND run_id = ?",
            (session.id, run_id),
        )

    reconcile_incomplete_runs(session)

    recovered = session._store.get_artifact(session.id, frame.ref)
    run = session._store.get_run(session.id, run_id)
    assert recovered is not None
    assert recovered["produced_by_job"] == run_id
    assert run is not None
    assert run["lifecycle"] == "succeeded"
    assert run["output_artifact_ref"] == frame.ref
    assert run["output_mode"] == "produced"
    connection.disconnect()
    session_attach._reset_process_state()


def test_recovery_does_not_complete_registered_artifact_without_evidence_marker(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    connection = connect_sales_orders()
    try:
        session = mv.session.get_or_create(
            name="recover-requires-marker",
            backends=sales_backends(connection),
        )
        frame = session.observe(make_ref("sales.revenue", SemanticKind.METRIC))
        run_id = frame.meta.produced_by_job
        assert run_id is not None

        evidence_store = session._evidence_store()
        assert evidence_store is not None
        evidence_connection = evidence_store.read()
        evidence_connection.execute("PRAGMA foreign_keys = OFF")
        evidence_connection.execute(
            "DELETE FROM artifacts WHERE session_id = ? AND artifact_id = ?",
            (session.id, frame.ref),
        )
        evidence_connection.execute("PRAGMA foreign_keys = ON")
        with session._store._connect() as store_connection:
            store_connection.execute(
                "UPDATE runs SET lifecycle = 'incomplete', finished_at = NULL, "
                "output_artifact_ref = NULL, output_mode = NULL, failure_json = NULL "
                "WHERE session_id = ? AND run_id = ?",
                (session.id, run_id),
            )

        reconcile_incomplete_runs(session)

        run = session._store.get_run(session.id, run_id)
        assert run is not None
        assert run["lifecycle"] == "incomplete"
        assert session._store.get_artifact(session.id, frame.ref) is not None
    finally:
        connection.disconnect()
        session_attach._reset_process_state()
