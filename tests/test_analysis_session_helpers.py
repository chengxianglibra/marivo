"""Facade-based session lifecycle tests.

All tests use ``import marivo.analysis as mv`` only; no direct imports
from ``attach``, ``active``, or ``persistence``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv

# ---------------------------------------------------------------------------
# __all__ and surface checks
# ---------------------------------------------------------------------------


def test_session_all_exports_exactly_four_names() -> None:
    assert mv.session.__all__ == ["current", "delete", "get_or_create", "list"]


def test_dir_does_not_contain_removed_names() -> None:
    names = dir(mv.session)
    for removed in ("attach", "active", "create", "switch", "archive"):
        assert removed not in names, f"removed name {removed!r} still in dir(mv.session)"


def test_hasattr_attach_is_false() -> None:
    assert hasattr(mv.session, "attach") is False


def test_hasattr_active_is_false() -> None:
    assert hasattr(mv.session, "active") is False


def test_hasattr_create_is_false() -> None:
    assert hasattr(mv.session, "create") is False


def test_hasattr_switch_is_false() -> None:
    assert hasattr(mv.session, "switch") is False


def test_hasattr_archive_is_false() -> None:
    assert hasattr(mv.session, "archive") is False


# ---------------------------------------------------------------------------
# current()
# ---------------------------------------------------------------------------


def test_current_returns_none_when_no_process_or_store_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    assert mv.session.current() is None


# ---------------------------------------------------------------------------
# get_or_create()
# ---------------------------------------------------------------------------


def test_get_or_create_creates_and_marks_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s = mv.session.get_or_create(name="s", use_datasources=False)
    assert s.name == "s"
    current = mv.session.current()
    assert current is not None
    assert current.id == s.id


def test_get_or_create_resumes_same_id_and_marks_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s1 = mv.session.get_or_create(name="s", use_datasources=False)
    s2 = mv.session.get_or_create(name="s", use_datasources=False)
    assert s1.id == s2.id
    assert mv.session.current() is not None
    assert mv.session.current().id == s1.id


def test_question_only_written_on_first_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s1 = mv.session.get_or_create(name="s", question="why?", use_datasources=False)
    assert s1.question == "why?"
    s2 = mv.session.get_or_create(name="s", question="different?", use_datasources=False)
    # question should NOT be overwritten on resume
    assert s2.question == "why?"


def test_default_calendar_restored_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s1 = mv.session.get_or_create(name="s", default_calendar="fiscal", use_datasources=False)
    assert s1.default_calendar == "fiscal"
    # Resume without explicit default_calendar -> should keep persisted value
    s2 = mv.session.get_or_create(name="s", use_datasources=False)
    assert s2.default_calendar == "fiscal"


def test_default_calendar_updated_when_explicitly_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    mv.session.get_or_create(name="s", default_calendar="fiscal", use_datasources=False)
    s = mv.session.get_or_create(name="s", default_calendar="standard", use_datasources=False)
    assert s.default_calendar == "standard"


def test_backends_and_backend_factory_both_raises_session_state_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    with pytest.raises(mv.errors.SessionStateError):
        mv.session.get_or_create(
            name="s",
            backends={"w": lambda: None},
            backend_factory=lambda name: None,
            use_datasources=False,
        )


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------


def test_list_returns_count_fields_and_no_state_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    mv.session.get_or_create(name="s1", use_datasources=False)
    mv.session.get_or_create(name="s2", use_datasources=False)
    summaries = mv.session.list()
    assert len(summaries) == 2
    s = summaries[0]
    # Must have count fields
    assert hasattr(s, "job_count")
    assert hasattr(s, "frame_count")
    assert hasattr(s, "report_count")
    # Must NOT have state field
    assert not hasattr(s, "state")


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


def test_delete_missing_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    # Should not raise
    mv.session.delete("nonexistent")


def test_delete_clears_current_and_allows_new_get_or_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s1 = mv.session.get_or_create(name="s", use_datasources=False)
    old_id = s1.id
    mv.session.delete("s")
    # Current should be None after delete
    assert mv.session.current() is None
    # get_or_create should create a new session with a different id
    s2 = mv.session.get_or_create(name="s", use_datasources=False)
    assert s2.id != old_id


def test_delete_interrupted_after_store_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When shutil.rmtree raises, store rows are gone but session dir remains.

    Calling get_or_create afterwards should create a new session id since
    the store no longer has a row for the old name.
    """
    import shutil

    from marivo.analysis.session._store import SessionStore

    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s1 = mv.session.get_or_create(name="s", use_datasources=False)
    old_id = s1.id

    # Create the session directory so we can observe that rmtree fails.
    old_session_dir = s1._layout.session_dir
    old_session_dir.mkdir(parents=True, exist_ok=True)

    # Make shutil.rmtree raise to simulate an interrupted delete.
    # When ignore_errors=True (which delete() uses), rmtree should
    # silently swallow the error.
    original_rmtree = shutil.rmtree

    def failing_rmtree(*args, **kwargs):
        if kwargs.get("ignore_errors"):
            # Simulate: ignore_errors=True means the error is swallowed
            # but the directory is NOT deleted.
            return
        raise OSError("simulated failure")

    monkeypatch.setattr("shutil.rmtree", failing_rmtree)

    # delete() should not raise; store rows are cleared first.
    mv.session.delete("s")

    # Store rows should be gone.
    store = SessionStore(project_root=tmp_path)
    assert store.get_session_by_name("s") is None

    # Session directory should still exist (rmtree was mocked to do nothing).
    assert old_session_dir.is_dir()

    # Restore rmtree so get_or_create can work.
    monkeypatch.setattr("shutil.rmtree", original_rmtree)

    # get_or_create should create a new session with a different id
    # since the store no longer has the old name.
    s2 = mv.session.get_or_create(name="s", use_datasources=False)
    assert s2.id != old_id
