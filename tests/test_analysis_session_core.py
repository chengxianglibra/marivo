"""Session class: store-backed jobs and frame summaries."""

from datetime import UTC, datetime

import pytest

from marivo.analysis.calendar.loader import CalendarCache
from marivo.analysis.errors import JobNotFoundError
from marivo.analysis.session._layout import PersistenceLayout
from marivo.analysis.session._runtime import _build_connection_runtime, persist_job_record
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session.core import JobSummary, Session
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import make_metric_frame


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _session(tmp_path, *, read_only: bool = False) -> Session:
    layout = PersistenceLayout(project_root=tmp_path, session_id="sess_t01")
    store = SessionStore(project_root=tmp_path)
    # Insert a session row with the known ID so foreign key constraints pass.
    with store._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, name, question, cwd, default_calendar, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_t01",
                "demo",
                "q",
                str(tmp_path),
                None,
                "2026-05-24T10:00:00+00:00",
                "2026-05-24T10:00:00+00:00",
            ),
        )
    return Session(
        id="sess_t01",
        name="demo",
        question="q",
        cwd=tmp_path,
        project_root=tmp_path,
        created_at=_now(),
        updated_at=_now(),
        connection_runtime=_build_connection_runtime(
            tmp_path,
            None if read_only else {"fake": lambda: object()},
            None,
            use_datasources=False,
        ),
        layout=layout,
        semantic_catalog=SemanticCatalog(SemanticProject(workspace_dir=tmp_path)),
        store=store,
    )


def test_session_is_read_only_when_no_factory(tmp_path):
    assert _session(tmp_path, read_only=True).is_read_only is True


def test_session_is_not_read_only_with_factory(tmp_path):
    assert _session(tmp_path, read_only=False).is_read_only is False


def test_session_jobs_lists_records_sorted_by_started_at(tmp_path):
    s = _session(tmp_path)
    persist_job_record(
        s,
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
    persist_job_record(
        s,
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


def test_session_job_raises_job_not_found_from_store_absence(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(JobNotFoundError) as exc_info:
        s.job("nonexistent_job")
    assert "nonexistent_job" in exc_info.value.message


def test_session_frame_summaries_returns_only_registered_artifacts(tmp_path):
    s = _session(tmp_path)
    import pandas as pd

    # make_metric_frame uses persist_frame which registers in the store.
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )

    records = s.frame_summaries()
    assert len(records) == 1
    assert records[0].ref == frame.ref
    assert records[0].kind == "metric_frame"
    assert records[0].metric_id == "sales.revenue"

    # Write a frame directory without registering in the store — it should be invisible.
    orphan_dir = s._layout.frames_dir / "orphan_001"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "meta.json").write_text(
        '{"ref": "orphan_001", "kind": "metric_frame", "metric_id": "orphan.metric"}'
    )
    assert len(s.frame_summaries()) == 1


def test_session_frame_summaries_sorted_by_created_at_then_ref(tmp_path):
    s = _session(tmp_path)
    import pandas as pd

    frame_a = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    frame_b = make_metric_frame(
        pd.DataFrame({"value": [2.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    records = s.frame_summaries()
    assert len(records) == 2
    # Frames are sorted by created_at then ref.
    # The two frames were created sequentially, so they should be in creation order.
    assert records[0].ref == frame_a.ref
    assert records[1].ref == frame_b.ref


def test_session_close_closes_runtime_connections(tmp_path):
    s = _session(tmp_path)
    s._connection_runtime.session_backend("fake")
    assert s._connection_runtime.service._session_backends
    s.close()
    assert s._connection_runtime.service._session_backends == {}


def test_session_initializes_calendar_cache(tmp_path):
    s = _session(tmp_path)
    assert isinstance(s._calendars, CalendarCache)


def test_session_public_fields_are_read_only(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(AttributeError):
        s.id = "other"
    with pytest.raises(AttributeError):
        s.name = "other"
    with pytest.raises(AttributeError):
        s.created_at = _now()


def test_session_exposes_catalog_property(tmp_path, monkeypatch):
    import marivo.analysis as mv
    from marivo.semantic.catalog import SemanticCatalog

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="catalog_session", use_datasources=False)

    assert isinstance(session.catalog, SemanticCatalog)
    assert session.catalog.workspace_dir == tmp_path


def test_session_close_closes_connection_runtime(tmp_path):
    from datetime import UTC, datetime

    from marivo.analysis.session._connections import AnalysisConnectionRuntime
    from marivo.analysis.session._layout import PersistenceLayout
    from marivo.analysis.session._store import SessionStore
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

    class Runtime(AnalysisConnectionRuntime):
        def __init__(self):
            super().__init__(
                DatasourceConnectionService(project_root=tmp_path, use_datasources=False)
            )
            self.closed = False

        def close_all(self):
            self.closed = True

    runtime = Runtime()
    project = SemanticProject(workspace_dir=tmp_path)
    session = Session(
        id="s1",
        name="n",
        question=None,
        cwd=tmp_path,
        project_root=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        connection_runtime=runtime,
        layout=PersistenceLayout(project_root=tmp_path, session_id="s1"),
        semantic_catalog=SemanticCatalog(project),
        store=SessionStore(project_root=tmp_path),
    )

    session.close()

    assert runtime.closed


def test_session_constructor_rejects_old_runtime_keywords(tmp_path):
    from marivo.analysis.session._connections import AnalysisConnectionRuntime
    from marivo.analysis.session._layout import PersistenceLayout
    from marivo.analysis.session._store import SessionStore
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

    kwargs = {
        "id": "s1",
        "name": "n",
        "question": None,
        "cwd": tmp_path,
        "project_root": tmp_path,
        "created_at": _now(),
        "updated_at": _now(),
        "connection_runtime": AnalysisConnectionRuntime(
            DatasourceConnectionService(project_root=tmp_path, use_datasources=False)
        ),
        "layout": PersistenceLayout(project_root=tmp_path, session_id="s1"),
        "semantic_catalog": SemanticCatalog(SemanticProject(workspace_dir=tmp_path)),
        "store": SessionStore(project_root=tmp_path),
    }

    with pytest.raises(TypeError):
        Session(**kwargs, backend_factory=lambda name: object())

    with pytest.raises(TypeError):
        Session(**kwargs, semantic_project=SemanticProject(workspace_dir=tmp_path))


def test_persisted_frame_records_content_hash_in_meta_store_and_state(tmp_path):
    s = _session(tmp_path)
    import json

    import pandas as pd

    frame = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-06-18"], "value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )

    assert frame.state.content_hash is not None
    assert frame.state.content_hash.startswith("sha256:")

    row = s._store.get_artifact(s.id, frame.ref)
    assert row is not None
    assert row["content_hash"] == frame.state.content_hash

    meta_path = s.project_root / row["meta_path"]
    meta_payload = json.loads(meta_path.read_text())
    assert meta_payload["content_hash"] == frame.state.content_hash

    loaded = s.get_frame(frame.ref)
    assert loaded.state.content_hash == frame.state.content_hash

    [summary] = s.frame_summaries()
    assert summary.content_hash == frame.state.content_hash
