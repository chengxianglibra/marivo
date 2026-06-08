from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    TestPolicyError,
    TestShapeNotTestableError,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.session._load import load_frame
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _reset_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def test_seeded_time_series_metric_frame_fixture(tmp_path):
    session = session_attach.get_or_create(name="demo")
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


def _metric_frame(
    session,
    rows,
    *,
    semantic_kind="time_series",
    axes=None,
    metric_id="sales.revenue",
    window=None,
):
    df = pd.DataFrame(rows)
    return MetricFrame.from_dataframe(
        df,
        metric_id=metric_id,
        axes=axes or {"time": {"role": "time", "field": "time", "grain": "day"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        # end=01-07 gives 6 day-buckets, matching the 6-row test data
        window=window
        or {"start": "2026-01-01", "end": "2026-01-07", "grain": "day", "time_field": "time"},
        session=session,
    )


def test_mean_changed_time_series_basic(tmp_path):
    session = session_attach.get_or_create(name="demo")
    times = pd.date_range("2026-01-01", periods=6, freq="D")
    a = _metric_frame(session, [{"time": t, "value": 20.0 + i} for i, t in enumerate(times)])
    b = _metric_frame(session, [{"time": t, "value": 10.0 + i * 0.2} for i, t in enumerate(times)])

    result = session.hypothesis_test(a, b)
    row = result.to_pandas().iloc[0]

    assert result.meta.kind == "hypothesis_test_result"
    assert result.meta.result_shape == "single"
    assert result.meta.rejected_count == 1
    assert row["reason_code"] == "ok"
    assert row["sample_size"] == 6
    assert row["p_value"] < 0.05
    assert bool(row["rejected"]) is True


def test_mean_changed_time_series_no_diff(tmp_path):
    session = session_attach.get_or_create(name="demo")
    times = pd.date_range("2026-01-01", periods=5, freq="D")
    rows = [{"time": t, "value": float(i)} for i, t in enumerate(times)]

    result = session.hypothesis_test(_metric_frame(session, rows), _metric_frame(session, rows))
    row = result.to_pandas().iloc[0]

    assert row["reason_code"] == "constant_diff"
    assert bool(row["rejected"]) is False


def test_segmented_paired_across_segments(tmp_path):
    session = session_attach.get_or_create(name="demo")
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

    result = session.hypothesis_test(a, b, sampling=mv.SamplingPolicy(pairing="segment_key"))
    assert result.meta.result_shape == "single"
    assert result.to_pandas().iloc[0]["sample_size"] == 3


def test_panel_per_segment_rows(tmp_path):
    session = session_attach.get_or_create(name="demo")
    times = pd.date_range("2026-01-01", periods=4, freq="D")
    axes = {
        "time": {"role": "time", "field": "time", "grain": "day"},
        "dimensions": [{"field": "segment"}],
    }
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

    result = session.hypothesis_test(a, b)
    df = result.to_pandas()
    assert result.meta.result_shape == "per_segment"
    assert result.meta.segment_dimensions == ["segment"]
    assert set(df["segment"]) == {"US", "CA"}


def test_time_series_cross_window_ordinal_pairing(tmp_path):
    """Regression: disjoint date windows must pair by ordinal bucket position.

    Pre-fix the literal-date merge produced 0 paired rows; this scenario
    must now yield 7 pairs with a mean diff that reflects ordinal pairing
    (cur[i] - base[i] for each i in 0..6), not a TestAlignmentError.
    """
    session = session_attach.get_or_create(name="demo")
    cur_window = {"start": "2026-01-08", "end": "2026-01-15", "grain": "day", "time_field": "time"}
    base_window = {"start": "2026-01-01", "end": "2026-01-08", "grain": "day", "time_field": "time"}
    cur_times = pd.date_range("2026-01-08", periods=7, freq="D")
    base_times = pd.date_range("2026-01-01", periods=7, freq="D")
    a = _metric_frame(
        session,
        [{"time": t, "value": 20.0 + i} for i, t in enumerate(cur_times)],
        window=cur_window,
    )
    b = _metric_frame(
        session,
        [{"time": t, "value": 10.0 + 2 * i} for i, t in enumerate(base_times)],
        window=base_window,
    )

    result = session.hypothesis_test(a, b)
    row = result.to_pandas().iloc[0]

    assert result.meta.result_shape == "single"
    assert row["reason_code"] == "ok"
    assert row["sample_size"] == 7
    assert row["mean_diff"] == pytest.approx(7.0)
    assert bool(row["rejected"]) is True


def test_panel_cross_window_ordinal_pairing(tmp_path):
    """Regression: panel path must pair the time axis ordinally per segment.

    Same disjoint-window scenario as the time_series regression, but on a
    panel frame. Each segment must independently produce 7 ordinal pairs.
    """
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {"role": "time", "field": "time", "grain": "day"},
        "dimensions": [{"field": "segment"}],
    }
    cur_window = {"start": "2026-01-08", "end": "2026-01-15", "grain": "day", "time_field": "time"}
    base_window = {"start": "2026-01-01", "end": "2026-01-08", "grain": "day", "time_field": "time"}
    cur_times = pd.date_range("2026-01-08", periods=7, freq="D")
    base_times = pd.date_range("2026-01-01", periods=7, freq="D")
    a_rows = [
        {"segment": s, "time": t, "value": 20.0 + i}
        for s in ["US", "CA"]
        for i, t in enumerate(cur_times)
    ]
    b_rows = [
        {"segment": s, "time": t, "value": 10.0 + 2 * i}
        for s in ["US", "CA"]
        for i, t in enumerate(base_times)
    ]
    a = _metric_frame(session, a_rows, semantic_kind="panel", axes=axes, window=cur_window)
    b = _metric_frame(session, b_rows, semantic_kind="panel", axes=axes, window=base_window)

    result = session.hypothesis_test(a, b)
    df = result.to_pandas().set_index("segment")

    assert result.meta.result_shape == "per_segment"
    assert set(df.index) == {"US", "CA"}
    for segment in ["US", "CA"]:
        row = df.loc[segment]
        assert row["reason_code"] == "ok"
        assert row["sample_size"] == 7
        assert row["mean_diff"] == pytest.approx(7.0)


def test_time_series_partial_overlap_uses_ordinal_pairing(tmp_path):
    """Partially overlapping windows must still pair ordinally.

    cur=01-02..01-05 (4 buckets), base=01-01..01-04 (4 buckets). Literal
    overlap is 3 dates (01-02, 01-03, 01-04). Ordinal pairing yields 4
    pairs and a mean diff that only matches the ordinal interpretation.
    """
    session = session_attach.get_or_create(name="demo")
    cur_window = {"start": "2026-01-02", "end": "2026-01-06", "grain": "day", "time_field": "time"}
    base_window = {"start": "2026-01-01", "end": "2026-01-05", "grain": "day", "time_field": "time"}
    cur_times = pd.date_range("2026-01-02", periods=4, freq="D")
    base_times = pd.date_range("2026-01-01", periods=4, freq="D")
    cur_values = [10.0, 20.0, 30.0, 40.0]
    base_values = [0.0, 5.0, 25.0, 30.0]
    a = _metric_frame(
        session,
        [{"time": t, "value": v} for t, v in zip(cur_times, cur_values, strict=True)],
        window=cur_window,
    )
    b = _metric_frame(
        session,
        [{"time": t, "value": v} for t, v in zip(base_times, base_values, strict=True)],
        window=base_window,
    )

    result = session.hypothesis_test(a, b)
    row = result.to_pandas().iloc[0]

    assert row["sample_size"] == 4
    assert row["mean_diff"] == pytest.approx(10.0)


def test_test_operator_errors_and_persistence(tmp_path):
    session = session_attach.get_or_create(name="demo")
    a = _metric_frame(session, [{"value": 1.0}], semantic_kind="scalar", axes={})
    with pytest.raises(TestShapeNotTestableError):
        session.hypothesis_test(a, a)

    ts = seeded_time_series_metric_frame(session=session, n_buckets=4)
    with pytest.raises(TestPolicyError):
        session.hypothesis_test(ts, ts, sampling=mv.SamplingPolicy(pairing="segment_key"))
    with pytest.raises(TestPolicyError):
        session.hypothesis_test(ts, ts, alpha=0)
    with pytest.raises(TestPolicyError):
        session.hypothesis_test(
            ts,
            ts,
            alignment=mv.AlignmentPolicy(
                kind="dow_aligned", calendar=mv.CalendarRef("sales.retail")
            ),
        )
    with pytest.raises(TestPolicyError) as calendar_bucket_exc:
        session.hypothesis_test(
            ts,
            ts,
            alignment=mv.AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
        )
    assert "only supports default window_bucket alignment" in str(calendar_bucket_exc.value)

    with pytest.raises(TestPolicyError):
        session.hypothesis_test(
            ts,
            ts,
            alignment=mv.AlignmentPolicy(kind="window_bucket", strict_lengths=True),
        )

    other = session_attach.get_or_create(name="other")
    foreign = seeded_time_series_metric_frame(session=other, n_buckets=4)
    with pytest.raises(CrossSessionFrameError):
        session.hypothesis_test(ts, foreign)

    segmented = _metric_frame(
        session,
        [{"segment": "US", "value": 1.0}],
        semantic_kind="segmented",
        axes={"dimensions": [{"field": "segment"}]},
    )
    with pytest.raises(SemanticKindMismatchError):
        session.hypothesis_test(ts, segmented)

    result = session.hypothesis_test(ts, ts)
    loaded = load_frame(result.ref, session=session)
    assert loaded.meta.kind == "hypothesis_test_result"
    assert loaded.lineage.steps[-1].intent == "test"
