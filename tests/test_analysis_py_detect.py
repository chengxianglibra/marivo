"""mv.detect marks z-score anomalies in MetricFrames."""

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
from marivo.analysis_py.frames.metric import MetricFrame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, semantic_kind="time_series"):
    return MetricFrame.from_dataframe(
        df,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def test_detect_marks_high_and_low_anomalies():
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c", "d"], "value": [-100.0, 0.0, 0.0, 100.0]}),
    )

    out = mv.detect(frame, threshold=1.0, session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.attribution_kind == "anomaly"
    assert out.meta.value_column == "value"
    assert out.meta.method == "zscore"
    assert out.meta.metric_ids == ["sales.revenue"]
    df = out.to_pandas()
    assert list(df.columns) == ["bucket", "value", "score", "is_anomaly", "direction", "threshold"]
    assert list(df["is_anomaly"]) == [True, False, False, True]
    assert list(df["direction"]) == ["low", "normal", "normal", "high"]
    assert list(df["threshold"]) == [1.0, 1.0, 1.0, 1.0]


def test_detect_zero_std_returns_no_anomalies():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b", "c"], "value": [5.0, 5.0, 5.0]}))

    out = mv.detect(frame, session=session)

    df = out.to_pandas()
    assert list(df["score"]) == [0.0, 0.0, 0.0]
    assert list(df["is_anomaly"]) == [False, False, False]
    assert list(df["direction"]) == ["normal", "normal", "normal"]


def test_detect_single_non_null_value_returns_no_anomalies():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b"], "value": [None, 5.0]}))

    out = mv.detect(frame, session=session)

    df = out.to_pandas()
    assert list(df["score"]) == [0.0, 0.0]
    assert list(df["is_anomaly"]) == [False, False]


def test_detect_uses_explicit_numeric_value_column():
    session = session_attach.create(name="demo")
    frame = _metric(
        session, pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0], "count": [3, 9]})
    )

    out = mv.detect(frame, value="count", threshold=1.0, session=session)

    assert out.meta.value_column == "count"
    assert out.meta.params["value"] == "count"


def test_detect_rejects_ambiguous_numeric_columns():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.detect(frame, session=session)


def test_detect_rejects_missing_explicit_value_column():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.detect(frame, value="missing", session=session)


def test_detect_rejects_non_numeric_explicit_value_column():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.detect(frame, value="bucket", session=session)


def test_detect_rejects_unsupported_method():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.detect(frame, method="iqr", session=session)  # type: ignore[arg-type]


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_detect_rejects_invalid_threshold(threshold):
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.detect(frame, threshold=threshold, session=session)


@pytest.mark.parametrize("threshold", ["3", None, True])
def test_detect_rejects_non_numeric_threshold(threshold):
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.detect(frame, threshold=threshold, session=session)  # type: ignore[arg-type]


def test_detect_rejects_reserved_output_column_collision_without_persisting():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0], "score": [0.0, 0.0]}))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        mv.detect(frame, session=session)

    assert exc_info.value.details == {"collisions": ["score"]}
    assert [job for job in session.jobs() if job.intent == "detect"] == []


def test_detect_writes_job_and_frame():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 99.0]}))

    out = mv.detect(frame, threshold=1.0, session=session)

    jobs = [job for job in session.jobs() if job.intent == "detect"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session.layout.frames_dir / out.ref / "data.parquet").is_file()
    assert session.job(jobs[0].id)["params"]["threshold"] == 1.0


def test_detect_rejects_cross_session_frame():
    session_a = session_attach.create(name="a")
    frame = _metric(session_a, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_b = session_attach.create(name="b")
    with pytest.raises(CrossSessionFrameError):
        mv.detect(frame, session=session_b)


def test_detect_archived_session_raises():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        mv.detect(frame, session=session)


def test_detect_stale_archived_session_raises():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert session.state == "active"
    with pytest.raises(SessionStateError):
        mv.detect(frame, session=session)
