from __future__ import annotations

import json

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import QualityShapeUnsupportedError
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._load import load_frame
from tests.shared_fixtures import make_metric_frame, seeded_time_series_metric_frame


@pytest.fixture(autouse=True)
def _reset_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _metric(session, rows, *, semantic_kind="time_series", axes=None, window=None, measure=None):
    return make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes or {"time": {"field": "time", "grain": "day"}},
        measure=measure or {"field": "value", "aggregation": "sum"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        window=window
        or {"start": "2026-01-01", "end": "2026-01-05", "grain": "day", "time_dimension": "time"},
        session=session,
    )


def test_metric_time_series_full_coverage_ok(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)

    report = session.assess_quality(frame)
    df = report.to_pandas()

    assert report.meta.kind == "quality_report"
    assert report.meta.overall_status == "ok"
    assert set(df["check_kind"]) == {"row_count", "null_ratio", "time_coverage"}
    assert report.meta.blocking_issue_count == 0


def test_metric_time_series_gap_warning_and_blocking(tmp_path):
    session = session_attach.get_or_create(name="demo")
    rows = [{"time": t, "value": 1.0} for t in pd.date_range("2026-01-01", periods=9, freq="D")]
    warning = _metric(
        session,
        rows,
        window={
            "start": "2026-01-01",
            "end": "2026-01-11",
            "grain": "day",
            "time_dimension": "time",
        },
    )
    warning_report = session.assess_quality(warning)
    assert warning_report.meta.overall_status == "warning"

    blocking = _metric(
        session,
        rows[:6],
        window={
            "start": "2026-01-01",
            "end": "2026-01-11",
            "grain": "day",
            "time_dimension": "time",
        },
    )
    blocking_report = session.assess_quality(blocking)
    assert blocking_report.meta.overall_status == "blocking"


def test_metric_segmented_duplicate_keys_blocking(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        [{"segment": "US", "value": 1.0}, {"segment": "US", "value": 2.0}],
        semantic_kind="segmented",
        axes={"dimensions": [{"field": "segment"}]},
        window=None,
    )

    report = session.assess_quality(frame)
    duplicate = report.to_pandas().set_index("check_kind").loc["duplicate_keys"]

    assert duplicate["severity"] == "blocking"
    assert report.meta.blocking_issues[0].kind == "quality"
    assert json.loads(duplicate["details_json"])["duplicate_count"] == 2


def test_null_ratio_per_measure_and_row_count_zero(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        [
            {"time": pd.Timestamp("2026-01-01"), "value": None, "value2": 1.0},
            {"time": pd.Timestamp("2026-01-02"), "value": None, "value2": None},
        ],
        measure={"fields": ["value", "value2"]},
    )
    report = session.assess_quality(frame)
    ids = set(report.to_pandas()["check_id"])
    assert {"null_ratio:value", "null_ratio:value2"}.issubset(ids)

    empty = _metric(session, [], semantic_kind="scalar", axes={})
    empty_report = session.assess_quality(empty)
    assert empty_report.meta.overall_status == "blocking"
    assert empty_report.meta.blocking_issues[0].kind == "sample_size"


def test_panel_all_checks_and_persistence(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5, segments=["US", "CA"])

    report = session.assess_quality(frame)
    loaded = load_frame(report.ref, session=session)

    assert {"row_count", "time_coverage", "duplicate_keys"}.issubset(
        set(report.to_pandas()["check_kind"])
    )
    assert loaded.meta.kind == "quality_report"
    assert loaded.lineage.steps[-1].intent == "assess_quality"


def test_non_metric_frame_raises(tmp_path):
    session = session_attach.get_or_create(name="demo")
    delta = DeltaFrame(
        _df=pd.DataFrame({"delta": [1.0]}),
        meta=DeltaFrameMeta(
            kind="delta_frame",
            ref="frame_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=pd.Timestamp.now("UTC").to_pydatetime(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            source_current_ref="frame_a",
            source_baseline_ref="frame_b",
            alignment={},
            semantic_model="sales",
            semantic_kind="time_series",
        ),
    )
    with pytest.raises(QualityShapeUnsupportedError):
        session.assess_quality(delta)


def test_summary_returns_quality_report_summary(tmp_path, capsys):
    from marivo.analysis.frames.quality import (
        CheckResult,
        QualityReportSummary,
    )
    from marivo.render import AgentResult

    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)
    report = session.assess_quality(frame)

    s = report.summary()
    assert isinstance(s, QualityReportSummary)
    assert isinstance(s, AgentResult)
    assert s.kind == "quality_report"
    assert s.overall_status == "ok"
    assert s.blocking_issue_count == 0
    assert s.warning_count == 0
    assert s.target_semantic_kind == "time_series"
    assert s.target_metric_id == "sales.revenue"
    assert len(s.checks) == 3
    assert all(isinstance(c, CheckResult) for c in s.checks)
    check_ids = [c.check_id for c in s.checks]
    assert "row_count" in check_ids
    assert all(c.status == "ok" for c in s.checks)

    r = repr(s)
    assert r == (
        f"<QualityReportSummary ref={report.ref} status=ok blocking=0; call .show() to inspect>"
    )
    assert "\n" not in r

    rendered = s.render()
    assert rendered.startswith(f"QualityReportSummary ref={report.ref} status=ok blocking=0")
    assert "status: ok; blocking=0 warning=0" in rendered
    assert "- .render()" in rendered
    assert "- .show()" in rendered
    assert not rendered.endswith("\n")

    assert s.show() is None
    captured = capsys.readouterr()
    assert captured.out == rendered + "\n"


