"""mv.correlate for same-model MetricFrames."""

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    AlignmentFailedError,
    CrossSessionFrameError,
    SemanticKindMismatchError,
)
from marivo.analysis_py.frames.attribution import AttributionFrame
from marivo.analysis_py.frames.metric import MetricFrame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, metric_id, semantic_model="sales", semantic_kind="time_series"):
    return MetricFrame.from_dataframe(
        df,
        metric_id=metric_id,
        axes={},
        measure={"name": metric_id.rsplit(".", 1)[-1]},
        semantic_kind=semantic_kind,
        semantic_model=semantic_model,
        session=session,
    )


def test_correlate_sample_alignment_same_model_cross_metric():
    session = session_attach.create(name="demo")
    revenue = _metric(
        session,
        pd.DataFrame({"value": [10.0, 20.0, 30.0, 40.0]}),
        metric_id="sales.revenue",
    )
    orders = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0]}),
        metric_id="sales.orders",
    )

    out = mv.correlate(revenue, orders, session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.attribution_kind == "correlation"
    assert out.meta.metric_ids == ["sales.revenue", "sales.orders"]
    df = out.to_pandas()
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)
    assert df.iloc[0]["aligned_row_count"] == 4
    assert df.iloc[0]["dropped_row_count"] == 0


def test_correlate_common_key_alignment():
    session = session_attach.create(name="demo")
    a = _metric(
        session,
        pd.DataFrame({"bucket": ["2026-07-01", "2026-07-02"], "value": [10.0, 20.0]}),
        metric_id="sales.revenue",
    )
    b = _metric(
        session,
        pd.DataFrame({"bucket": ["2026-07-01", "2026-07-02"], "value": [5.0, 10.0]}),
        metric_id="sales.orders",
    )

    out = mv.correlate(a, b, align="bucket", session=session)

    df = out.to_pandas()
    assert df.iloc[0]["driver_field"] == "bucket"
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)


def test_correlate_common_key_alignment_uses_all_common_non_numeric_columns():
    session = session_attach.create(name="demo")
    a = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "consumer", "business"],
                "bucket": ["2026-07-01", "2026-07-02", "2026-07-01"],
                "value": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        semantic_kind="panel",
    )
    b = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "business", "business"],
                "bucket": ["2026-07-01", "2026-07-01", "2026-07-02"],
                "value": [5.0, 15.0, 25.0],
            }
        ),
        metric_id="sales.orders",
        semantic_kind="panel",
    )

    out = mv.correlate(a, b, align="bucket", session=session)

    df = out.to_pandas()
    assert df.iloc[0]["driver_field"] == "segment,bucket"
    assert df.iloc[0]["aligned_row_count"] == 2
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)


def test_correlate_rejects_duplicate_composite_keys_without_persisting():
    session = session_attach.create(name="demo")
    a = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "consumer", "business"],
                "bucket": ["2026-07-01", "2026-07-01", "2026-07-01"],
                "value": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        semantic_kind="panel",
    )
    b = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "business"],
                "bucket": ["2026-07-01", "2026-07-01"],
                "value": [5.0, 15.0],
            }
        ),
        metric_id="sales.orders",
        semantic_kind="panel",
    )

    with pytest.raises(AlignmentFailedError):
        mv.correlate(a, b, align="bucket", session=session)

    assert [job for job in session.jobs() if job.intent == "correlate"] == []


def test_correlate_sample_alignment_truncates_and_drops_nulls():
    session = session_attach.create(name="demo")
    a = _metric(
        session,
        pd.DataFrame({"left": [1.0, None, 3.0, 4.0]}),
        metric_id="sales.revenue",
    )
    b = _metric(
        session,
        pd.DataFrame({"right": [1.0, 2.0, 3.0, 4.0, 999.0]}),
        metric_id="sales.orders",
    )

    out = mv.correlate(a, b, value_a="left", value_b="right", session=session)

    df = out.to_pandas()
    assert df.iloc[0]["aligned_row_count"] == 3
    assert df.iloc[0]["dropped_row_count"] == 1
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)


def test_correlate_writes_job_and_frame():
    session = session_attach.create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    out = mv.correlate(a, b, session=session)

    jobs = [job for job in session.jobs() if job.intent == "correlate"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session.layout.frames_dir / out.ref / "data.parquet").is_file()
    params = session.job(jobs[0].id)["params"]
    assert params["value_a"] == "value"
    assert params["value_b"] == "value"


def test_correlate_rejects_constant_input_without_persisting():
    session = session_attach.create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [5.0, 5.0, 5.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}), metric_id="sales.orders")

    with pytest.raises(AlignmentFailedError):
        mv.correlate(a, b, session=session)

    assert [job for job in session.jobs() if job.intent == "correlate"] == []


def test_correlate_rejects_cross_model_frames():
    session = session_attach.create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0]}),
        metric_id="marketing.spend",
        semantic_model="marketing",
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.correlate(a, b, session=session)


def test_correlate_rejects_mixed_semantic_kind():
    session = session_attach.create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0]}),
        metric_id="sales.orders",
        semantic_kind="scalar",
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.correlate(a, b, session=session)


def test_correlate_rejects_insufficient_aligned_rows():
    session = session_attach.create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0]}), metric_id="sales.orders")
    with pytest.raises(AlignmentFailedError):
        mv.correlate(a, b, session=session)


def test_correlate_rejects_cross_session_frame():
    session_a = session_attach.create(name="a")
    a = _metric(session_a, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    session_b = session_attach.create(name="b")
    b = _metric(session_b, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.orders")
    with pytest.raises(CrossSessionFrameError):
        mv.correlate(a, b, session=session_a)
