"""session.decompose for scalar, time-series, and segmented DeltaFrames."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    SessionStateError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.refs import DimensionRef


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
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={"kind": "window_bucket"},
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
        where={},
        semantic_kind="scalar",
        semantic_model="sales",
    )
    return MetricFrame(_df=pd.DataFrame({"value": [10.0]}), meta=meta)


def test_decompose_time_series_uses_axis_ref():
    session = session_attach.get_or_create(name="demo")
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

    out = session.decompose(frame, axis=DimensionRef("bucket"))

    assert isinstance(out, AttributionFrame)
    assert out.meta.attribution_kind == "decomposition"
    assert out.meta.driver_field == "bucket"
    assert out.meta.metric_ids == ["sales.revenue"]
    df = out.to_pandas()
    assert list(df["bucket"]) == ["2026-07-01", "2026-07-03", "2026-07-02"]
    assert list(df["rank"]) == [1, 2, 3]
    assert df.iloc[0]["contribution"] == pytest.approx(10.0)


def test_decompose_segmented_uses_axis_ref():
    session = session_attach.get_or_create(name="demo")
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

    out = session.decompose(frame, axis=DimensionRef("region"))

    df = out.to_pandas()
    assert list(df["region"]) == ["north", "south"]
    assert list(df["contribution"]) == [pytest.approx(15.0), pytest.approx(-3.0)]
    assert list(df["rank"]) == [1, 2]


def test_decompose_accepts_model_prefixed_axis_ref():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "department": ["analytics", "search", "analytics"],
                "delta": [10.0, -3.0, 4.0],
            }
        ),
        semantic_kind="segmented",
    )

    out = session.decompose(frame, axis=DimensionRef("trino_query.department"))

    assert out.meta.driver_field == "department"
    df = out.to_pandas()
    assert list(df["department"]) == ["analytics", "search"]
    assert list(df["contribution"]) == [pytest.approx(14.0), pytest.approx(-3.0)]


def test_decompose_requires_axis_argument():
    session = session_attach.get_or_create(name="demo")
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

    with pytest.raises(TypeError):
        session.decompose(frame)


def test_decompose_scalar_rejects_missing_axis_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"delta": [8.0]}),
        semantic_kind="scalar",
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.decompose(frame, axis=DimensionRef("region"))

    assert exc_info.value.details["requested_axis"] == "region"
    assert exc_info.value.details["normalized_axis"] == "region"
    assert exc_info.value.details["available_columns"] == ["delta"]


def test_decompose_writes_job_and_frame():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))

    out = session.decompose(frame, axis=DimensionRef("bucket"))

    jobs = [job for job in session.jobs() if job.intent == "decompose"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session._layout.frames_dir / out.ref / "data.parquet").is_file()


def test_decompose_rejects_metric_frame():
    session = session_attach.get_or_create(name="demo")
    with pytest.raises(SemanticKindMismatchError):
        session.decompose(_metric(session), axis=DimensionRef("bucket"))  # type: ignore[arg-type]


def test_decompose_rejects_panel_delta():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"bucket": ["a"], "delta": [1.0]}),
        semantic_kind="panel",
    )
    with pytest.raises(SemanticKindMismatchError):
        session.decompose(frame, axis=DimensionRef("bucket"))


def test_decompose_rejects_non_dimension_ref_axis():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["north", "south"], "delta": [1.0, 2.0]}),
        semantic_kind="segmented",
    )
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.decompose(frame, axis="region")  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "DimensionRef"
    assert exc_info.value.details["got_kind"] == "str"


def test_decompose_rejects_missing_axis_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"delta": [1.0, 2.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.decompose(frame, axis=DimensionRef("bucket"))

    assert exc_info.value.details["requested_axis"] == "bucket"
    assert exc_info.value.details["normalized_axis"] == "bucket"
    assert exc_info.value.details["available_columns"] == ["delta"]


def test_decompose_rejects_missing_delta_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "value": [1.0]}))
    with pytest.raises(SemanticKindMismatchError):
        session.decompose(frame, axis=DimensionRef("bucket"))


def test_decompose_rejects_measure_column_kwarg():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    with pytest.raises(TypeError):
        session.decompose(frame, axis=DimensionRef("bucket"), measure_column="delta")  # type: ignore[call-arg]
    from marivo.analysis.intents.decompose import decompose

    with pytest.raises(TypeError):
        decompose(frame, axis=DimensionRef("bucket"), measure_column="delta", session=session)  # type: ignore[call-arg]


def test_decompose_rejects_non_numeric_value_column():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": ["bad"]}))
    with pytest.raises(SemanticKindMismatchError):
        session.decompose(frame, axis=DimensionRef("bucket"))


def test_decompose_rejects_cross_session_frame():
    session_a = session_attach.get_or_create(name="a")
    frame = _delta(session_a, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_b = session_attach.get_or_create(name="b")
    with pytest.raises(CrossSessionFrameError):
        session_b.decompose(frame, axis=DimensionRef("bucket"))


def test_decompose_archived_session_raises():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        session.decompose(frame, axis=DimensionRef("bucket"))


def test_decompose_stale_archived_session_raises():
    session = session_attach.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"bucket": ["a"], "delta": [1.0]}))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert session.state == "active"
    with pytest.raises(SessionStateError):
        session.decompose(frame, axis=DimensionRef("bucket"))
