"""SQLite session store contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from marivo.analysis.session._store import SessionStore, SessionSummary

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """Create a minimal project root with marivo.toml manifest."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    return tmp_path


@pytest.fixture()
def store(project_root: Path) -> SessionStore:
    return SessionStore(project_root=project_root)


# ---------------------------------------------------------------------------
# Schema path and pragmas
# ---------------------------------------------------------------------------


def test_db_path_under_marivo_analysis(project_root: Path) -> None:
    store = SessionStore(project_root=project_root)
    assert store.db_path == project_root / ".marivo" / "analysis" / "session_store.db"


def test_opening_store_creates_analysis_dir(project_root: Path) -> None:
    analysis_dir = project_root / ".marivo" / "analysis"
    assert not analysis_dir.exists()
    SessionStore(project_root=project_root)
    assert analysis_dir.is_dir()


def test_connection_enables_wal(store: SessionStore) -> None:
    with store._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_connection_enables_busy_timeout(store: SessionStore) -> None:
    with store._connect() as conn:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000


def test_connection_enables_foreign_keys(store: SessionStore) -> None:
    with store._connect() as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


# ---------------------------------------------------------------------------
# Lifecycle: get_or_insert_session
# ---------------------------------------------------------------------------


def test_get_or_insert_creates_one_row(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s",
        question="q",
        cwd=project_root,
        default_calendar="calendar",
    )
    assert row is not None
    assert row["name"] == "s"
    assert row["question"] == "q"
    assert row["id"].startswith("sess_")


def test_same_name_returns_same_id(store: SessionStore, project_root: Path) -> None:
    first = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar="cal"
    )
    second = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar="cal"
    )
    assert first["id"] == second["id"]


def test_different_question_keeps_original(store: SessionStore, project_root: Path) -> None:
    store.get_or_insert_session(name="s", question="q1", cwd=project_root, default_calendar="cal")
    row = store.get_or_insert_session(
        name="s", question="q2", cwd=project_root, default_calendar="cal"
    )
    assert row["question"] == "q1"


def test_explicit_default_calendar_updates_persisted(
    store: SessionStore, project_root: Path
) -> None:
    store.get_or_insert_session(name="s", question="q", cwd=project_root, default_calendar="cal_a")
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar="cal_b"
    )
    assert row["default_calendar"] == "cal_b"
    # updated_at must have advanced
    assert row["updated_at"] >= row["created_at"]


def test_no_default_calendar_restores_persisted(store: SessionStore, project_root: Path) -> None:
    store.get_or_insert_session(name="s", question="q", cwd=project_root, default_calendar="cal_a")
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    assert row["default_calendar"] == "cal_a"


def test_duplicate_create_race_handled_gracefully(store: SessionStore, project_root: Path) -> None:
    # Simulate a race: insert directly, then call get_or_insert_session
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, name, question, cwd, default_calendar, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_raced",
                "raced",
                "q",
                str(project_root),
                None,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    # This should not raise; it should return the existing row
    row = store.get_or_insert_session(
        name="raced", question="q", cwd=project_root, default_calendar=None
    )
    assert row["id"] == "sess_raced"


# ---------------------------------------------------------------------------
# Summary counts: list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_session_summary(store: SessionStore, project_root: Path) -> None:
    store.get_or_insert_session(name="s", question="q", cwd=project_root, default_calendar=None)
    summaries = store.list_sessions()
    assert len(summaries) == 1
    s = summaries[0]
    assert isinstance(s, SessionSummary)
    assert s.name == "s"
    assert s.question == "q"
    assert s.job_count == 0
    assert s.frame_count == 0


