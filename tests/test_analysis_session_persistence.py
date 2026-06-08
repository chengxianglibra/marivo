"""Disk I/O for session metadata, job records, and frame files."""

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session.persistence import (
    PersistenceLayout,
    read_frame_from_disk,
    read_job_record,
    read_session_meta,
    write_frame_to_disk,
    write_job_record,
    write_session_meta,
)


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _layout(tmp_path):
    return PersistenceLayout(project_root=tmp_path, session_id="sess_test01")


def test_layout_paths(tmp_path):
    layout = _layout(tmp_path)
    assert layout.session_dir == tmp_path / ".marivo" / "analysis" / "sessions" / "sess_test01"
    assert layout.jobs_dir == layout.session_dir / "jobs"
    assert layout.frames_dir == layout.session_dir / "frames"
    assert layout.scripts_dir == layout.session_dir / "scripts"
    assert layout.reports_dir == layout.session_dir / "reports"


def test_write_and_read_session_meta(tmp_path):
    layout = _layout(tmp_path)
    meta = {
        "id": "sess_test01",
        "name": "demo",
        "question": "What changed?",
        "cwd": "/tmp/proj",
        "state": "active",
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
        "project_root": str(tmp_path),
        "known_datasources": ["warehouse"],
    }
    write_session_meta(layout, meta)
    assert read_session_meta(layout) == meta


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


def test_write_and_read_frame(tmp_path):
    layout = _layout(tmp_path)
    df = pd.DataFrame({"x": [1, 2, 3]})
    meta = MetricFrameMeta(
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


def test_write_session_meta_atomic_via_rename(tmp_path):
    layout = _layout(tmp_path)
    write_session_meta(layout, {"id": "sess_test01", "name": "v1"})
    write_session_meta(layout, {"id": "sess_test01", "name": "v2"})
    assert read_session_meta(layout)["name"] == "v2"
