"""session.discover emits candidate_set artifacts for point anomalies."""

import json

import pandas as pd
import pytest
from pandas.api.types import is_float_dtype

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    AnalysisError,
    CrossSessionFrameError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.candidate import CandidateSet
from marivo.analysis.intents._candidate_columns import CANDIDATE_COLUMNS
from tests.run_read_helpers import run_arguments
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, semantic_kind="time_series"):
    return make_metric_frame(
        df,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def test_discover_point_anomalies_returns_candidate_set():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c", "d"], "value": [-100.0, 0.0, 0.0, 100.0]}),
    )

    out = session.discover.point_anomalies(
        frame,
        threshold=1.0,
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
        "value": "value",
        "threshold": 1.0,
        "limit": 50,
    }
    df = out.to_pandas()
    assert list(df.columns) == CANDIDATE_COLUMNS
    assert all(
        isinstance(item_id, str)
        and item_id.startswith("candidate_")
        and len(item_id) == len("candidate_") + 64
        for item_id in df["item_id"]
    )
    assert df["item_id"].is_unique
    assert list(df["direction"]) == ["low", "high"]
    keys = [json.loads(k) for k in df["keys_json"]]
    assert keys == [{"bucket": "a"}, {"bucket": "d"}]
    source_refs = [json.loads(s) for s in df["source_refs_json"]]
    assert source_refs == [[f"{frame.ref}#row=0"], [f"{frame.ref}#row=3"]]
    assert "affordances_json" not in df.columns


def test_discover_zero_std_returns_empty_candidate_set():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b", "c"], "value": [5.0, 5.0, 5.0]}))

    out = session.discover.point_anomalies(frame)

    assert out.to_pandas().empty
    assert list(out.to_pandas().columns) == CANDIDATE_COLUMNS
    assert out.meta.row_count == 0


def test_discover_empty_candidate_set_round_trips_with_numeric_schema():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b", "c"], "value": [5.0, 5.0, 5.0]}))

    out = session.discover.point_anomalies(frame)
    loaded = session.artifact(out.ref)

    df = loaded.to_pandas()
    assert df.empty
    assert is_float_dtype(df["score"])


def test_discover_single_non_null_value_returns_empty_candidate_set():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b"], "value": [None, 5.0]}))

    out = session.discover.point_anomalies(frame)

    assert out.to_pandas().empty
    assert out.meta.row_count == 0


def test_discover_rejects_non_measure_numeric_value_column():
    """An explicit value must name the MetricFrame's declared measure (public
    ``revenue`` or internal ``value``); an unrelated numeric column is not a
    discover target (issue #118 review)."""
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session, pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0], "count": [3, 9]})
    )

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.discover.point_anomalies(frame, value="count", threshold=1.0)

    assert exc.value.repair is not None


def test_discover_accepts_public_measure_name_as_explicit_value():
    """An explicit value may name the MetricFrame's public measure column
    (``value_columns``), not just the internal ``value`` storage column
    (issue #118 review: public/internal column vocabularies must agree)."""
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0], "count": [3, 9]}),
    )

    out = session.discover.point_anomalies(frame, value="revenue", threshold=1.0)

    assert out.meta.params["value"] == "value"


def test_discover_public_measure_name_resolves_same_as_internal_name():
    """Explicit public name ``value=\"revenue\"`` and internal name
    ``value=\"value\"`` resolve to the identical scored result."""
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": ["a", "b", "c", "d"],
                "value": [-100.0, 0.0, 0.0, 100.0],
            }
        ),
    )

    by_public = session.discover.point_anomalies(frame, value="revenue", threshold=1.0)
    by_internal = session.discover.point_anomalies(frame, value="value", threshold=1.0)

    assert by_public.meta.params["value"] == "value"
    assert by_internal.meta.params["value"] == "value"
    assert list(by_public.to_pandas()["item_id"]) == list(by_internal.to_pandas()["item_id"])


@pytest.mark.parametrize("semantic_kind", ["scalar", "segmented"])
def test_discover_rejects_unsupported_point_anomaly_semantic_kinds(semantic_kind):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session, pd.DataFrame({"segment": ["a"], "value": [1.0]}), semantic_kind=semantic_kind
    )

    with pytest.raises(SemanticKindMismatchError):
        session.discover.point_anomalies(frame)


def test_discover_accepts_panel_metric_frame():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame(
            {"bucket": ["a", "b", "c"], "region": ["x", "x", "y"], "value": [1.0, 2.0, 99.0]}
        ),
        semantic_kind="panel",
    )

    out = session.discover.point_anomalies(frame, threshold=1.0)

    assert isinstance(out, CandidateSet)
    assert out.meta.semantic_kind == "panel"


def test_discover_rejects_non_metric_frame():
    session = session_attach.get_or_create(name="demo")

    with pytest.raises(AnalysisError) as exc:
        session.discover.point_anomalies(object())  # type: ignore[arg-type]

    assert exc.value.location == "discover.point_anomalies.source"