def test_list_sessions_job_count(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_job(
        session_id=sid,
        job_id="j1",
        intent="observe",
        status="completed",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        output_artifact_id=None,
        record_path="jobs/j1.json",
    )
    summaries = store.list_sessions()
    assert summaries[0].job_count == 1


def test_list_sessions_frame_count(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_artifact(
        session_id=sid,
        artifact_id="a1",
        kind="frame",
        path="frames/a1/data.parquet",
        meta_path="frames/a1/meta.json",
        content_hash=None,
        produced_by_job=None,
    )
    summaries = store.list_sessions()
    assert summaries[0].frame_count == 1


# ---------------------------------------------------------------------------
# Current pointer
# ---------------------------------------------------------------------------


def test_set_and_get_current_session_id(store: SessionStore) -> None:
    store.set_current_session_id("sess_abc")
    assert store.get_current_session_id() == "sess_abc"


def test_get_current_session_id_returns_none_initially(store: SessionStore) -> None:
    assert store.get_current_session_id() is None


def test_clear_current_session_id(store: SessionStore) -> None:
    store.set_current_session_id("sess_abc")
    store.clear_current_session_id()
    assert store.get_current_session_id() is None


def test_clear_current_session_id_when_none_is_noop(store: SessionStore) -> None:
    store.clear_current_session_id()  # should not raise


# ---------------------------------------------------------------------------
# Delete ordering
# ---------------------------------------------------------------------------


def test_delete_session_rows_removes_session_and_related(
    store: SessionStore, project_root: Path
) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_artifact(
        session_id=sid,
        artifact_id="a1",
        kind="frame",
        path="frames/a1/data.parquet",
        meta_path="frames/a1/meta.json",
        content_hash=None,
        produced_by_job=None,
    )
    store.record_job(
        session_id=sid,
        job_id="j1",
        intent="observe",
        status="completed",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at=None,
        output_artifact_id=None,
        record_path="jobs/j1.json",
    )
    result = store.delete_session_rows(name="s")
    assert result is not None
    assert result["id"] == sid
    # Verify all related rows are gone
    assert store.get_session_by_name("s") is None
    assert store.list_artifacts(sid) == []
    assert store.list_jobs(sid) == []


def test_delete_unknown_name_returns_none(store: SessionStore) -> None:
    result = store.delete_session_rows(name="nonexistent")
    assert result is None


def test_delete_does_not_remove_files(store: SessionStore, project_root: Path) -> None:
    dummy = project_root / ".marivo" / "analysis" / "dummy.txt"
    dummy.parent.mkdir(parents=True, exist_ok=True)
    dummy.write_text("keep me")
    store.get_or_insert_session(name="s", question="q", cwd=project_root, default_calendar=None)
    store.delete_session_rows(name="s")
    assert dummy.exists()
    assert dummy.read_text() == "keep me"


# ---------------------------------------------------------------------------
# Get-by helpers
# ---------------------------------------------------------------------------


def test_get_session_by_name(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    found = store.get_session_by_name("s")
    assert found is not None
    assert found["id"] == row["id"]


def test_get_session_by_name_missing(store: SessionStore) -> None:
    assert store.get_session_by_name("nope") is None


def test_get_session_by_id(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    found = store.get_session_by_id(row["id"])
    assert found is not None
    assert found["name"] == "s"


def test_get_session_by_id_missing(store: SessionStore) -> None:
    assert store.get_session_by_id("sess_nope") is None


# ---------------------------------------------------------------------------
# Touch and calendar helpers
# ---------------------------------------------------------------------------


def test_touch_session_updates_updated_at(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    old_updated = row["updated_at"]
    new_updated = store.touch_session(row["id"])
    assert new_updated >= old_updated


def test_update_default_calendar(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar="cal_a"
    )
    store.update_default_calendar(row["id"], "cal_b")
    updated = store.get_session_by_id(row["id"])
    assert updated is not None
    assert updated["default_calendar"] == "cal_b"


def test_update_default_calendar_to_none(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar="cal_a"
    )
    store.update_default_calendar(row["id"], None)
    updated = store.get_session_by_id(row["id"])
    assert updated is not None
    assert updated["default_calendar"] is None


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def test_record_and_get_artifact(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_artifact(
        session_id=sid,
        artifact_id="a1",
        kind="frame",
        path="frames/a1/data.parquet",
        meta_path="frames/a1/meta.json",
        content_hash="sha256:abc",
        produced_by_job="j1",
    )
    art = store.get_artifact(sid, "a1")
    assert art is not None
    assert art["kind"] == "frame"
    assert art["content_hash"] == "sha256:abc"
    assert art["produced_by_job"] == "j1"


def test_get_artifact_missing(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    assert store.get_artifact(row["id"], "nope") is None


def test_list_artifacts(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_artifact(
        session_id=sid,
        artifact_id="a1",
        kind="frame",
        path="f1",
        meta_path="m1",
        content_hash=None,
        produced_by_job=None,
    )
    store.record_artifact(
        session_id=sid,
        artifact_id="a2",
        kind="frame",
        path="f2",
        meta_path="m2",
        content_hash=None,
        produced_by_job=None,
    )
    arts = store.list_artifacts(sid)
    assert len(arts) == 2
    ids = {a["artifact_id"] for a in arts}
    assert ids == {"a1", "a2"}


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------


def test_record_and_get_job(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_job(
        session_id=sid,
        job_id="j1",
        intent="observe",
        status="completed",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        output_artifact_id="a1",
        record_path="jobs/j1.json",
    )
    job = store.get_job(sid, "j1")
    assert job is not None
    assert job["intent"] == "observe"
    assert job["status"] == "completed"
    assert job["output_artifact_id"] == "a1"


def test_get_job_missing(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    assert store.get_job(row["id"], "nope") is None


def test_list_jobs(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_job(
        session_id=sid,
        job_id="j1",
        intent="observe",
        status="completed",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at=None,
        output_artifact_id=None,
        record_path="jobs/j1.json",
    )
    store.record_job(
        session_id=sid,
        job_id="j2",
        intent="compare",
        status="completed",
        started_at="2026-01-01T00:01:00+00:00",
        finished_at=None,
        output_artifact_id=None,
        record_path="jobs/j2.json",
    )
    jobs = store.list_jobs(sid)
    assert len(jobs) == 2
    ids = {j["job_id"] for j in jobs}
    assert ids == {"j1", "j2"}


def test_store_does_not_create_report_table_or_helpers(
    store: SessionStore, project_root: Path
) -> None:
    assert not hasattr(store, "record_report")
    assert not hasattr(store, "get_report")
    assert not hasattr(store, "update_report_published_url")
    with store._connect() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
        ).fetchone()
    assert table is None


def test_record_artifact_preserves_content_hash(store: SessionStore, project_root: Path) -> None:
    row = store.get_or_insert_session(
        name="s", question="q", cwd=project_root, default_calendar=None
    )
    sid = row["id"]
    store.record_artifact(
        session_id=sid,
        artifact_id="a_hash",
        kind="metric_frame",
        path="frames/a_hash/data.parquet",
        meta_path="frames/a_hash/meta.json",
        content_hash="sha256:" + "b" * 64,
        produced_by_job=None,
    )

    artifact = store.get_artifact(sid, "a_hash")

    assert artifact is not None
    assert artifact["content_hash"] == "sha256:" + "b" * 64
