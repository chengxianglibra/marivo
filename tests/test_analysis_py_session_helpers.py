"""mv.session.current() and mv.session.history()."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.session.persistence import write_job_record


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_current_returns_none_when_no_active_session() -> None:
    assert mv.session.current() is None


def test_current_returns_summary_after_create() -> None:
    mv.session.get_or_create(name="s_test")
    current = mv.session.current()
    assert current is not None
    assert current.name == "s_test"


def test_history_returns_empty_list_when_no_active_session() -> None:
    assert mv.session.history() == []


def test_history_returns_empty_list_when_no_jobs() -> None:
    mv.session.get_or_create(name="s_test")
    assert mv.session.history() == []


def test_history_respects_limit() -> None:
    session = mv.session.get_or_create(name="s_test")
    for index in range(3):
        write_job_record(
            session.layout,
            {
                "id": f"job_{index}",
                "session_id": session.id,
                "intent": "observe",
                "params": {},
                "input_frame_refs": [],
                "output_frame_ref": f"frame_{index}",
                "started_at": f"2026-05-24T10:0{index}:00+00:00",
                "finished_at": f"2026-05-24T10:0{index}:01+00:00",
                "duration_ms": 1000,
                "status": "succeeded",
                "error": None,
                "semantic_project_root": "/p",
                "semantic_model": "sales",
            },
        )

    history = mv.session.history(limit=2)

    assert [job.id for job in history] == ["job_1", "job_2"]


def test_history_limit_zero_returns_empty_list() -> None:
    mv.session.get_or_create(name="s_test")
    assert mv.session.history(limit=0) == []
