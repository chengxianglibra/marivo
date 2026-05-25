"""mv.discover emits candidate_set artifacts for point anomalies."""

import pandas as pd
import pytest
from pandas.api.types import is_float_dtype, is_integer_dtype

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    SessionStateError,
)
from marivo.analysis_py.frames.candidate import CandidateSet
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


def test_discover_point_anomalies_returns_candidate_set():
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c", "d"], "value": [-100.0, 0.0, 0.0, 100.0]}),
    )

    out = mv.discover(
        frame,
        objective="point_anomalies",
        strategy="zscore",
        threshold=1.0,
        session=session,
    )

    assert isinstance(out, CandidateSet)
    assert out.meta.kind == "candidate_set"
    assert out.meta.objective == "point_anomalies"
    assert out.meta.strategy == "zscore"
    assert out.meta.source_ref == frame.ref
    assert out.meta.metric_ids == ["sales.revenue"]
    assert out.meta.params == {
        "source_ref": frame.ref,
        "objective": "point_anomalies",
        "strategy": "zscore",
        "value": None,
        "threshold": 1.0,
    }
    df = out.to_pandas()
    assert list(df.columns) == [
        "candidate_id",
        "source_ref",
        "source_row_index",
        "value_column",
        "observed_value",
        "score",
        "direction",
        "threshold",
        "keys_json",
    ]
    assert list(df["direction"]) == ["low", "high"]
    assert list(df["threshold"]) == [1.0, 1.0]
    assert list(df["source_row_index"]) == [0, 3]
    assert list(df["source_ref"]) == [frame.ref, frame.ref]
    assert list(df["value_column"]) == ["value", "value"]
    assert list(df["observed_value"]) == [-100.0, 100.0]
    assert list(df["keys_json"]) == ['{"bucket": "a"}', '{"bucket": "d"}']


def test_discover_zero_std_returns_empty_candidate_set():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b", "c"], "value": [5.0, 5.0, 5.0]}))

    out = mv.discover(frame, objective="point_anomalies", strategy="zscore", session=session)

    assert out.to_pandas().empty
    assert list(out.to_pandas().columns) == [
        "candidate_id",
        "source_ref",
        "source_row_index",
        "value_column",
        "observed_value",
        "score",
        "direction",
        "threshold",
        "keys_json",
    ]
    assert out.meta.row_count == 0


def test_discover_empty_candidate_set_round_trips_with_numeric_schema():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b", "c"], "value": [5.0, 5.0, 5.0]}))

    out = mv.discover(frame, objective="point_anomalies", strategy="zscore", session=session)
    loaded = mv.load_frame(out.ref, session=session)

    df = loaded.to_pandas()
    assert df.empty
    assert is_integer_dtype(df["source_row_index"])
    assert is_float_dtype(df["observed_value"])
    assert is_float_dtype(df["score"])
    assert is_float_dtype(df["threshold"])


def test_discover_single_non_null_value_returns_empty_candidate_set():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b"], "value": [None, 5.0]}))

    out = mv.discover(frame, objective="point_anomalies", session=session)

    assert out.to_pandas().empty
    assert out.meta.row_count == 0


def test_discover_uses_explicit_numeric_value_column():
    session = session_attach.create(name="demo")
    frame = _metric(
        session, pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0], "count": [3, 9]})
    )

    out = mv.discover(
        frame, objective="point_anomalies", value="count", threshold=1.0, session=session
    )

    assert list(out.to_pandas()["value_column"]) == ["count", "count"]
    assert out.meta.params["value"] == "count"


@pytest.mark.parametrize("semantic_kind", ["scalar", "segmented"])
def test_discover_rejects_unsupported_point_anomaly_semantic_kinds(semantic_kind):
    session = session_attach.create(name="demo")
    frame = _metric(
        session, pd.DataFrame({"segment": ["a"], "value": [1.0]}), semantic_kind=semantic_kind
    )

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", session=session)


def test_discover_accepts_panel_metric_frame():
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame(
            {"bucket": ["a", "b", "c"], "region": ["x", "x", "y"], "value": [1.0, 2.0, 99.0]}
        ),
        semantic_kind="panel",
    )

    out = mv.discover(frame, objective="point_anomalies", threshold=1.0, session=session)

    assert isinstance(out, CandidateSet)
    assert out.meta.semantic_kind == "panel"


def test_discover_rejects_non_metric_frame():
    session = session_attach.create(name="demo")

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(object(), objective="point_anomalies", session=session)  # type: ignore[arg-type]


def test_discover_rejects_ambiguous_numeric_columns():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", session=session)


def test_discover_rejects_missing_explicit_value_column():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", value="missing", session=session)


def test_discover_rejects_non_numeric_explicit_value_column():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", value="bucket", session=session)


def test_discover_rejects_unknown_objective():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="driver_axes", session=session)  # type: ignore[arg-type]


def test_discover_rejects_unsupported_strategy():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", strategy="iqr", session=session)  # type: ignore[arg-type]


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_discover_rejects_invalid_threshold(threshold):
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", threshold=threshold, session=session)


@pytest.mark.parametrize("threshold", ["3", None, True])
def test_discover_rejects_non_numeric_threshold(threshold):
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="point_anomalies", threshold=threshold, session=session)  # type: ignore[arg-type]


def test_discover_writes_job_and_frame():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 99.0]}))

    out = mv.discover(frame, objective="point_anomalies", threshold=1.0, session=session)

    jobs = [job for job in session.jobs() if job.intent == "discover"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session.layout.frames_dir / out.ref / "data.parquet").is_file()
    record = session.job(jobs[0].id)
    assert record["params"]["objective"] == "point_anomalies"
    assert record["params"]["strategy"] == "zscore"
    assert record["params"]["source_ref"] == frame.ref


def test_discover_rejects_cross_session_frame():
    session_a = session_attach.create(name="a")
    frame = _metric(session_a, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_b = session_attach.create(name="b")

    with pytest.raises(CrossSessionFrameError):
        mv.discover(frame, objective="point_anomalies", session=session_b)


def test_discover_archived_session_raises():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_attach.archive("demo")

    with pytest.raises(SessionStateError):
        mv.discover(frame, objective="point_anomalies", session=session)


def test_discover_stale_archived_session_raises():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert session.state == "active"

    with pytest.raises(SessionStateError):
        mv.discover(frame, objective="point_anomalies", session=session)


def test_detect_is_not_public_api():
    assert hasattr(mv, "discover")
    assert not hasattr(mv, "detect")
