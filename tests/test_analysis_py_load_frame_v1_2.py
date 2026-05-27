"""v1.2 frame loading compatibility checks."""

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import FrameMetaInvalidError
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.lineage import Lineage, LineageStep
from marivo.analysis_py.session.persistence import write_frame_to_disk


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
            ]
        ),
    }


def test_load_frame_coerces_legacy_window_dict():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session.layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {
        "start": "2026-05-01",
        "end": "2026-05-24",
        "rogue_key": "drop-me",
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    loaded = mv.load_frame(frame.ref, session=session)

    assert loaded.meta.window is not None
    assert loaded.meta.window["kind"] == "absolute"
    assert loaded.meta.window["start"] == "2026-05-01"
    assert loaded.meta.window["end"] == "2026-05-24"
    assert "rogue_key" not in loaded.meta.window


def test_load_frame_rejects_unparseable_legacy_window():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session.layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {"foo": "bar"}
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        mv.load_frame(frame.ref, session=session)

    assert exc_info.value.details.get("kind") == "LegacyWindowShapeInvalid"


def test_load_frame_wraps_legacy_window_validation_error():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [1.0]}),
        metric_id="custom.metric",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="custom",
        session=session,
    )
    meta_file = session.layout.frames_dir / frame.ref / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["window"] = {
        "start": "2026-05-01",
        "end": "2026-05-24",
        "grain": "invalid-grain",
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        mv.load_frame(frame.ref, session=session)

    assert exc_info.value.details.get("kind") == "LegacyWindowShapeInvalid"


def test_load_frame_round_trips_hypothesis_test_result():
    from marivo.analysis_py.frames.hypothesis import (
        HypothesisTestResult,
        HypothesisTestResultMeta,
    )

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
            alignment={"kind": "calendar_bucket"},
            sampling={"unit": "bucket"},
            alpha=0.05,
            result_shape="single",
            segment_dimensions=[],
            rejected_count=1,
            not_enough_data_count=0,
        ),
    )
    frame.meta = write_frame_to_disk(session.layout, frame)

    loaded = mv.load_frame("frame_test", session=session)

    assert isinstance(loaded, HypothesisTestResult)
    assert loaded.meta.kind == "hypothesis_test_result"
    assert loaded.meta.hypothesis == "mean_changed"
    assert loaded.to_pandas().iloc[0]["p_value"] == 0.01


def test_load_frame_round_trips_forecast_frame():
    from marivo.analysis_py.frames.forecast import ForecastFrame, ForecastFrameMeta

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
    frame.meta = write_frame_to_disk(session.layout, frame)

    loaded = mv.load_frame("frame_forecast", session=session)

    assert isinstance(loaded, ForecastFrame)
    assert loaded.meta.kind == "forecast_frame"
    assert loaded.meta.horizon == 1
    assert loaded.to_pandas().iloc[0]["forecast"] == 12.0


def test_load_frame_round_trips_quality_report():
    from marivo.analysis_py.frames.quality import QualityReport, QualityReportMeta

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
    frame.meta = write_frame_to_disk(session.layout, frame)

    loaded = mv.load_frame("frame_quality", session=session)

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
        mv.hypothesis_test(frame, frame, session=session),
        mv.forecast(frame, horizon=2, model="naive", session=session),
        mv.assess_quality(frame, session=session),
    ]

    assert [mv.load_frame(output.ref, session=session).meta.kind for output in outputs] == [
        "hypothesis_test_result",
        "forecast_frame",
        "quality_report",
    ]
