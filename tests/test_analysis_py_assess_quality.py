from __future__ import annotations

import json

import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import QualityShapeUnsupportedError
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.lineage import Lineage
from marivo.analysis_py.session._load import load_frame
from tests.shared_fixtures import seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _reset_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _metric(session, rows, *, semantic_kind="time_series", axes=None, window=None, measure=None):
    return MetricFrame.from_dataframe(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes or {"time": {"field": "time", "grain": "day"}},
        measure=measure or {"field": "value", "aggregation": "sum"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window=window
        or {"start": "2026-01-01", "end": "2026-01-05", "grain": "day", "time_field": "time"},
        session=session,
    )


def test_metric_time_series_full_coverage_ok(tmp_path):
    session = session_attach.create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)

    report = mv.assess_quality(frame, session=session)
    df = report.to_pandas()

    assert report.meta.kind == "quality_report"
    assert report.meta.overall_status == "ok"
    assert set(df["check_kind"]) == {"row_count", "null_ratio", "time_coverage"}
    assert report.meta.blocking_issue_count == 0


def test_metric_time_series_gap_warning_and_blocking(tmp_path):
    session = session_attach.create(name="demo")
    rows = [{"time": t, "value": 1.0} for t in pd.date_range("2026-01-01", periods=9, freq="D")]
    warning = _metric(
        session,
        rows,
        window={"start": "2026-01-01", "end": "2026-01-10", "grain": "day", "time_field": "time"},
    )
    warning_report = mv.assess_quality(warning, session=session)
    assert warning_report.meta.overall_status == "warning"

    blocking = _metric(
        session,
        rows[:6],
        window={"start": "2026-01-01", "end": "2026-01-10", "grain": "day", "time_field": "time"},
    )
    blocking_report = mv.assess_quality(blocking, session=session)
    assert blocking_report.meta.overall_status == "blocking"
    assert blocking_report.meta.recommended_followups[0].operator == "observe"


def test_metric_segmented_duplicate_keys_blocking(tmp_path):
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        [{"segment": "US", "value": 1.0}, {"segment": "US", "value": 2.0}],
        semantic_kind="segmented",
        axes={"dimensions": [{"field": "segment"}]},
        window=None,
    )

    report = mv.assess_quality(frame, session=session)
    duplicate = report.to_pandas().set_index("check_kind").loc["duplicate_keys"]

    assert duplicate["severity"] == "blocking"
    assert report.meta.blocking_issues[0].kind == "quality"
    assert json.loads(duplicate["details_json"])["duplicate_count"] == 2


def test_null_ratio_per_measure_and_row_count_zero(tmp_path):
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        [
            {"time": pd.Timestamp("2026-01-01"), "value": None, "value2": 1.0},
            {"time": pd.Timestamp("2026-01-02"), "value": None, "value2": None},
        ],
        measure={"fields": ["value", "value2"]},
    )
    report = mv.assess_quality(frame, session=session)
    ids = set(report.to_pandas()["check_id"])
    assert {"null_ratio:value", "null_ratio:value2"}.issubset(ids)
    assert report.meta.recommended_followups[0].params == {"op": "impute_nulls"}

    empty = _metric(session, [], semantic_kind="scalar", axes={})
    empty_report = mv.assess_quality(empty, session=session)
    assert empty_report.meta.overall_status == "blocking"
    assert empty_report.meta.blocking_issues[0].kind == "sample_size"


def test_panel_all_checks_and_persistence(tmp_path):
    session = session_attach.create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5, segments=["US", "CA"])

    report = mv.assess_quality(frame, session=session)
    loaded = load_frame(report.ref, session=session)

    assert {"row_count", "time_coverage", "duplicate_keys"}.issubset(
        set(report.to_pandas()["check_kind"])
    )
    assert loaded.meta.kind == "quality_report"
    assert loaded.lineage.steps[-1].intent == "assess_quality"


def test_non_metric_frame_raises(tmp_path):
    session = session_attach.create(name="demo")
    delta = DeltaFrame(
        _df=pd.DataFrame({"delta": [1.0]}),
        meta=DeltaFrameMeta(
            kind="delta_frame",
            ref="frame_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=pd.Timestamp.utcnow().to_pydatetime(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            source_a_ref="frame_a",
            source_b_ref="frame_b",
            alignment={},
            semantic_model="sales",
            semantic_kind="time_series",
        ),
    )
    with pytest.raises(QualityShapeUnsupportedError):
        mv.assess_quality(delta, session=session)