def test_summary_reflects_blocking(tmp_path):
    session = session_attach.get_or_create(name="demo")
    empty = _metric(session, [], semantic_kind="scalar", axes={})
    report = session.assess_quality(empty)

    s = report.summary()
    assert s.overall_status == "blocking"
    assert s.blocking_issue_count >= 1
    assert any(c.status == "blocking" for c in s.checks)


def test_repr_contains_identity_and_show_hint(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)
    report = session.assess_quality(frame)

    r = repr(report)
    assert "QualityReport" in r
    assert f"ref={report.ref}" in r
    assert "status=ok" in r
    assert "blocking=0" in r
    assert "rows=3" in r
    assert "call .show() to inspect" in r


def test_summary_reflects_warning(tmp_path):
    session = session_attach.get_or_create(name="demo")
    rows = [{"time": t, "value": 1.0} for t in pd.date_range("2026-01-01", periods=9, freq="D")]
    warning = _metric(
        session,
        rows,
        window={
            "start": "2026-01-01",
            "end": "2026-01-11",
            "grain": "day",
            "time_dimension": "time",
        },
    )
    report = session.assess_quality(warning)

    s = report.summary()
    assert s.overall_status == "warning"
    assert s.warning_count >= 1
    assert s.blocking_issue_count == 0


def test_summary_scalar_without_metric_id(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, [], semantic_kind="scalar", axes={}, window=None)
    frame.meta.metric_id = None
    report = session.assess_quality(frame)

    s = report.summary()
    assert s.target_metric_id is None


# --- Panel duplicate_keys with observe-produced axes format ---


def test_panel_duplicate_keys_no_false_positive(tmp_path):
    """Panel frames with observe-produced axes (role='dimension') must not
    flag every row as a duplicate.  The key must be (time_col, dimension_col)."""
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"bucket_start": "2026-01-01", "region": "north", "value": 10.0},
        {"bucket_start": "2026-01-01", "region": "south", "value": 20.0},
        {"bucket_start": "2026-01-02", "region": "north", "value": 30.0},
        {"bucket_start": "2026-01-02", "region": "south", "value": 40.0},
    ]
    frame = _metric(
        session,
        rows,
        semantic_kind="panel",
        axes={
            "time": {"role": "time", "column": "bucket_start", "grain": "day"},
            "region": {"role": "dimension", "column": "region"},
        },
        measure={"field": "value"},
        window={
            "start": "2026-01-01",
            "end": "2026-01-03",
            "grain": "day",
            "time_dimension": "bucket_start",
        },
    )

    report = session.assess_quality(frame)
    duplicate = report.to_pandas().set_index("check_kind").loc["duplicate_keys"]
    assert duplicate["severity"] == "ok"


def test_panel_duplicate_keys_catches_real_duplicates(tmp_path):
    """Real duplicate rows in a panel frame must still be caught."""
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"bucket_start": "2026-01-01", "region": "north", "value": 10.0},
        {"bucket_start": "2026-01-01", "region": "north", "value": 99.0},
    ]
    frame = _metric(
        session,
        rows,
        semantic_kind="panel",
        axes={
            "time": {"role": "time", "column": "bucket_start", "grain": "day"},
            "region": {"role": "dimension", "column": "region"},
        },
        measure={"field": "value"},
        window=None,
    )

    report = session.assess_quality(frame)
    duplicate = report.to_pandas().set_index("check_kind").loc["duplicate_keys"]
    assert duplicate["severity"] == "blocking"


def test_assess_quality_returns_report_without_copying_report_into_source_artifact() -> None:
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)

    report = session.assess_quality(frame)

    assert report.kind == "quality_report"
    assert frame.quality_summary is not None
    assert not hasattr(frame.meta, "quality")
    assert not hasattr(frame.meta, "recommended_followups")
    assert report.ref != frame.ref
    assert report.summary().overall_status == "ok"


def test_panel_time_coverage_with_timezone(tmp_path):
    """Weekly grain with a non-UTC timezone must not yield 0% coverage.
    bucket_start values are session-local (e.g., 2026-05-18 for Shanghai's
    Monday), and the check must compare against local-calendar dates."""
    session = session_attach.get_or_create(name="demo")
    # Simulate weekly buckets in UTC+8: Monday midnights in session-local time.
    rows = [
        {"bucket_start": "2026-05-18T00:00:00", "region": "US", "value": 1.0},
        {"bucket_start": "2026-05-25T00:00:00", "region": "US", "value": 2.0},
        {"bucket_start": "2026-05-18T00:00:00", "region": "CA", "value": 3.0},
        {"bucket_start": "2026-05-25T00:00:00", "region": "CA", "value": 4.0},
    ]
    frame = _metric(
        session,
        rows,
        semantic_kind="panel",
        axes={
            "time": {"role": "time", "column": "bucket_start", "grain": "week"},
            "region": {"role": "dimension", "column": "region"},
        },
        measure={"field": "value"},
        window={
            "start": "2026-05-18",
            "end": "2026-06-01",
            "grain": "week",
            "time_dimension": "bucket_start",
        },
    )
    # Force the report timezone to Asia/Shanghai (UTC+8)
    from zoneinfo import ZoneInfo

    session._tz = ZoneInfo("Asia/Shanghai")

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])
    # With timezone alignment, coverage should be near 1.0, not 0.0
    assert details["coverage_ratio"] > 0.5
