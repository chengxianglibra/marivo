"""Independent SQLite readers verify final v3 publication and immutable history."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from multiprocessing import get_context
from multiprocessing.synchronize import Barrier
from pathlib import Path
from threading import Barrier as ThreadBarrier

import pytest

from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.contracts import ResourceRecord, RunDatasetInput, RunFailure
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore
from marivo.introspection.live.model import LiveHelpTarget
from tests.lazy_materialization_fixtures import descriptor


def _input() -> RunDatasetInput:
    value = descriptor()
    return RunDatasetInput(
        value.definition_fingerprint,
        value.row_contract.shape_id,
        value.row_contract_fingerprint,
        value.row_set_contract_fingerprint,
        ("session.population",),
        ("entity:sales.customers",),
    )


def _admitted(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    store.admit("session", "e" * 64, _input(), run_ref="run")
    return store


def _counts(path: Path) -> tuple[int, ...]:
    with sqlite3.connect(path) as conn:
        return tuple(
            int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "dataset_artifacts",
                "dataset_evidence",
                "findings",
                "analysis_action_run_terminals",
            )
        )


def test_v3_schema_is_strict_normalized_and_durable(tmp_path: Path) -> None:
    old = tmp_path / ".marivo" / "analysis" / "session_store.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"preserved eager generation")
    store = _admitted(tmp_path)
    assert "/generations/v3/" in str(store.db_path)
    assert old.read_bytes() == b"preserved eager generation"
    with store._read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = tuple(
            row for row in conn.execute("PRAGMA table_list") if not row[1].startswith("sqlite_")
        )
        assert len(tables) == 9
        assert all(row[5] == 1 for row in tables)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with store._write() as conn:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert store.incomplete("session")[0].lifecycle == "incomplete"


@pytest.mark.parametrize("version", [1, 2, 4])
def test_existing_wrong_generation_is_not_mutated(tmp_path: Path, version: int) -> None:
    path = tmp_path / ".marivo/analysis/generations/v3/session_store.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("CREATE TABLE preserved(value TEXT)")
    original = path.read_bytes()
    with pytest.raises(IntegrityError):
        SessionStore(tmp_path)
    assert path.read_bytes() == original


def test_success_is_one_normalized_bundle_and_exact_binding(tmp_path: Path) -> None:
    store = _admitted(tmp_path)
    seen = []

    def inspect(point: str) -> None:
        seen.append((point, _counts(store.db_path)))

    result = store.publish("run", "artifact", descriptor(), event=inspect)
    assert all(counts == (0, 0, 0, 0) for point, counts in seen if point != "after_commit")
    assert seen[-1] == ("after_commit", (1, 1, 0, 1))
    assert result.producing_run_ref == "run"
    assert result.evidence.finding_count == 0
    assert store.lookup("session", "e" * 64) == result
    run = store.run("run")
    assert run is not None and run.lifecycle == "succeeded"
    assert store.incomplete("session") == ()
    assert not (store.layout.artifact_dir("session", "artifact") / "meta.json").exists()
    with pytest.raises(IntegrityError, match="already has an Artifact"):
        store.admit("session", "e" * 64, _input())


@pytest.mark.parametrize(
    "point",
    ["insert_artifact", "insert_evidence", "insert_findings", "insert_terminal", "before_commit"],
)
def test_precommit_fault_rolls_back_the_whole_bundle(tmp_path: Path, point: str) -> None:
    store = _admitted(tmp_path)

    def fail(at: str) -> None:
        if at == point:
            raise RuntimeError("injected publication boundary")

    with pytest.raises(RuntimeError, match="injected"):
        store.publish("run", "artifact", descriptor(), event=fail)
    assert _counts(store.db_path) == (0, 0, 0, 0)
    assert store.artifact("artifact") is None
    assert store.incomplete("session")[0].run_ref == "run"


@pytest.mark.runtime
@pytest.mark.parametrize(
    "point", ["insert_artifact", "insert_evidence", "before_commit", "after_commit"]
)
def test_process_exit_preserves_atomic_publication(tmp_path: Path, point: str) -> None:
    store = _admitted(tmp_path)
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import os, sys\n"
            "from pathlib import Path\n"
            "from marivo.analysis.materialization.store import SessionStore\n"
            "from tests.lazy_materialization_fixtures import descriptor\n"
            "def stop(point):\n"
            "    if point == sys.argv[2]:\n"
            "        os._exit(73)\n"
            "SessionStore(Path(sys.argv[1])).publish('run', 'artifact', descriptor(), event=stop)\n"
            "raise AssertionError('publication crash point was not reached')\n",
            str(tmp_path),
            point,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 73, child.stdout + child.stderr
    reopened = SessionStore(tmp_path)
    if point == "after_commit":
        assert _counts(store.db_path) == (1, 1, 0, 1)
        artifact = reopened.artifact("artifact")
        assert artifact is not None and artifact.producing_run_ref == "run"
        assert reopened.incomplete("session") == ()
    else:
        assert _counts(store.db_path) == (0, 0, 0, 0)
        assert reopened.artifact("artifact") is None
        assert reopened.incomplete("session")[0].run_ref == "run"
        reopened.publish("run", "artifact", descriptor())
        assert _counts(store.db_path) == (1, 1, 0, 1)


def test_lost_commit_acknowledgement_preserves_authoritative_success(tmp_path: Path) -> None:
    store = _admitted(tmp_path)

    def fail(point: str) -> None:
        if point == "after_commit":
            raise RuntimeError("lost acknowledgement")

    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        store.publish("run", "artifact", descriptor(), event=fail)
    reopened = SessionStore(tmp_path)
    artifact = reopened.artifact("artifact")
    assert artifact is not None and artifact.producing_run_ref == "run"
    assert _counts(reopened.db_path) == (1, 1, 0, 1)
    with pytest.raises(IntegrityError, match="history cannot be rewritten"):
        reopened.fail(
            "run",
            RunFailure(
                "publication",
                "execution_failed",
                "safe",
                "run",
                "success",
                "error",
                AnalysisRepair(
                    kind="retry",
                    action="recover",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
                ),
            ),
        )


def test_optimized_publication_rejects_output_removed_by_sqlite_trigger(tmp_path: Path) -> None:
    program = """
