"""Session class: in-memory state + lifecycle methods."""

from datetime import UTC, datetime

from marivo.analysis.calendar.loader import CalendarCache
from marivo.analysis.session.core import FrameRecord, JobSummary, Session, SessionState
from marivo.analysis.session.persistence import PersistenceLayout, write_job_record


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _session(tmp_path, *, read_only: bool = False) -> Session:
    layout = PersistenceLayout(project_root=tmp_path, session_id="sess_t01")
    return Session(
        id="sess_t01",
        name="demo",
        question="q",
        cwd=tmp_path,
        project_root=tmp_path,
        state="active",
        created_at=_now(),
        updated_at=_now(),
        backend_factory=None if read_only else (lambda name: object()),
        layout=layout,
        semantic_project=None,
        known_datasources=set(),
    )


def test_session_is_read_only_when_no_factory(tmp_path):
    assert _session(tmp_path, read_only=True).is_read_only is True


def test_session_is_not_read_only_with_factory(tmp_path):
    assert _session(tmp_path, read_only=False).is_read_only is False


def test_session_jobs_lists_records_sorted_by_started_at(tmp_path):
    s = _session(tmp_path)
    write_job_record(
        s.layout,
        {
            "id": "job_two",
            "session_id": "sess_t01",
            "intent": "observe",
            "params": {},
            "input_frame_refs": [],
            "output_frame_ref": "f2",
            "started_at": "2026-05-24T10:05:00+00:00",
            "finished_at": "2026-05-24T10:05:01+00:00",
            "duration_ms": 1000,
            "status": "succeeded",
            "error": None,
            "semantic_project_root": "/p",
            "semantic_model": "sales",
        },
    )
    write_job_record(
        s.layout,
        {
            "id": "job_one",
            "session_id": "sess_t01",
            "intent": "observe",
            "params": {},
            "input_frame_refs": [],
            "output_frame_ref": "f1",
            "started_at": "2026-05-24T10:00:00+00:00",
            "finished_at": "2026-05-24T10:00:01+00:00",
            "duration_ms": 1000,
            "status": "succeeded",
            "error": None,
            "semantic_project_root": "/p",
            "semantic_model": "sales",
        },
    )
    summaries = s.jobs()
    assert [j.id for j in summaries] == ["job_one", "job_two"]
    assert isinstance(summaries[0], JobSummary)


def test_session_frames_returns_frame_records(tmp_path):
    s = _session(tmp_path)
    frame_dir = s.layout.frames_dir / "frame_001"
    frame_dir.mkdir(parents=True)
    (frame_dir / "meta.json").write_text('{"ref": "frame_001", "kind": "metric"}')

    records = s.frames()

    assert records == [FrameRecord(ref="frame_001", kind="metric")]


def test_session_state_literal_values():
    assert SessionState.__args__ == ("active", "archived")  # type: ignore[attr-defined]


def test_session_close_clears_backend_cache(tmp_path):
    s = _session(tmp_path)
    s.backend_cache._cache["fake"] = object()
    s.close()
    assert s.backend_cache._cache == {}


def test_session_initializes_calendar_cache(tmp_path):
    s = _session(tmp_path)
    assert isinstance(s.calendars, CalendarCache)
