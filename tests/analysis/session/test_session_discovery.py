"""Current-generation Session discovery preserves independent legacy state."""

import os
import sqlite3
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import SessionNotFoundError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout


def _project_state(root: Path) -> dict[str, bytes | str | None]:
    return {
        str(path.relative_to(root)): (
            str(path.readlink())
            if path.is_symlink()
            else path.read_bytes()
            if path.is_file()
            else None
        )
        for path in root.rglob("*")
    }


def _foreign_entry(root: Path, name: str | None) -> Path | None:
    if name is None:
        return None
    generations = MaterializationLayout(root).generation_dir.parent
    path = generations / name
    if name != "README":
        path /= "session_store.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"preserve-unreadable-legacy-bytes")
    return path


def test_session_discovery_preserves_project_without_current_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for entry in ("v5", "v8", "v11", "README"):
        _foreign_entry(tmp_path, entry)
    before = _project_state(tmp_path)

    page = mv.session.recent(limit=7)
    assert page.items == ()
    assert page.limit == 7
    assert page.has_more is False
    assert page.next_cursor is None
    assert _project_state(tmp_path) == before

    with pytest.raises(IntegrityError):
        mv.session.resume("legacy-session")
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize("existing_session", [False, True])
def test_session_creation_and_history_preserve_foreign_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_session: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.create("first", timezone="UTC") if existing_session else None
    foreign = [_foreign_entry(tmp_path, entry) for entry in ("v5", "v8", "v11", "README")]
    if original is None:
        before = _project_state(tmp_path)
        with pytest.raises(IntegrityError):
            mv.session.create("first", timezone="UTC")
        assert _project_state(tmp_path) == before
        return
    first = mv.session.resume(original.id)
    assert first.id == original.id
    if original is not None:
        assert first.id == original.id
    assert mv.session.resume(first.id).id == first.id
    second = mv.session.create("second", timezone="UTC")

    page = mv.session.recent(limit=1)
    assert [item.id for item in page.items] == [second.id]
    assert page.has_more is True
    assert page.next_cursor is not None
    next_page = mv.session.recent(limit=1, cursor=page.next_cursor)
    assert [item.id for item in next_page.items] == [first.id]
    assert next_page.has_more is False
    assert next_page.next_cursor is None
    assert mv.session.resume(first.id).id == first.id
    assert mv.session.resume(second.id).id == second.id
    with pytest.raises(SessionNotFoundError):
        mv.session.resume("legacy-session")
    assert first._runtime.store.layout.generation == 15
    assert all(
        path is not None and path.read_bytes() == b"preserve-unreadable-legacy-bytes"
        for path in foreign
    )


def test_history_cursor_is_decoded_once_per_public_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.terminal_store import admit
    from marivo.analysis.session import _lazy_runtime_reads

    monkeypatch.chdir(tmp_path)
    mv.session.create("first", timezone="UTC")
    session = mv.session.create("second", timezone="UTC")
    for run_id in ("run_first", "run_second"):
        admit(
            session._runtime.store,
            session.id,
            run_id,
            "result_" + run_id,
        )
        mv.session.resume(session.id)
    cursor = mv.session.recent(limit=1).next_cursor
    assert cursor is not None
    from marivo.analysis._pages import decode_keyset_cursor

    decode = decode_keyset_cursor
    calls = 0

    def counted(value: str) -> tuple[str | int, str]:
        nonlocal calls
        calls += 1
        return decode(value)

    monkeypatch.setattr(_lazy_runtime_reads, "decode_keyset_cursor", counted)
    page = mv.session.recent(limit=1, cursor=cursor)
    assert [item.name for item in page.items] == ["first"]
    assert calls == 1


def test_history_invalid_cursor_precedes_store_lookup_and_preserves_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.store import SessionStore

    monkeypatch.chdir(tmp_path)
    before = _project_state(tmp_path)
    monkeypatch.setattr(
        mv.session, "_existing_store", lambda _root: pytest.fail("history opened a Store")
    )
    monkeypatch.setattr(
        SessionStore, "open_existing", lambda _root: pytest.fail("inspect opened a Store")
    )
    with pytest.raises(mv.errors.SessionStateError) as caught:
        mv.session.recent(cursor="invalid")
    error = caught.value
    assert error.location == "session.recent.cursor"
    assert error.received == "cursor is not a supported keyset encoding"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "session.recent"
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize(
    "state", ["directory", "dangling_symlink", "invalid_bytes", "incomplete_schema", "non_wal"]
)
def test_session_discovery_rejects_invalid_current_store_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    path.parent.mkdir(parents=True)
    if state == "directory":
        path.mkdir()
    elif state == "dangling_symlink":
        path.symlink_to("missing-store.db")
    elif state == "invalid_bytes":
        path.write_bytes(b"invalid-sqlite")
    elif state == "incomplete_schema":
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version=15")
    else:
        from marivo.analysis.materialization.store import SessionStore

        SessionStore(tmp_path)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
    before = _project_state(tmp_path)
    for read in (mv.session.recent,):
        with pytest.raises(IntegrityError) as caught:
            read()
        if state == "incomplete_schema":
            assert caught.value.received == "incomplete v15 schema"
        assert _project_state(tmp_path) == before


def test_session_discovery_reports_store_access_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    before = _project_state(tmp_path)
    lstat = Path.lstat

    def denied(selected: Path) -> os.stat_result:
        if selected == path:
            raise PermissionError("store access denied")
        return lstat(selected)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", denied)
        for read in (mv.session.recent,):
            with pytest.raises(IntegrityError) as caught:
                read()
            assert caught.value.received == "selected Store 15 is unavailable"
    assert _project_state(tmp_path) == before


def test_session_discovery_rejects_incompatible_selected_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=8")
    before = _project_state(tmp_path)
    for read in (mv.session.recent,):
        with pytest.raises(IntegrityError) as caught:
            read()
        assert caught.value.stage == "store_generation"
        assert caught.value.received == "Session Store user_version=8"
    assert _project_state(tmp_path) == before
