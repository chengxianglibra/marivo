"""session lifecycle helpers (facade surface)."""

import ibis
import pytest

import marivo.analysis.session as session_facade


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_facade._reset_process_state()
    yield


def _backends():
    def fn():
        return ibis.duckdb.connect(":memory:")

    return {"warehouse": fn}


def test_get_or_create_creates_missing_session(tmp_path):
    s = session_facade.get_or_create(name="demo", question="q", backends=_backends())

    assert s.name == "demo"
    assert s.question == "q"


def test_get_or_create_attaches_existing_session(tmp_path):
    a = session_facade.get_or_create(name="demo", backends=_backends())
    session_facade._reset_process_state()

    b = session_facade.get_or_create(name="demo", backends=_backends())

    assert b.id == a.id
    assert b.name == "demo"


def test_get_or_create_rebinds_backend_factory_on_attach(tmp_path):
    session_facade.get_or_create(name="demo", use_datasources=False)
    con = ibis.duckdb.connect(":memory:")

    s = session_facade.get_or_create(name="demo", backends={"warehouse": lambda: con})

    assert s._backend_cache.get_or_create("warehouse") is con


def test_current_returns_none_when_no_session(tmp_path):
    assert session_facade.current() is None


def test_current_returns_session_after_get_or_create(tmp_path):
    s = session_facade.get_or_create(name="demo", backends=_backends())
    assert session_facade.current() is not None
    assert session_facade.current().id == s.id


def test_delete_removes_session_dir_and_store_row(tmp_path):
    s = session_facade.get_or_create(name="demo", backends=_backends())
    session_dir = s._layout.session_dir
    assert session_dir.is_dir()
    session_facade.delete("demo")
    assert not session_dir.is_dir()


def test_list_returns_all_sessions_in_project(tmp_path):
    session_facade.get_or_create(name="a", backends=_backends())
    session_facade.get_or_create(name="b", backends=_backends())
    assert sorted(s.name for s in session_facade.list()) == ["a", "b"]


def test_delete_is_noop_for_unknown_name(tmp_path):
    # Should not raise
    session_facade.delete("nonexistent")


def test_get_or_create_rejects_both_backends_and_backend_factory(tmp_path):
    from marivo.analysis.errors import SessionStateError

    with pytest.raises(SessionStateError):
        session_facade.get_or_create(
            name="demo",
            backends={"x": lambda: object()},
            backend_factory=lambda n: object(),
        )


def test_get_or_create_read_only_when_no_factory(tmp_path):
    s = session_facade.get_or_create(name="demo", use_datasources=False)
    assert s.is_read_only
