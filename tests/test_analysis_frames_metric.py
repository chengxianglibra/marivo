"""MetricFrame and MetricFrameMeta."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import FrameReadError, NoBackendFactoryError
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from tests.shared_fixtures import make_metric_frame, make_test_metric_meta_contract


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
        **make_test_metric_meta_contract("sales.revenue"),
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
        **make_test_metric_meta_contract("sales.revenue"),
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


def test_metric_frame_public_metric_name_uses_qualified_id_on_axis_collision():
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_collision",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_1",
        created_at=_now(),
        row_count=1,
        byte_size=64,
        lineage=Lineage(),
        metric_id="sales.region",
        **make_test_metric_meta_contract("sales.region"),
        axes={"region": {"role": "dimension", "column": "region"}},
        measure={"name": "region", "unit": None, "type": "scalar"},
        window=None,
        where={},
        semantic_kind="segmented",
        semantic_model="sales",
    )
    frame = MetricFrame(
        _df=pd.DataFrame({"region": ["NORTH"], "value": [1.0]}),
        meta=meta,
    )

    assert frame.value_columns == ("sales__region",)
    assert frame.columns == ["region", "sales__region"]
    assert list(frame.to_pandas().columns) == frame.columns


def test_metric_frame_public_metric_name_stays_unique_after_qualified_collision():
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_qualified_collision",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_1",
        created_at=_now(),
        row_count=1,
        byte_size=64,
        lineage=Lineage(),
        metric_id="sales.region",
        **make_test_metric_meta_contract("sales.region"),
        axes={
            "region": {"role": "dimension", "column": "region"},
            "qualified": {"role": "dimension", "column": "sales__region"},
        },
        measure={"name": "region", "unit": None, "type": "scalar"},
        window=None,
        where={},
        semantic_kind="segmented",
        semantic_model="sales",
    )
    frame = MetricFrame(
        _df=pd.DataFrame({"region": ["NORTH"], "sales__region": ["axis"], "value": [1.0]}),
        meta=meta,
    )

    assert frame.value_columns == ("sales__region#2",)
    assert frame.columns == ["region", "sales__region", "sales__region#2"]
    assert frame["sales__region#2"].tolist() == [1.0]
    assert list(frame.to_pandas().columns) == frame.columns
    assert [column.name for column in frame.contract().artifact_schema.columns] == frame.columns


def test_make_metric_frame_creates_external_entry(tmp_path):
    """test helper marks lineage with external_inputs."""

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
                    intent="test helper",
                    job_ref=None,
                    inputs=[],
                    params_digest="external",
                )
            ],
            external_inputs=["frame_external_001"],
        ),
        metric_id="custom.metric",
        **make_test_metric_meta_contract("custom.metric"),
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


def test_make_metric_frame_persists_external_frame(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    s = session_attach.get_or_create(name="demo")
    df = pd.DataFrame({"region": ["north"], "value": [1.0]})

    mf = make_metric_frame(
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
    assert (s._layout.frames_dir / mf.ref / "data.parquet").is_file()


def test_recovered_metric_frame_keeps_internal_value_and_public_metric_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"region": ["north"], "revenue": [1.0]}),
        metric_id="sales.revenue",
        axes={"region": {"role": "dimension", "column": "region"}},
        measure={"name": "revenue", "column": "revenue"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )

    recovered = session.get_frame(frame.ref)

    assert isinstance(recovered, MetricFrame)
    assert list(recovered._dataframe_copy().columns) == ["region", "value"]
    assert recovered.columns == ["region", "revenue"]
    assert list(recovered.to_pandas().columns) == recovered.columns
    assert [
        column.name for column in recovered.contract().artifact_schema.columns
    ] == recovered.columns


def test_make_metric_frame_rejects_read_only_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    s = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        make_metric_frame(
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
        **make_test_metric_meta_contract("sales.revenue"),
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
        **make_test_metric_meta_contract("sales.revenue"),
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    assert meta.normalization is None


def test_metric_frame_meta_component_links_default_to_none():
    meta = MetricFrameMeta(
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp/proj",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        metric_id="sales.revenue",
        **make_test_metric_meta_contract("sales.revenue"),
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    assert meta.component_ref is None
    assert meta.composition is None


def test_metric_frame_meta_coverage_links_default_to_none():
    meta = MetricFrameMeta(
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp/proj",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        metric_id="sales.revenue",
        **make_test_metric_meta_contract("sales.revenue"),
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    assert meta.coverage_ref is None
    assert meta.coverage_summary is None


def test_metric_frame_meta_accepts_coverage_summary():
    meta = MetricFrameMeta(
        ref="frame_test",
        session_id="sess_test",
        project_root="/tmp/proj",
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=0,
        byte_size=0,
        metric_id="sales.revenue",
        **make_test_metric_meta_contract("sales.revenue"),
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
        coverage_ref="cov_abc123",
        coverage_summary={"min": 0.5, "avg": 0.75, "partial_buckets": 3},
    )
    assert meta.coverage_ref == "cov_abc123"
    assert meta.coverage_summary == {"min": 0.5, "avg": 0.75, "partial_buckets": 3}


def test_metric_frame_coverage_raises_when_no_sidecar():
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
        **make_test_metric_meta_contract("sales.revenue"),
        axes={"time": {"column": "bucket", "grain": "day"}},
        measure={"name": "value", "unit": "USD", "type": "scalar"},
        window=None,
        where={},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    mf = MetricFrame(_df=df, meta=meta)
    with pytest.raises(FrameReadError, match="no coverage sidecar"):
        mf.coverage()
