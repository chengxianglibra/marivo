"""session lifecycle helpers."""

import ibis
import pytest

import marivo.analysis.session.attach as attach
from marivo.analysis.errors import (
    DuplicateSessionNameError,
    NoActiveSessionError,
    SessionStateError,
)
from marivo.analysis.session.active import (
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
    assert s._layout.meta_file.is_file()


def test_create_session_sets_active(tmp_path):
    attach.create(name="demo", backends=_backends())
    assert read_active_session_name(resolve_project_root()) == "demo"


def test_create_session_duplicate_raises(tmp_path):
    attach.create(name="demo", backends=_backends())
    with pytest.raises(DuplicateSessionNameError):
        attach.create(name="demo", backends=_backends())


def test_get_or_create_creates_missing_session(tmp_path):
    s = attach.get_or_create(name="demo", question="q", backends=_backends())

    assert s.name == "demo"
    assert s.question == "q"
    assert read_active_session_name(resolve_project_root()) == "demo"


def test_get_or_create_attaches_existing_session_and_sets_active(tmp_path):
    a = attach.create(name="demo", backends=_backends())
    attach.create(name="other", backends=_backends())
    attach._reset_process_state()

    b = attach.get_or_create(name="demo", backends=_backends())

    assert b.id == a.id
    assert b.name == "demo"
    assert read_active_session_name(resolve_project_root()) == "demo"
    assert attach.active().id == a.id


def test_get_or_create_rebinds_backend_factory_on_attach(tmp_path):
    attach.create(name="demo", use_datasources=False)
    con = ibis.duckdb.connect(":memory:")

    s = attach.get_or_create(name="demo", backends={"warehouse": lambda: con})

    assert s._backend_cache.get_or_create("warehouse") is con


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
    session_dir = s._layout.session_dir
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
    s = attach.create(name="demo", use_datasources=False)
    assert s.is_read_only
