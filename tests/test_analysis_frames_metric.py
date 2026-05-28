"""MetricFrame and MetricFrameMeta."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import SessionStateError
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def test_meta_kind_literal_is_metric_frame():
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_abc",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_1",
        created_at=_now(),
        row_count=1,
        byte_size=64,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={"time": {"column": "order_date", "grain": "day"}},
        measure={"name": "amount", "unit": "USD", "type": "scalar"},
        window={"start": "2026-07-01", "end": "2026-09-30"},
        where={"region": "north"},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    assert meta.kind == "metric_frame"
    assert meta.metric_id == "sales.revenue"
    assert meta.axes["time"]["column"] == "order_date"


def test_metric_frame_wraps_df_and_meta():
    df = pd.DataFrame({"bucket": ["2026-07-01"], "value": [10.0]})
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_abc",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_1",
        created_at=_now(),
        row_count=1,
        byte_size=64,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={"time": {"column": "bucket", "grain": "day"}},
        measure={"name": "value", "unit": "USD", "type": "scalar"},
        window=None,
        where={},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    mf = MetricFrame(_df=df, meta=meta)
    assert mf.meta.metric_id == "sales.revenue"
    assert list(mf.columns) == ["bucket", "value"]


def test_from_dataframe_creates_external_entry(tmp_path):
    """from_dataframe marks lineage with external_inputs."""

    df = pd.DataFrame({"region": ["a", "b"], "value": [1.0, 2.0]})

    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_external_001",
        session_id="sess_x",
        project_root=str(tmp_path),
        produced_by_job=None,
        created_at=_now(),
        row_count=2,
        byte_size=64,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="from_dataframe",
                    job_ref=None,
                    inputs=[],
                    params_digest="external",
                )
            ],
            external_inputs=["frame_external_001"],
        ),
        metric_id="custom.metric",
        axes={"segment": {"column": "region"}},
        measure={"name": "value", "unit": None, "type": "scalar"},
        window=None,
        where={},
        semantic_kind="segmented",
        semantic_model="custom",
    )
    mf = MetricFrame(_df=df, meta=meta)
    assert mf.meta.produced_by_job is None
    assert "frame_external_001" in mf.meta.lineage.external_inputs


def test_from_dataframe_persists_external_frame(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    s = session_attach.get_or_create(name="demo")
    df = pd.DataFrame({"region": ["north"], "value": [1.0]})

    mf = MetricFrame.from_dataframe(
        df,
        metric_id="custom.metric",
        axes={"segment": {"column": "region"}},
        measure={"name": "value", "unit": None, "type": "scalar"},
        semantic_kind="segmented",
        semantic_model="custom",
        session=s,
    )

    assert mf.meta.produced_by_job is None
    assert mf.ref in mf.meta.lineage.external_inputs
    assert (s.layout.frames_dir / mf.ref / "data.parquet").is_file()


def test_from_dataframe_rejects_archived_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    s = session_attach.get_or_create(name="demo")
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        MetricFrame.from_dataframe(
            pd.DataFrame({"value": [1.0]}),
            metric_id="custom.metric",
            axes={},
            measure={"name": "value"},
            semantic_kind="scalar",
            semantic_model="custom",
            session=s,
        )


def test_metric_frame_meta_accepts_optional_normalization():
    from datetime import UTC, datetime

    from marivo.analysis.frames.metric import MetricFrameMeta

    meta = MetricFrameMeta(
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp/proj",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
        normalization={"kind": "share", "base": None, "columns_affected": ["revenue"]},
    )
    assert meta.normalization == {"kind": "share", "base": None, "columns_affected": ["revenue"]}


def test_metric_frame_meta_normalization_defaults_to_none():
    from datetime import UTC, datetime

    from marivo.analysis.frames.metric import MetricFrameMeta

    meta = MetricFrameMeta(
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp/proj",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    assert meta.normalization is None
