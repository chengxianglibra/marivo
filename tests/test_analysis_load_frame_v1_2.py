"""v1.2 frame loading compatibility checks."""

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    CrossSessionFrameError,
    FrameCacheCorruptedError,
    FrameMetaInvalidError,
    FrameRefNotFound,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._layout import write_frame_to_disk
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _base_meta(session, *, kind, ref):
    return {
        "kind": kind,
        "ref": ref,
        "session_id": session.id,
        "project_root": str(session.project_root),
        "produced_by_job": "job_test",
        "created_at": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        "row_count": 1,
        "byte_size": 0,
        "lineage": Lineage(
            steps=[
                LineageStep(
                    intent="test",
                    job_ref="job_test",
                    inputs=[],
                    params_digest="sha256:test",
                )
            ],
        ),
    }


def test_load_frame_coerces_legacy_window_dict():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session._layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {
        "start": "2026-05-01",
        "end": "2026-05-24",
        "rogue_key": "drop-me",
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    loaded = session.get_frame(frame.ref)

    assert loaded.meta.window is not None
    assert loaded.meta.window["kind"] == "absolute"
    assert loaded.meta.window["start"] == "2026-05-01"
    assert loaded.meta.window["end"] == "2026-05-24"
    assert "rogue_key" not in loaded.meta.window


def test_load_frame_rejects_unparseable_legacy_window():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session._layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {"foo": "bar"}
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(frame.ref)

    assert exc_info.value.details.get("kind") == "LegacyWindowShapeInvalid"


def test_load_frame_wraps_legacy_window_validation_error():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session._layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {
        "start": "2026-05-01",
        "end": "2026-05-24",
        "grain": "invalid-grain",
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(frame.ref)

    assert exc_info.value.details.get("kind") == "LegacyWindowShapeInvalid"


def test_load_frame_round_trips_hypothesis_test_result():
    from marivo.analysis.frames.hypothesis import (
        HypothesisTestResult,
        HypothesisTestResultMeta,
    )
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    frame = HypothesisTestResult(
        _df=pd.DataFrame({"p_value": [0.01], "rejected": [True]}),
        meta=HypothesisTestResultMeta(
            **_base_meta(session, kind="hypothesis_test_result", ref="frame_test"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.revenue"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            hypothesis="mean_changed",
            method="paired_t",
            alignment={"kind": "window_bucket"},
            sampling={"unit": "bucket"},
            alpha=0.05,
            result_shape="single",
            segment_dimensions=[],
            rejected_count=1,
            not_enough_data_count=0,
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_test")

    assert isinstance(loaded, HypothesisTestResult)
    assert loaded.meta.kind == "hypothesis_test_result"
    assert loaded.meta.hypothesis == "mean_changed"
    assert loaded.to_pandas().iloc[0]["p_value"] == 0.01


def test_load_frame_round_trips_forecast_frame():
    from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    frame = ForecastFrame(
        _df=pd.DataFrame({"time": ["2026-06-01"], "forecast": [12.0]}),
        meta=ForecastFrameMeta(
            **_base_meta(session, kind="forecast_frame", ref="frame_forecast"),
            source_refs=["frame_history"],
            metric_id="sales.revenue",
            semantic_model="sales",
            semantic_kind="time_series",
            measure={"field": "value"},
            axes={"time": {"field": "time", "grain": "day"}},
            history_window={"start": "2026-01-01", "end": "2026-05-31"},
            forecast_window={"start": "2026-06-01", "end": "2026-06-01"},
            horizon=1,
            horizon_unit="day",
            model="naive",
            seasonality_period=None,
            interval_level=0.95,
            interval_method="normal_residual",
            train_row_count_per_segment={"__all__": 30},
            segment_dimensions=[],
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_forecast")

    assert isinstance(loaded, ForecastFrame)
    assert loaded.meta.kind == "forecast_frame"
    assert loaded.meta.horizon == 1
    assert loaded.to_pandas().iloc[0]["forecast"] == 12.0


def test_load_frame_round_trips_quality_report():
    from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
    from marivo.analysis.session._runtime import persist_frame

    session = session_attach.get_or_create(name="demo")
    frame = QualityReport(
        _df=pd.DataFrame({"check": ["missing_values"], "status": ["ok"]}),
        meta=QualityReportMeta(
            **_base_meta(session, kind="quality_report", ref="frame_quality"),
            source_refs=["frame_metric"],
            report_shape="metric",
            target_kind="metric_frame",
            target_metric_id="sales.revenue",
            target_semantic_model="sales",
            target_semantic_kind="time_series",
            checks_run=["missing_values"],
            overall_status="ok",
            blocking_issue_count=0,
            warning_count=0,
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_quality")

    assert isinstance(loaded, QualityReport)
    assert loaded.meta.kind == "quality_report"
    assert loaded.meta.overall_status == "ok"
    assert loaded.to_pandas().iloc[0]["check"] == "missing_values"


def test_loads_new_operator_frame_families(tmp_path, monkeypatch):
    from tests.shared_fixtures import seeded_time_series_metric_frame

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=10)

    outputs = [
        session.hypothesis_test(frame, frame),
        session.forecast(frame, horizon=2, model="naive"),
        session.assess_quality(frame),
    ]

    assert [session.get_frame(output.ref).meta.kind for output in outputs] == [
        "hypothesis_test_result",
        "forecast_frame",
        "quality_report",
    ]


def test_session_get_frame_accepts_ref_string():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    loaded = session.get_frame(frame.ref)
    assert isinstance(loaded, MetricFrame)
    assert loaded.ref == frame.ref


def test_session_get_frame_ref_not_found():
    session = session_attach.get_or_create(name="demo")
    with pytest.raises(FrameRefNotFound):
        session.get_frame("frame_nonexistent")


# ---------------------------------------------------------------------------
# Store-backed frame loading tests
# ---------------------------------------------------------------------------


def test_resolve_frame_session_uses_persisted_project_root_for_connection_runtime(
    tmp_path, monkeypatch
):
    """Resolving a frame session from another cwd keeps datasource lookup project-scoped."""
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()

    monkeypatch.chdir(project_a)
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    session_attach._reset_process_state()

    monkeypatch.chdir(project_b)
    from marivo.analysis.session._resolve import resolve_frame_session

    resolved = resolve_frame_session(frame.meta.session_id, frame.meta.project_root)

    assert resolved.project_root == project_a.resolve()
    assert resolved._connection_runtime.service.project_root == project_a.resolve()


def test_frame_file_without_artifacts_row_is_unreachable():
    """A frame file on disk without an artifacts store row cannot be loaded."""
    session = session_attach.get_or_create(name="demo")
    # Write a frame directly to disk without using persist_frame,
    # so no store row is created.
    from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
    from marivo.analysis.lineage import Lineage, LineageStep

    ref = "frame_orphan"
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=None,
        created_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        row_count=1,
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="test_orphan",
                    job_ref=None,
                    inputs=[],
                    params_digest="test",
                )
            ],
        ),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        window=None,
        where={},
        semantic_kind="scalar",
        semantic_model="custom",
    )
    frame = MetricFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)
    # Write to disk only (no store registration).
    write_frame_to_disk(session._layout, frame)
    # Attempting to load it should raise FrameRefNotFound.
    with pytest.raises(FrameRefNotFound):
        session.get_frame(ref)


def test_registered_frame_with_missing_bytes_raises_corrupted_error():
    """A registered frame whose data.parquet is deleted raises FrameCacheCorruptedError."""
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    # make_metric_frame uses persist_frame, so the frame is registered.
    # Delete the data file to simulate corruption.
    data_path = session._layout.frames_dir / frame.ref / "data.parquet"
    data_path.unlink()
    with pytest.raises(FrameCacheCorruptedError):
        session.get_frame(frame.ref)


def test_cross_session_frame_raises_cross_session_frame_error():
    """A frame registered to another session raises CrossSessionFrameError."""
    session_a = session_attach.get_or_create(name="session_a")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session_a,
    )

    # Create a second session. Manually register the frame ref in session_b's
    # store so that the store lookup passes, but point the paths to session_a's
    # files. The meta.json will say session_id=session_a.id, triggering
    # CrossSessionFrameError.
    session_b = session_attach.get_or_create(name="session_b")
    session_b._store.record_artifact(
        session_id=session_b.id,
        artifact_id=frame.ref,
        kind=frame.meta.kind,
        path=session_a._layout.relative_path(
            session_a._layout.frames_dir / frame.ref / "data.parquet"
        ),
        meta_path=session_a._layout.relative_path(
            session_a._layout.frames_dir / frame.ref / "meta.json"
        ),
        content_hash=None,
        produced_by_job=None,
    )
    with pytest.raises(CrossSessionFrameError):
        session_b.get_frame(frame.ref)
