"""Current-generation Session discovery preserves independent legacy state."""

import os
import sqlite3
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import SessionNotFoundError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.session._lazy_read_model import SessionSummaryPage
from tests.support.documentation import _example


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


@pytest.mark.parametrize("entry", [None, "v5", "v8", "README"])
def test_session_discovery_preserves_project_without_current_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str | None
) -> None:
    monkeypatch.chdir(tmp_path)
    _foreign_entry(tmp_path, entry)
    before = _project_state(tmp_path)

    assert mv.session.current() is None
    page = mv.session.recent(limit=7)
    assert page.items == ()
    assert page.limit == 7
    assert page.has_more is False
    assert page.next_cursor is None
    assert _project_state(tmp_path) == before

    with pytest.raises(IntegrityError):
        mv.session.resume("legacy-session", by="id")
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize("entry", [None, "v5", "v8", "README"])
@pytest.mark.parametrize("existing_session", [False, True])
def test_session_creation_and_history_preserve_foreign_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str | None,
    existing_session: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    original = (
        mv.session.get_or_create("first", report_timezone="UTC") if existing_session else None
    )
    foreign = _foreign_entry(tmp_path, entry)
    first = mv.session.get_or_create("first", report_timezone="UTC")
    if original is not None:
        assert first.id == original.id
    assert mv.session.get_or_create("first").id == first.id
    second = mv.session.get_or_create("second", report_timezone="UTC")

    current = mv.session.current()
    assert current is not None and current.id == second.id
    page = mv.session.recent(limit=1)
    assert [item.id for item in page.items] == [second.id]
    assert page.has_more is True
    assert page.next_cursor is not None
    next_page = mv.session.recent(limit=1, cursor=page.next_cursor)
    assert [item.id for item in next_page.items] == [first.id]
    assert next_page.has_more is False
    assert next_page.next_cursor is None
    assert mv.session.resume(first.id, by="id").id == first.id
    assert mv.session.resume("second", by="name").id == second.id
    with pytest.raises(SessionNotFoundError):
        mv.session.resume("legacy-session", by="id")
    assert first._runtime.store.layout.generation == 9
    if foreign is not None:
        assert foreign.read_bytes() == b"preserve-unreadable-legacy-bytes"


@pytest.mark.parametrize(
    ("limit", "cursor", "parameter"),
    [(0, None, "limit"), (101, None, "limit"), (True, None, "limit"), (7, "invalid", "cursor")],
)
def test_missing_store_history_validates_arguments_without_creating_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
    cursor: str | None,
    parameter: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    _foreign_entry(tmp_path, "v5")
    before = _project_state(tmp_path)
    with pytest.raises(mv.errors.SessionStateError, match=f"session.recent.{parameter}"):
        mv.session.recent(limit=limit, cursor=cursor)
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize("entry", ["current", "recent"])
@pytest.mark.parametrize(
    "state", ["directory", "dangling_symlink", "invalid_bytes", "incomplete_schema", "non_wal"]
)
def test_session_discovery_rejects_invalid_current_store_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, state: str
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
            connection.execute("PRAGMA user_version=9")
    else:
        from marivo.analysis.materialization.store import SessionStore

        SessionStore(tmp_path)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
    before = _project_state(tmp_path)
    with pytest.raises(IntegrityError):
        if entry == "current":
            mv.session.current()
        else:
            mv.session.recent()
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize("entry", ["current", "recent"])
def test_session_discovery_reports_store_access_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
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
        with pytest.raises(IntegrityError) as caught:
            if entry == "current":
                mv.session.current()
            else:
                mv.session.recent()
        assert caught.value.received == "selected Store 9 is unavailable"
    assert _project_state(tmp_path) == before


@pytest.mark.parametrize("entry", ["current", "recent"])
@pytest.mark.parametrize("version", [0, 5, 8])
def test_session_discovery_rejects_incompatible_selected_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, version: int
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = _project_state(tmp_path)
    with pytest.raises(IntegrityError) as caught:
        if entry == "current":
            mv.session.current()
        else:
            mv.session.recent()
    assert caught.value.stage == "store_generation"
    assert caught.value.received == f"Session Store user_version={version}"
    assert _project_state(tmp_path) == before


def test_documented_session_discovery_executes_with_preserved_legacy_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    foreign = _foreign_entry(tmp_path, "v5")
    namespace: dict[str, object] = {}
    source = _example("en", "session-discovery")
    exec(compile(source, "session-discovery-example", "exec"), namespace)
    assert namespace["current"] is None
    history = namespace["history"]
    assert isinstance(history, SessionSummaryPage)
    assert history.items == () and history.limit == 5
    assert history.has_more is False and history.next_cursor is None
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    assert session.name == "revenue-review"
    assert foreign is not None and foreign.read_bytes() == b"preserve-unreadable-legacy-bytes"