def test_discover_rejects_ambiguous_numeric_columns():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]}))

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.discover.point_anomalies(frame)

    assert exc.value.repair is not None
    assert set(exc.value.repair.candidates) == {"a", "b"}


def test_discover_rejects_missing_explicit_value_column():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.discover.point_anomalies(frame, value="missing")

    assert exc.value.repair is not None
    assert exc.value.repair.candidates == ("value",)


def test_discover_rejects_non_numeric_explicit_value_column():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.discover.point_anomalies(frame, value="bucket")

    assert exc.value.repair is not None
    assert exc.value.repair.candidates == ("value",)


def test_discover_metric_frame_omitted_value_defaults_to_measure_column():
    """An extra numeric column no longer makes omitted value ambiguous: the
    MetricFrame's declared measure/value column wins (issue #118)."""
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": ["a", "b", "c", "d"],
                "value": [-100.0, 0.0, 0.0, 100.0],
                "count": [10, 20, 30, 40],
            }
        ),
    )

    out = session.discover.point_anomalies(frame, threshold=1.0)

    assert out.meta.params["value"] == "value"
    assert list(out.to_pandas()["direction"]) == ["low", "high"]


def test_discover_rejects_unknown_objective():
    session = session_attach.get_or_create(name="demo")
    name = "not_an_objective"

    with pytest.raises(AttributeError):
        getattr(session.discover, name)


def test_discover_rejects_unsupported_strategy():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))

    with pytest.raises(SemanticKindMismatchError):
        session.discover.point_anomalies(frame, strategy="iqr")  # type: ignore[arg-type]


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_discover_rejects_invalid_threshold(threshold):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))
    run_count = len(session._store.list_runs(session.id))

    with pytest.raises(SemanticKindMismatchError):
        session.discover.point_anomalies(frame, threshold=threshold)
    assert len(session._store.list_runs(session.id)) == run_count


@pytest.mark.parametrize("threshold", ["3", True])
def test_discover_rejects_non_numeric_threshold(threshold):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}))
    run_count = len(session._store.list_runs(session.id))

    with pytest.raises(SemanticKindMismatchError):
        session.discover.point_anomalies(frame, threshold=threshold)  # type: ignore[arg-type]
    assert len(session._store.list_runs(session.id)) == run_count


def test_discover_writes_job_and_frame():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 99.0]}))

    out = session.discover.point_anomalies(frame, threshold=1.0)

    jobs = [
        job
        for job in session.runs(limit=100).items
        if job.capability_id == "discover.point_anomalies"
    ]
    assert len(jobs) == 1
    assert jobs[0].output_artifact_ref == out.ref
    assert (session._layout.frames_dir / out.ref / "data.parquet").is_file()
    record = session.get_run(jobs[0].run_id)
    arguments = run_arguments(record)
    assert arguments["threshold"] == 1.0
    assert record.capability_id == "discover.point_anomalies"


def test_discover_rejects_cross_session_frame():
    session_a = session_attach.get_or_create(name="a")
    frame = _metric(session_a, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    session_b = session_attach.get_or_create(name="b")

    with pytest.raises(CrossSessionFrameError):
        session_b.discover.point_anomalies(frame)


def test_discover_read_only_session_without_backend_raises():
    # Create a writable session to build the metric frame first.
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    # Re-open without backend factory -> read-only.
    session_attach._reset_process_state()
    session_ro = session_attach.get_or_create(name="demo", use_datasources=False)

    with pytest.raises(NoBackendFactoryError):
        session_ro.discover.point_anomalies(frame)


def test_discover_stale_session_without_backend_raises():
    # Create a writable session to build the metric frame first.
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    # Re-open without backend factory -> read-only.
    session_attach._reset_process_state()
    session_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    with pytest.raises(NoBackendFactoryError):
        session_ro.discover.point_anomalies(frame)


def test_detect_is_not_public_api():
    assert not hasattr(mv, "discover")
    assert not hasattr(mv, "detect")


def test_discover_point_anomalies_object_dtype_time_column():
    """bucket_start with datetime.date objects (object dtype) is still detected as time."""
    import datetime

    session = session_attach.get_or_create(name="demo")
    dates = [datetime.date(2026, 1, i) for i in range(1, 9)]
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": dates,
                "value": [-100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 100.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {"role": "time", "column": "bucket_start", "grain": "day"},
        },
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    out = session.discover.point_anomalies(frame, threshold=1.0)
    df = out.to_pandas()

    assert len(df) == 2

    # window_start should contain actual dates, not Timestamp.now()
    for ts in df["window_start"]:
        assert ts.year == 2026
        assert ts.month == 1

    # bucket_start should NOT appear in keys_json
    keys = [json.loads(k) for k in df["keys_json"]]
    for key_dict in keys:
        assert "bucket_start" not in key_dict
