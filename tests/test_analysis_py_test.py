from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    TestPolicyError,
    TestShapeNotTestableError,
)
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.session._load import load_frame
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _reset_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def test_seeded_time_series_metric_frame_fixture(tmp_path):
    session = session_attach.create(name="demo")
    frame = seeded_time_series_metric_frame(
        session=session,
        n_buckets=7,
        value_pattern="linear",
    )

    df = frame.to_pandas()
    assert frame.meta.semantic_kind == "time_series"
    assert frame.meta.axes["time"]["field"] == "time"
    assert frame.meta.window is not None
    assert list(df.columns) == ["time", "value"]
    assert df["value"].tolist() == [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]


def _metric_frame(session, rows, *, semantic_kind="time_series", axes=None, metric_id="sales.revenue"):
    df = pd.DataFrame(rows)
    return MetricFrame.from_dataframe(
        df,
        metric_id=metric_id,
        axes=axes or {"time": {"field": "time", "grain": "day"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window={"start": "2026-01-01", "end": "2026-01-05", "grain": "day", "time_field": "time"},
        session=session,
    )


def test_mean_changed_time_series_basic(tmp_path):
    session = session_attach.create(name="demo")
    times = pd.date_range("2026-01-01", periods=6, freq="D")
    a = _metric_frame(session, [{"time": t, "value": 20.0 + i} for i, t in enumerate(times)])
    b = _metric_frame(session, [{"time": t, "value": 10.0 + i * 0.2} for i, t in enumerate(times)])

    result = mv.test(a, b, session=session)
    row = result.to_pandas().iloc[0]

    assert result.meta.kind == "hypothesis_test_result"
    assert result.meta.result_shape == "single"
    assert result.meta.rejected_count == 1
    assert row["reason_code"] == "ok"
    assert row["sample_size"] == 6
    assert row["p_value"] < 0.05
    assert bool(row["rejected"]) is True


def test_mean_changed_time_series_no_diff(tmp_path):
    session = session_attach.create(name="demo")
    times = pd.date_range("2026-01-01", periods=5, freq="D")
    rows = [{"time": t, "value": float(i)} for i, t in enumerate(times)]

    result = mv.test(_metric_frame(session, rows), _metric_frame(session, rows), session=session)
    row = result.to_pandas().iloc[0]

    assert row["reason_code"] == "constant_diff"
    assert bool(row["rejected"]) is False


def test_segmented_paired_across_segments(tmp_path):
    session = session_attach.create(name="demo")
    axes = {"dimensions": [{"field": "segment"}]}
    a = _metric_frame(
        session,
        [{"segment": s, "value": v} for s, v in [("US", 20.0), ("CA", 22.0), ("MX", 24.0)]],
        semantic_kind="segmented",
        axes=axes,
    )
    b = _metric_frame(
        session,
        [{"segment": s, "value": v} for s, v in [("US", 10.0), ("CA", 11.0), ("MX", 12.0)]],
        semantic_kind="segmented",
        axes=axes,
    )

    result = mv.test(a, b, sampling=mv.SamplingPolicy(pairing="segment_key"), session=session)
    assert result.meta.result_shape == "single"
    assert result.to_pandas().iloc[0]["sample_size"] == 3


def test_panel_per_segment_rows(tmp_path):
    session = session_attach.create(name="demo")
    times = pd.date_range("2026-01-01", periods=4, freq="D")
    axes = {"time": {"field": "time", "grain": "day"}, "dimensions": [{"field": "segment"}]}
    a = _metric_frame(
        session,
        [
            {"segment": s, "time": t, "value": 20.0 + i}
            for s in ["US", "CA"]
            for i, t in enumerate(times)
        ],
        semantic_kind="panel",
        axes=axes,
    )
    b = _metric_frame(
        session,
        [
            {"segment": s, "time": t, "value": 10.0 + i}
            for s in ["US", "CA"]
            for i, t in enumerate(times)
        ],
        semantic_kind="panel",
        axes=axes,
    )

    result = mv.test(a, b, session=session)
    df = result.to_pandas()
    assert result.meta.result_shape == "per_segment"
    assert result.meta.segment_dimensions == ["segment"]
    assert set(df["segment"]) == {"US", "CA"}


def test_test_operator_errors_and_persistence(tmp_path):
    session = session_attach.create(name="demo")
    a = _metric_frame(session, [{"value": 1.0}], semantic_kind="scalar", axes={})
    with pytest.raises(TestShapeNotTestableError):
        mv.test(a, a, session=session)

    ts = seeded_time_series_metric_frame(session=session, n_buckets=4)
    with pytest.raises(TestPolicyError):
        mv.test(ts, ts, sampling=mv.SamplingPolicy(pairing="segment_key"), session=session)
    with pytest.raises(TestPolicyError):
        mv.test(ts, ts, alpha=0, session=session)
    with pytest.raises(TestPolicyError):
        mv.test(
            ts,
            ts,
            alignment=mv.AlignmentPolicy(kind="dow_aligned", calendar=mv.CalendarRef("sales.retail")),
            session=session,
        )

    other = session_attach.create(name="other")
    foreign = seeded_time_series_metric_frame(session=other, n_buckets=4)
    with pytest.raises(CrossSessionFrameError):
        mv.test(ts, foreign, session=session)

    segmented = _metric_frame(
        session,
        [{"segment": "US", "value": 1.0}],
        semantic_kind="segmented",
        axes={"dimensions": [{"field": "segment"}]},
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.test(ts, segmented, session=session)

    result = mv.test(ts, ts, session=session)
    loaded = load_frame(result.ref, session=session)
    assert loaded.meta.kind == "hypothesis_test_result"
    assert loaded.lineage.steps[-1].intent == "test"
