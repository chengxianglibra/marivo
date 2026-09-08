"""v3 Session history selection, snapshot counts, and read-only continuation."""

import sqlite3
from pathlib import Path

import pytest

from marivo.analysis.errors import SessionNotFoundError
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session._lazy_history import inspect, recent
from marivo.analysis.session._lazy_runtime_reads import recap
from tests.lazy_runtime_read_fixtures import input_value, publish


def test_session_recency_ties_and_bounded_inspection(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    for name in ("a", "b", "c"):
        store.create_session(name, session_ref=name)
    publish(store, "first", "one", session_ref="c")
    publish(store, "second", "two", session_ref="c")
    with store._write() as conn:
        conn.execute(
            "UPDATE sessions SET created_at='2026-09-08T00:00:00.000000Z',updated_at='2026-09-08T00:00:00.000000Z'"
        )
    first = recent(store, limit=2)
    assert [value.id for value in first] == ["c", "b"]
    assert first.has_more
    assert [value.id for value in recent(store, limit=2, cursor=first.next_cursor)] == ["a"]
    selected = inspect(store, "c", run_limit=1)
    assert selected.summary.run_count == 2 and selected.summary.artifact_count == 2
    assert selected.runs.has_more and selected.runs.items[0].run_id == "second"
    assert (
        inspect(store, "c", run_limit=1, run_cursor=selected.runs.next_cursor).runs.items[0].run_id
        == "first"
    )


def test_history_sentinel_is_not_decoded_and_not_found_candidates_are_bounded(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("older", session_ref="older")
    store.create_session("newer", session_ref="newer")
    with store._write() as conn:
        conn.execute("UPDATE sessions SET created_at='bad' WHERE session_ref='older'")
    assert recent(store, limit=1).items[0].name == "newer"
    with pytest.raises(SessionNotFoundError) as error:
        inspect(store, "missing")
    assert error.value.repair is not None
    assert set(error.value.repair.candidates) == {"older", "newer"}


def test_inspection_holds_one_snapshot_for_counts_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "first", "one")
    original = store._session
    changed = False

    def racing(row: sqlite3.Row) -> SessionRecord:
        nonlocal changed
        value = original(row)
        if not changed:
            changed = True
            publish(SessionStore(tmp_path), "second", "two")
        return value

    monkeypatch.setattr(store, "_session", racing)
    selected = inspect(store, "test")
    assert selected.summary.run_count == 1
    assert [run.run_id for run in selected.runs] == ["first"]


def test_busy_and_incomplete_sessions_allow_history_without_activation(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    store.admit("session", "pending-key", input_value(), run_ref="pending")
    before = store.session("session")
    with store._write() as conn:
        conn.execute("UPDATE sessions SET question='uncommitted'")
        assert inspect(store, "test").summary.question is None
        assert recent(store).items[0].run_count == 1
        assert recap(store, "session").incomplete_run_count == 1
    after = store.session("session")
    assert before is not None and after is not None
    assert after.updated_at == before.updated_at


def test_existing_store_reads_work_under_writer_guard_and_readonly_filesystem(
    tmp_path: Path,
) -> None:
    from marivo.analysis.materialization.writer_guard import session_writer_guard
    from marivo.analysis.session._lazy_graph import graph

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    publish(store, "run", "artifact")
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        cold = SessionStore.open_existing(tmp_path)
        assert inspect(cold, "test").summary.run_count == 1
        assert len(graph(cold, "session").runs) == 1
    # The database is cleanly checkpointed before removing filesystem write bits.
    with store._write() as conn:
        conn.execute("SELECT 1")
    files = [path for path in store.layout.generation_dir.rglob("*") if path.is_file()]
    directories = [
        store.layout.generation_dir,
        *(path for path in store.layout.generation_dir.rglob("*") if path.is_dir()),
    ]
    original_modes = {path: path.stat().st_mode & 0o777 for path in (*files, *directories)}
    before = store.db_path.read_bytes()
    try:
        for path in files:
            path.chmod(0o444)
        for path in directories:
            path.chmod(0o555)
        readonly = SessionStore.open_existing(tmp_path)
        assert recent(readonly).items[0].run_count == 1
        assert inspect(readonly, "test").runs.items[0].run_id == "run"
        assert recap(readonly, "session").artifact_count == 1
        assert not graph(readonly, "session").truncated
        assert store.db_path.read_bytes() == before
    finally:
        for path, mode in original_modes.items():
            path.chmod(mode)


def test_readonly_store_never_ignores_existing_wal(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute("UPDATE sessions SET question='committed in WAL'")
        connection.commit()
        wal = Path(str(store.db_path) + "-wal")
        assert wal.is_file() and wal.stat().st_size > 0
        files = [path for path in store.db_path.parent.iterdir() if path.is_file()]
        modes = {path: path.stat().st_mode & 0o777 for path in (*files, store.db_path.parent)}
        try:
            for path in files:
                path.chmod(0o444)
            store.db_path.parent.chmod(0o555)
            assert store._readonly_uri()[1] is False
            assert (
                inspect(SessionStore.open_existing(tmp_path), "test").summary.question
                == "committed in WAL"
            )
        finally:
            for path, mode in modes.items():
                path.chmod(mode)
    finally:
        connection.close()


def test_ordinary_read_connection_keeps_wal_coordination_and_store_authority(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    before = store.db_path.read_bytes()
    assert store._readonly_uri()[1] is False
    assert recent(SessionStore.open_existing(tmp_path)).items[0].id == "session"
    assert store.db_path.read_bytes() == before
    # SQLite can create WAL/SHM coordination files for mode=ro on a writable generation.
    assert {path.name for path in store.db_path.parent.iterdir()} <= {
        "session_store.db",
        "session_store.db-wal",
        "session_store.db-shm",
    }


def test_existing_store_unavailable_diagnostic_has_no_native_exception_context(
    tmp_path: Path,
) -> None:
    from marivo.analysis.materialization.errors import IntegrityError

    store = SessionStore(tmp_path)
    store.db_path.write_bytes(b"invalid database")
    with pytest.raises(IntegrityError) as caught:
        SessionStore.open_existing(tmp_path)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("items", []),
        ("items", (object(),)),
        ("limit", True),
        ("has_more", 1),
        ("next_cursor", 3),
        ("next_cursor", ""),
    ],
)
def test_pages_validate_exact_immutable_state(field: str, value: object) -> None:
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.session._lazy_read_model import RunPage, SessionSummaryPage

    for page in (
        RunPage(items=(), limit=1, has_more=False, next_cursor=None),
        SessionSummaryPage(items=(), limit=1, has_more=False, next_cursor=None),
    ):
        object.__setattr__(page, field, value)
        with pytest.raises(IntegrityError):
            page.__post_init__()


def test_pages_reject_new_attributes() -> None:
    from dataclasses import FrozenInstanceError

    from marivo.analysis.session._lazy_read_model import RunPage, SessionSummaryPage

    unexpected_field = "unexpected"
    for page in (
        RunPage(items=(), limit=1, has_more=False, next_cursor=None),
        SessionSummaryPage(items=(), limit=1, has_more=False, next_cursor=None),
    ):
        with pytest.raises((FrozenInstanceError, TypeError)):
            setattr(page, unexpected_field, 1)


@pytest.mark.parametrize(
    "field,value",
    [("attention_run_ids", []), ("attention_run_ids", (1,)), ("overall_graph_available", 1)],
)
def test_recap_requires_typed_attention_and_availability(
    tmp_path: Path, field: str, value: object
) -> None:
    from marivo.analysis.materialization.errors import IntegrityError

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    selected = recap(store, "session")
    object.__setattr__(selected, field, value)
    with pytest.raises(IntegrityError):
        selected.__post_init__()


@pytest.mark.parametrize("field", ["summary", "runs"])
def test_inspection_requires_exact_nested_types(tmp_path: Path, field: str) -> None:
    from marivo.analysis.materialization.errors import IntegrityError

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    selected = inspect(store, "test")
    object.__setattr__(selected, field, object())
    with pytest.raises(IntegrityError):
        selected.__post_init__()


@pytest.mark.parametrize(
    "operation,parameter",
    [
        ("recent", "limit"),
        ("recent", "cursor"),
        ("inspect", "run_limit"),
        ("inspect", "run_cursor"),
    ],
)
def test_history_argument_repairs_are_owned_safe_and_precede_store_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, parameter: str
) -> None:
    from marivo._compat import Never
    from marivo.analysis.errors import SessionStateError

    store = SessionStore(tmp_path)
    canary = "history-cursor-secret"

    def forbidden() -> Never:
        pytest.fail("Invalid history arguments must not open the Store")

    monkeypatch.setattr(store, "_read", forbidden)
    with pytest.raises(SessionStateError) as caught:
        if operation == "recent":
            recent(
                store,
                limit=0 if parameter == "limit" else 20,
                cursor=canary if parameter == "cursor" else None,
            )
        else:
            inspect(
                store,
                "test",
                run_limit=0 if parameter == "run_limit" else 5,
                run_cursor=canary if parameter == "run_cursor" else None,
            )
    error = caught.value
    assert error.expected and error.received and error.repair is not None
    assert error.location == f"session.{operation}.{parameter}"
    assert error.repair.help_target.canonical_id == f"session.{operation}"
    assert error.__cause__ is None and error.__context__ is None
    assert canary not in str(error) and len(str(error).encode()) < 2048
    assert "next_cursor" in error.repair.action


def test_selected_history_row_invariant_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.store import _one
    from marivo.analysis.session import _lazy_history

    store = SessionStore(tmp_path)
    store.create_session("test", session_ref="session")
    original = _one

    def missing_selected(
        conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Row | None:
        if sql == "SELECT * FROM sessions WHERE session_ref=?":
            return None
        return original(conn, sql, parameters)

    monkeypatch.setattr(_lazy_history, "_one", missing_selected)
    with pytest.raises(IntegrityError, match="same snapshot") as caught:
        recent(store)
    assert caught.value.expected and caught.value.received and caught.value.repair
