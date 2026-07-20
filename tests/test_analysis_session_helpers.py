"""Facade-based session lifecycle tests.

All tests use ``import marivo.analysis as mv`` only; no direct imports
from ``attach``, ``active``, or ``persistence``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import marivo.analysis as mv

# ---------------------------------------------------------------------------
# __all__ and surface checks
# ---------------------------------------------------------------------------


def test_session_all_exports_exactly_six_names() -> None:
    assert mv.session.__all__ == [
        "current",
        "delete",
        "get_or_create",
        "inspect",
        "list",
        "recent",
    ]


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
    # Must NOT have state field
    assert not hasattr(s, "state")


# ---------------------------------------------------------------------------
# recent() and inspect()
# ---------------------------------------------------------------------------


def test_recent_is_bounded_newest_first_and_list_stays_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    first = mv.session.get_or_create(name="first", use_datasources=False)
    second = mv.session.get_or_create(name="second", use_datasources=False)

    listed = mv.session.list()
    assert isinstance(listed, list)
    assert [item.name for item in listed] == ["first", "second"]

    page = mv.session.recent(limit=1)
    assert [item.name for item in page.items] == ["second"]
    assert page.has_more is True
    assert page.next_cursor is not None
    next_page = mv.session.recent(limit=1, cursor=page.next_cursor)
    assert [item.name for item in next_page.items] == ["first"]
    assert {item.id for item in (*page.items, *next_page.items)} == {first.id, second.id}


def test_inspect_returns_bounded_snapshot_without_touching_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    from marivo.analysis.session._layout import write_job_record

    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    historical = mv.session.get_or_create(
        name="historical", question="Why did revenue drop?", use_datasources=False
    )
    meta_relative = (
        Path(".marivo")
        / "analysis"
        / "sessions"
        / historical.id
        / "frames"
        / "frame_1"
        / "meta.json"
    )
    meta_path = tmp_path / meta_relative
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "ref": "frame_1",
                "kind": "metric_frame",
                "metric_identity": {
                    "kind": "catalog",
                    "metric_ref": {
                        "schema": "marivo.semantic_ref/v1",
                        "kind": "metric",
                        "path": "sales.revenue",
                    },
                },
                "analysis_purpose": "explain revenue decline",
            }
        )
    )
    historical._store.record_artifact(
        session_id=historical.id,
        artifact_id="frame_1",
        kind="metric_frame",
        path=str(meta_relative.with_name("data.parquet")),
        meta_path=str(meta_relative),
        content_hash="content-1",
        produced_by_job="job_1",
        evidence_status="complete",
    )
    job_record = {
        "id": "job_1",
        "intent": "observe",
        "status": "succeeded",
        "started_at": "2026-01-01T00:00:00+00:00",
        "duration_ms": 10,
        "output_frame_ref": "frame_1",
    }
    write_job_record(historical._layout, job_record)
    historical._store.record_job(
        session_id=historical.id,
        job_id="job_1",
        intent="observe",
        status="succeeded",
        started_at=job_record["started_at"],
        finished_at="2026-01-01T00:00:00.010000+00:00",
        output_artifact_id="frame_1",
        record_path=str(
            historical._layout.relative_path(historical._layout.jobs_dir / "job_1.json")
        ),
    )
    active = mv.session.get_or_create(name="active", use_datasources=False)
    before = next(item for item in mv.session.list() if item.name == "historical")
    session_meta_path = historical._layout.session_dir / "meta.json"
    meta_before = session_meta_path.read_text()

    runtime = importlib.import_module("marivo.analysis.session._runtime")
    monkeypatch.setattr(
        runtime,
        "_build_semantic_catalog",
        lambda project_root: pytest.fail("inspect must not load the semantic catalog"),
    )
    snapshot = mv.session.inspect("historical", frame_limit=1, job_limit=1)

    after = next(item for item in mv.session.list() if item.name == "historical")
    assert mv.session.current() is not None
    assert mv.session.current().id == active.id
    assert after.updated_at == before.updated_at
    assert session_meta_path.read_text() == meta_before
    assert snapshot.summary.question == "Why did revenue drop?"
    assert snapshot.frames.items[0].metric_id == "sales.revenue"
    assert snapshot.recent_jobs[0].id == "job_1"


def test_inspect_missing_session_raises_typed_error_with_real_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    mv.session.get_or_create(name="known", use_datasources=False)

    with pytest.raises(mv.errors.SessionNotFoundError) as exc_info:
        mv.session.inspect("missing")

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    assert repair.help_target.canonical_id == "session.recent"
    assert repair.candidates == ("known",)


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: mv.session.recent(limit=0), "session.recent limit"),
        (lambda: mv.session.inspect("x", frame_limit=0), "frame_limit"),
        (lambda: mv.session.inspect("x", job_limit=101), "job_limit"),
    ],
)
def test_history_limits_fail_before_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    call,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    with pytest.raises(ValueError, match=message):
        call()


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