import sqlite3
import sys
from pathlib import Path

from marivo.analysis.materialization.errors import IntegrityError
from tests.lazy_materialization_fixtures import descriptor
from tests.test_lazy_materialization_store import _admitted, _counts

if sys.flags.optimize != 1:
    raise RuntimeError("expected optimized interpreter")
store = _admitted(Path(sys.argv[1]))
with sqlite3.connect(store.db_path) as connection:
    connection.executescript('''
        CREATE TRIGGER remove_publication AFTER INSERT ON analysis_action_run_terminals
        BEGIN
            DELETE FROM analysis_action_run_terminals WHERE run_ref=NEW.run_ref;
            DELETE FROM dataset_evidence WHERE artifact_ref=NEW.output_artifact_ref;
            DELETE FROM dataset_artifacts WHERE artifact_ref=NEW.output_artifact_ref;
        END;
    ''')
events = []
try:
    store.publish("run", "artifact", descriptor(), event=events.append)
except IntegrityError as error:
    if error.received != "newly published Artifact is absent":
        raise RuntimeError("unexpected publication failure") from error
else:
    raise RuntimeError("incomplete output was accepted")
if "after_commit" in events or _counts(store.db_path) != (0, 0, 0, 0):
    raise RuntimeError("invalid publication escaped transaction rollback")
run = store.run("run")
if run is None or run.lifecycle != "incomplete":
    raise RuntimeError("producer history changed after rejected publication")
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", program, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_reservations_transfer_only_with_publication(tmp_path: Path) -> None:
    store = _admitted(tmp_path)
    resource = ResourceRecord(
        "run",
        "local_storage_staging",
        "local_parquet@v1",
        "nonce",
        "local_owned_path@v1",
        ".marivo/analysis/generations/v3/sessions/session/artifacts/artifact",
    )
    store.reserve(resource)
    with pytest.raises(IntegrityError, match="cleanup obligation"):
        store.publish("run", "artifact", descriptor())
    assert store.resources("session") == (resource,)
    assert _counts(store.db_path) == (0, 0, 0, 0)
    store.publish("run", "artifact", descriptor(), resolved_resources=(resource,))
    assert store.resources("session") == ()


