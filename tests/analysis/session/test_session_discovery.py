"""Current-generation Session discovery preserves independent legacy state."""

import os
import sqlite3
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import SessionNotFoundError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.store import SessionStore


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

    with pytest.warns(UserWarning, match="Older Session Stores"):
        page = mv.session.recent(limit=7)
    assert page.items == ()
    assert page.limit == 7
    assert page.has_more is False
    assert page.next_cursor is None
    assert _project_state(tmp_path) == before

    for read in (mv.session.resume, mv.session.inspect):
        with pytest.raises(SessionNotFoundError) as caught:
            read("legacy-session")
        assert "may belong" in caught.value.message
        assert "ownership has not been verified" in caught.value.message
        assert caught.value.repair is not None
        assert caught.value.repair.candidates == ()
        assert "v8" in caught.value.repair.action
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize("existing_session", [False, True])
def test_session_creation_and_history_preserve_foreign_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_session: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create("first", timezone="UTC") if existing_session else None
    foreign = [_foreign_entry(tmp_path, entry) for entry in ("v5", "v8", "v11", "README")]
    if original is None:
        with pytest.warns(UserWarning, match="Older Session Stores"):
            original = mv.session.get_or_create("first", timezone="UTC")
    first = mv.session.resume(original.id)
    assert first.id == original.id
    assert mv.session.resume(first.id).id == first.id
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        second = mv.session.get_or_create("second", timezone="UTC")
    assert len(notices) == int(existing_session)

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
    assert first._runtime.store.layout.generation == 16
    assert all(
        path is not None and path.read_bytes() == b"preserve-unreadable-legacy-bytes"
        for path in foreign
    )


def test_old_generation_notices_follow_numeric_paths_without_database_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for entry in ("v10", "v8", "v9", "vunknown", "README"):
        _foreign_entry(tmp_path, entry)
    legacy = tmp_path / ".marivo/analysis/generations/v7/store.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"unreadable database")
    before = _project_state(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("old-generation discovery opened a database")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.warns(UserWarning) as notices:
        assert mv.session.recent().items == ()
    text = str(notices[0].message)
    assert text.index("v7 (") < text.index("v8 (") < text.index("v9 (") < text.index("v10 (")
    assert "store.sqlite3" in text and "file mtime" in text
    assert "last business write time" in text
    assert "vunknown" not in text and "README" not in text
    assert _project_state(tmp_path) == before


def test_notices_deduplicate_across_calls_but_follow_changed_paths_and_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    old = _foreign_entry(first, "v8")
    _foreign_entry(second, "v8")
    assert old is not None
    monkeypatch.chdir(first)
    with pytest.warns(UserWarning, match="v8"):
        session = mv.session.get_or_create("current")
    os.utime(old, (1, 1))
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        assert mv.session.recent().items[0].id == session.id
        assert mv.session.get_or_create("current").id == session.id
    assert notices == []

    _foreign_entry(first, "v9")
    with pytest.warns(UserWarning, match="v9"):
        mv.session.recent()
    old.unlink()
    with pytest.warns(UserWarning) as notices:
        mv.session.recent()
    assert "v8 (" not in str(notices[0].message)
    (old.parent.parent / "v9/session_store.db").unlink()
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        mv.session.recent()
    assert notices == []
    monkeypatch.chdir(second)
    with pytest.warns(UserWarning, match="v8"):
        mv.session.recent()
    assert not MaterializationLayout(second).store_db.exists()


@pytest.mark.parametrize("has_current", [False, True])
def test_missing_session_guidance_does_not_claim_old_id_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_current: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("current") if has_current else None
    for old_present in (False, True):
        if old_present:
            _foreign_entry(tmp_path, "v8")
        before = _project_state(tmp_path)
        for read in (mv.session.resume, mv.session.inspect):
            with pytest.raises(SessionNotFoundError) as caught:
                read("unknown-id")
            error = caught.value
            assert "current v16 Store" in error.message
            assert ("may belong" in error.message) is old_present
            assert error.repair is not None
            assert error.repair.candidates == (() if session is None else (session.id,))
            assert ("v8 (" in error.repair.action) is old_present
        assert _project_state(tmp_path) == before


def test_concurrent_current_store_creation_preserves_old_generation(tmp_path: Path) -> None:
    old = _foreign_entry(tmp_path, "v8")
    assert old is not None
    before = _project_state(old.parent)
    barrier = Barrier(4)

    def create(_index: int) -> Path:
        barrier.wait(timeout=10)
        return SessionStore(tmp_path).db_path

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = tuple(pool.map(create, range(4)))
    store = SessionStore.open_existing(tmp_path)
    assert paths == (store.db_path,) * 4
    assert _project_state(old.parent) == before
    assert not tuple(store.db_path.parent.glob("store-init-*"))


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
            connection.execute("PRAGMA user_version=16")
    else:
        from marivo.analysis.materialization.store import SessionStore

        SessionStore(tmp_path)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
    before = _project_state(tmp_path)
    for read in (
        mv.session.recent,
        lambda: mv.session.get_or_create("new"),
        lambda: mv.session.resume("missing"),
        lambda: mv.session.inspect("missing"),
    ):
        with pytest.raises(IntegrityError) as caught:
            read()
        if state == "incomplete_schema":
            assert caught.value.received == "incomplete v16 schema"
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
            assert caught.value.received == "selected Store 16 is unavailable"
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
