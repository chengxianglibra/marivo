"""mv.session.current()."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_current_returns_none_when_no_active_session() -> None:
    assert mv.session.current() is None


def test_current_returns_session_after_create() -> None:
    mv.session.get_or_create(name="s_test")
    current = mv.session.current()
    assert current is not None
    assert current.name == "s_test"
    assert hasattr(current, "observe")


def test_current_session_has_datetime_timestamps() -> None:
    mv.session.get_or_create(name="s_test")
    current = mv.session.current()
    assert current is not None
    from datetime import datetime

    assert isinstance(current.created_at, datetime)
    assert isinstance(current.updated_at, datetime)