def test_failed_retry_is_new_run_and_terminal_rows_are_not_rewritten(tmp_path: Path) -> None:
    store = _admitted(tmp_path)
    failure = RunFailure(
        "stage_execution",
        "execution_failed",
        "safe failure",
        "run",
        "rows",
        "source stopped",
        AnalysisRepair(
            kind="retry",
            action="Retry the action.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
        ),
    )
    store.fail("run", failure)
    failed = store.run("run")
    assert failed is not None and failed.failure == failure and failed.output_artifact_ref is None
    retry = store.admit("session", "e" * 64, _input(), run_ref="retry")
    assert retry.lifecycle == "incomplete"
    store.publish("retry", "artifact", descriptor())
    assert store.run("run") == failed


def test_cross_session_inputs_preserve_ordered_operand_occurrences(tmp_path: Path) -> None:
    store = _admitted(tmp_path)
    store.publish("run", "artifact", descriptor())
    store.create_session("second", session_ref="second")
    consumed = store.admit(
        "second", "f" * 64, _input(), input_artifact_refs=("artifact", "artifact")
    )
    assert consumed.input_artifact_refs == ("artifact", "artifact")
    artifact = store.artifact("artifact")
    assert artifact is not None and artifact.session_ref == "session"
    with pytest.raises(IntegrityError, match="incomplete Run"):
        store.admit("second", "a" * 64, _input())


def test_known_corrupt_evidence_fails_without_origin_repair(tmp_path: Path) -> None:
    store = _admitted(tmp_path)
    store.publish("run", "artifact", descriptor())
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE dataset_evidence SET evidence_digest=?", ("b" * 64,))
    with pytest.raises(IntegrityError, match="Evidence summary"):
        store.artifact("artifact")
    assert _counts(store.db_path) == (1, 1, 0, 1)


def test_mismatched_admission_cannot_publish(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    store.admit(
        "session", "e" * 64, replace(_input(), row_contract_fingerprint="b" * 64), run_ref="run"
    )
    with pytest.raises(IntegrityError, match="disagree with admission"):
        store.publish("run", "artifact", descriptor())
    assert _counts(store.db_path) == (0, 0, 0, 0)


def test_existing_name_lookup_cannot_activate_without_its_guard(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    first = store.create_session("first", session_ref="first")
    second = store.create_session("second", session_ref="second")
    assert store.create_session("first", question="ignored", session_ref="discarded") == first
    assert store.current() == second


def test_concurrent_schema_creation_resolves_one_complete_generation(tmp_path: Path) -> None:
    start = ThreadBarrier(8)

    def initialize(root: Path) -> SessionStore:
        start.wait(timeout=10)
        return SessionStore(root)

    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = tuple(pool.map(initialize, (tmp_path,) * 8))
    assert len({store.db_path for store in stores}) == 1
    for store in stores:
        with store._read() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _initialize_generations_in_process(root: Path, start: Barrier) -> None:
    for index in range(8):
        start.wait(timeout=15)
        store = SessionStore(root / str(index))
        with store._read() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert (
                len(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
                == 9
            )


def test_processes_create_one_complete_generation_without_thread_lock(tmp_path: Path) -> None:
    context = get_context("spawn")
    start = context.Barrier(4)
    processes = tuple(
        context.Process(target=_initialize_generations_in_process, args=(tmp_path, start))
        for _ in range(4)
    )
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=25)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_initialization_waits_for_wal_transition_lock(tmp_path: Path) -> None:
    path = tmp_path / ".marivo/analysis/generations/v3/session_store.db"
    path.parent.mkdir(parents=True)
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN")
    blocker.execute("SELECT name FROM sqlite_master").fetchall()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(SessionStore, tmp_path)
        try:
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        finally:
            blocker.close()
        assert future.result(timeout=10).db_path == path


def test_existing_generation_opens_readonly_while_writer_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import store as store_module

    store = SessionStore(tmp_path)
    session = store.create_session("current", session_ref="current")

    def forbidden_transition(conn: sqlite3.Connection) -> None:
        raise AssertionError("existing metadata readers cannot initialize or lock the generation")

    monkeypatch.setattr(store_module, "_enable_wal", forbidden_transition)
    with sqlite3.connect(store.db_path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE sessions SET question='uncommitted' WHERE session_ref='current'")
        assert SessionStore(tmp_path).current() == session
