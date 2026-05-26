"""create / attach / switch / active / active_or_create / list / archive / delete."""

import ibis
import pytest

import marivo.analysis_py.session.attach as attach
from marivo.analysis_py.errors import (
    DuplicateSessionNameError,
    NoActiveSessionError,
    SessionStateError,
)
from marivo.analysis_py.session.active import (
    read_active_session_name,
    resolve_project_root,
)


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    attach._reset_process_state()
    yield


def _backends():
    def fn():
        return ibis.duckdb.connect(":memory:")

    return {"warehouse": fn}


def test_create_session_writes_index_and_meta(tmp_path):
    s = attach.create(name="demo", question="q", backends=_backends())
    assert s.name == "demo"
    assert (tmp_path / ".marivo" / "analysis" / "index.db").is_file()
    assert s.layout.meta_file.is_file()


def test_create_session_sets_active(tmp_path):
    attach.create(name="demo", backends=_backends())
    assert read_active_session_name(resolve_project_root()) == "demo"


def test_create_session_duplicate_raises(tmp_path):
    attach.create(name="demo", backends=_backends())
    with pytest.raises(DuplicateSessionNameError):
        attach.create(name="demo", backends=_backends())


def test_attach_returns_existing(tmp_path):
    a = attach.create(name="demo", backends=_backends())
    attach._reset_process_state()
    b = attach.attach(name="demo", backends=_backends())
    assert b.id == a.id


def test_attach_unknown_raises(tmp_path):
    with pytest.raises(NoActiveSessionError):
        attach.attach(name="nope")


def test_active_returns_attached_session(tmp_path):
    s = attach.create(name="demo", backends=_backends())
    assert attach.active().id == s.id


def test_active_raises_when_no_active(tmp_path):
    with pytest.raises(NoActiveSessionError):
        attach.active()


def test_active_or_create_returns_existing(tmp_path):
    a = attach.create(name="demo", backends=_backends())
    attach._reset_process_state()
    b = attach.active_or_create(name_hint="ignored_because_active_exists", backends=_backends())
    assert b.id == a.id
    assert b.name == "demo"


def test_active_or_create_creates_when_missing(tmp_path):
    s = attach.active_or_create(name_hint="fresh", backends=_backends())
    assert s.name == "fresh"


def test_switch_changes_active_pointer(tmp_path):
    attach.create(name="a", backends=_backends())
    attach.create(name="b", backends=_backends())
    attach.switch(name="a", backends=_backends())
    assert read_active_session_name(resolve_project_root()) == "a"


def test_list_returns_all_sessions_in_project(tmp_path):
    attach.create(name="a", backends=_backends())
    attach.create(name="b", backends=_backends())
    assert sorted(s.name for s in attach.list_sessions()) == ["a", "b"]


def test_archive_sets_state(tmp_path):
    attach.create(name="demo", backends=_backends())
    attach.archive("demo")
    archived = next(s for s in attach.list_sessions(include_archived=True) if s.name == "demo")
    assert archived.state == "archived"


def test_archive_writing_archived_session_raises_session_state_error(tmp_path):
    attach.create(name="demo", backends=_backends())
    attach.archive("demo")
    with pytest.raises(SessionStateError):
        attach.switch(name="demo", backends=_backends())


def test_archive_marks_cached_session_read_only_for_jobs(tmp_path):
    s = attach.create(name="demo", backends=_backends())
    attach.archive("demo")
    assert s.state == "archived"
    assert attach.active().state == "archived"


def test_delete_removes_session_dir_and_index_row(tmp_path):
    s = attach.create(name="demo", backends=_backends())
    session_dir = s.layout.session_dir
    assert session_dir.is_dir()
    attach.delete("demo")
    assert not session_dir.is_dir()
    assert attach.list_sessions(include_archived=True) == []


def test_create_rejects_both_backends_and_backend_factory(tmp_path):
    with pytest.raises(SessionStateError):
        attach.create(
            name="demo",
            backends={"x": lambda: object()},
            backend_factory=lambda n: object(),
        )


def test_create_read_only_when_no_factory(tmp_path):
    s = attach.create(name="demo", use_profiles=False)
    assert s.is_read_only
