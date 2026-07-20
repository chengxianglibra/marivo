"""Tests for session file-system layout and byte I/O helpers."""

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._layout import (
    PersistenceLayout,
    _atomic_write_text,
    read_frame_from_disk,
    read_job_record,
    write_frame_to_disk,
    write_job_record,
)
from tests.shared_fixtures import make_test_metric_meta_contract


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _layout(tmp_path):
    return PersistenceLayout(project_root=tmp_path, session_id="sess_test01")


# -- PersistenceLayout paths --


def test_layout_paths(tmp_path):
    layout = _layout(tmp_path)
    assert layout.session_dir == tmp_path / ".marivo" / "analysis" / "sessions" / "sess_test01"
    assert layout.jobs_dir == layout.session_dir / "jobs"
    assert layout.frames_dir == layout.session_dir / "frames"
    assert layout.scripts_dir == layout.session_dir / "scripts"


def test_layout_store_db(tmp_path):
    layout = _layout(tmp_path)
    assert layout.store_db == tmp_path / ".marivo" / "analysis" / "session_store.db"


# -- _atomic_write_text --


def test_atomic_write_text_creates_file(tmp_path):
    target = tmp_path / "sub" / "file.txt"
    _atomic_write_text(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_text_replaces_existing(tmp_path):
    target = tmp_path / "file.txt"
    _atomic_write_text(target, "v1")
    _atomic_write_text(target, "v2")
    assert target.read_text() == "v2"


# -- Job records --


def test_write_and_read_job_record(tmp_path):
    layout = _layout(tmp_path)
    record = {
        "id": "job_abc12345",
        "session_id": "sess_test01",
        "intent": "observe",
        "params": {"metric": "sales.revenue"},
        "input_frame_refs": [],
        "output_frame_ref": "frame_xyz",
        "started_at": _now().isoformat(),
        "finished_at": _now().isoformat(),
        "duration_ms": 100,
        "status": "succeeded",
        "error": None,
        "semantic_project_root": "/p",
        "semantic_model": "sales",
    }
    write_job_record(layout, record)
    assert read_job_record(layout, "job_abc12345") == record


# -- Frame disk I/O --


def test_write_and_read_frame(tmp_path):
    layout = _layout(tmp_path)
    df = pd.DataFrame({"x": [1, 2, 3]})
    meta = MetricFrameMeta(
        **make_test_metric_meta_contract("sales.revenue"),
        kind="metric_frame",
        ref="frame_pq789012",
        session_id="sess_test01",
        project_root=str(tmp_path),
        produced_by_job="job_a",
        created_at=_now(),
        row_count=3,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "x"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    written_meta = write_frame_to_disk(layout, MetricFrame(_df=df, meta=meta))
    assert written_meta.byte_size > 0
    assert (layout.frames_dir / "frame_pq789012" / "data.parquet").is_file()
    assert (layout.frames_dir / "frame_pq789012" / "meta.json").is_file()
    df_back, meta_back = read_frame_from_disk(layout, "frame_pq789012")
    assert list(df_back["x"]) == [1, 2, 3]
    assert meta_back["kind"] == "metric_frame"


# -- Module does not export session meta functions --


def test_layout_module_has_no_session_meta_functions():
    """_layout should not expose read_session_meta or write_session_meta."""
    import marivo.analysis.session._layout as mod

    assert not hasattr(mod, "read_session_meta")
    assert not hasattr(mod, "write_session_meta")
