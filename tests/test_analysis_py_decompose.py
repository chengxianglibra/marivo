"""mv.decompose for scalar, time-series, and segmented DeltaFrames."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    SessionStateError,
)
from marivo.analysis_py.frames.attribution import AttributionFrame
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.lineage import Lineage, LineageStep


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _delta(session, df, *, semantic_kind="time_series", ref="frame_delta"):
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref=ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_compare",
        created_at=_now(),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_compare",
                    inputs=["frame_a", "frame_b"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.revenue",
        source_a_ref="frame_a",
        source_b_ref="frame_b",
        compare_type="custom",
        align="bucket",
        calendar_info=None,
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def _metric(session):
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref="frame_metric",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        slice={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    return MetricFrame(_df=pd.DataFrame({"value": [10.0]}), meta=meta)


def test_decompose_time_series_infers_bucket_column():
    session = session_attach.create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
        semantic_kind="time_series",
    )

    out = mv.decompose(frame, session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.attribution_kind == "decomposition"
    assert out.meta.driver_field == "bucket"
    assert out.meta.metric_ids == ["sales.revenue"]
    df = out.to_pandas()
    assert list(df["bucket"]) == ["2026-07-01", "2026-07-03", "2026-07-02"]
    assert list(df["rank"]) == [1, 2, 3]
    assert df.iloc[0]["contribution"] == pytest.approx(10.0)


def test_decompose_segmented_uses_explicit_by():
    session = session_attach.create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["north", "north", "south"],
                "delta": [10.0, 5.0, -3.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = mv.decompose(frame, by="region", session=session)

    df = out.to_pandas()
    assert list(df["region"]) == ["north", "south"]
    assert list(df["contribution"]) == [pytest.approx(15.0), pytest.approx(-3.0)]
    assert list(df["rank"]) == [1, 2]


def test_decompose_segmented_infers_first_non_numeric_column():
    session = session_attach.create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["north", "south"],
                "cohort": ["new", "existing"],
                "delta": [5.0, 2.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = mv.decompose(frame, session=session)

    assert out.meta.driver_field == "region"
    assert list(out.to_pandas()["region"]) == ["north", "south"]


def test_decompose_scalar_emits_total_row():
    session = session_attach.create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"delta": [8.0]}),
        semantic_kind="scalar",
    )

    out = mv.decompose(frame, session=session)

    df = out.to_pandas()
    assert df.to_dict("records") == [
        {
            "driver": "total",
            "delta": 8.0,
            "contribution": 8.0,
            "pct_contribution": 1.0,
            "rank": 1,
        }
    ]


def test_decompose_writes_job_and_frame():
    session = session_attach.create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))

    out = mv.decompose(frame, session=session)

    jobs = [job for job in session.jobs() if job.intent == "decompose"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session.layout.frames_dir / out.ref / "data.parquet").is_file()


def test_decompose_rejects_metric_frame():
    session = session_attach.create(name="demo")
    with pytest.raises(SemanticKindMismatchError):
        mv.decompose(_metric(session), session=session)  # type: ignore[arg-type]


def test_decompose_rejects_panel_delta():
    session = session_attach.create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"bucket": ["a"], "delta": [1.0]}),
        semantic_kind="panel",
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.decompose(frame, session=session)


def test_decompose_rejects_missing_inferred_by():
    session = session_attach.create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"delta": [1.0, 2.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.decompose(frame, session=session)


def test_decompose_rejects_missing_value_column():
    session = session_attach.create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.decompose(frame, value="missing", session=session)


def test_decompose_rejects_non_numeric_value_column():
    session = session_attach.create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": ["bad"]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.decompose(frame, session=session)


def test_decompose_rejects_cross_session_frame():
    session_a = session_attach.create(name="a")
    frame = _delta(session_a, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_b = session_attach.create(name="b")
    with pytest.raises(CrossSessionFrameError):
        mv.decompose(frame, session=session_b)


def test_decompose_archived_session_raises():
    session = session_attach.create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        mv.decompose(frame, session=session)


def test_decompose_stale_archived_session_raises():
    session = session_attach.create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert session.state == "active"
    with pytest.raises(SessionStateError):
        mv.decompose(frame, session=session)
