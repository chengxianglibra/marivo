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


@pytest.mark.parametrize(
    ("grain", "start", "end", "freq", "expected_buckets"),
    [
        ("hour", "2026-06-30T00:00:00", "2026-06-30T03:00:00", "h", 3),
        ("day", "2026-06-30", "2026-07-03", "D", 3),
        ("week", "2026-06-29", "2026-07-20", "W-MON", 3),
        ("month", "2026-04-01", "2026-07-01", "MS", 3),
        ("quarter", "2026-01-01", "2026-10-01", "QS", 3),
    ],
)
def test_metric_time_coverage_preserves_supported_grain_buckets(
    tmp_path, grain, start, end, freq, expected_buckets
):
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": timestamp, "value": 1.0}
        for timestamp in pd.date_range(start, end, freq=freq, inclusive="left")
    ]
    frame = _metric(
        session,
        rows,
        axes={"time": {"field": "time", "grain": grain}},
        window={"start": start, "end": end, "grain": grain, "time_dimension": "time"},
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])

    assert details["expected_buckets"] == expected_buckets
    assert details["observed_buckets"] == expected_buckets
    assert details["coverage_ratio"] == 1.0


def test_hourly_time_coverage_blocker_persists_to_source_frame(tmp_path):
    session = session_attach.get_or_create(name="demo")
    start = "2026-06-30T00:00:00"
    end = "2026-07-01T00:00:00"
    frame = _metric(
        session,
        [
            {"time": timestamp, "value": 1.0}
            for timestamp in pd.date_range(start, periods=12, freq="h")
        ],
        axes={"time": {"field": "time", "grain": "hour"}},
        window={"start": start, "end": end, "grain": "hour", "time_dimension": "time"},
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])
    loaded_source = load_frame(frame.ref, session=session)
    report_issue = next(
        issue for issue in report.meta.blocking_issues if issue.kind == "time_coverage"
    )
    source_issue = next(
        issue for issue in frame.meta.blocking_issues if issue.kind == "time_coverage"
    )
    loaded_issue = next(
        issue for issue in loaded_source.meta.blocking_issues if issue.kind == "time_coverage"
    )

    assert details["expected_buckets"] == 24
    assert details["observed_buckets"] == 12
    assert details["coverage_ratio"] == 0.5
    assert details["missing_examples"] == [
        "2026-06-30T12:00:00",
        "2026-06-30T13:00:00",
        "2026-06-30T14:00:00",
        "2026-06-30T15:00:00",
        "2026-06-30T16:00:00",
    ]
    assert report.meta.overall_status == "blocking"
    assert frame.quality_summary is not None
    assert frame.quality_summary.coverage == pytest.approx(0.5)
    assert report_issue.payload == source_issue.payload == loaded_issue.payload
    assert report_issue.payload == {
        "check_id": "time_coverage",
        "check_kind": "time_coverage",
        "coverage_ratio": 0.5,
        "expected_buckets": 24,
        "missing_examples": details["missing_examples"],
        "observed_buckets": 12,
        "origin": "assess_quality",
    }


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


def test_scalar_metric_single_row_does_not_emit_row_count_warning(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame([{"value": 0.73}]),
        metric_id="infra.utilization",
        axes={},
        measure={"field": "value", "aggregation": "mean"},
        semantic_kind="scalar",
        semantic_model="infra",
        window=None,
        session=session,
    )

    report = session.assess_quality(frame)
    row_count = report.to_pandas().set_index("check_kind").loc["row_count"]

    assert report.meta.overall_status == "ok"
    assert report.meta.warning_count == 0
    assert row_count["severity"] == "ok"


def test_segmented_metric_single_row_still_emits_row_count_warning(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        [{"segment": "US", "value": 1.0}],
        semantic_kind="segmented",
        axes={"dimensions": [{"field": "segment"}]},
        window=None,
    )

    report = session.assess_quality(frame)
    row_count = report.to_pandas().set_index("check_kind").loc["row_count"]

    assert report.meta.overall_status == "warning"
    assert row_count["severity"] == "warning"


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


def test_quality_report_render_surfaces_check_results(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)
    report = session.assess_quality(frame)
    rendered = report.render()
    assert f"status={report.meta.overall_status}" in rendered
    assert f"blocking={report.meta.blocking_issue_count}" in rendered
    assert f"warning={report.meta.warning_count}" in rendered
    for check_id in report._df["check_id"].head(5):
        assert str(check_id) in rendered
    assert "summary()" not in rendered


def test_summary_reflects_blocking(tmp_path):
    session = session_attach.get_or_create(name="demo")
    empty = _metric(session, [], semantic_kind="scalar", axes={})
    report = session.assess_quality(empty)

    assert report.meta.overall_status == "blocking"
    assert report.meta.blocking_issue_count >= 1
    assert (report.to_pandas()["severity"] == "blocking").any()


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

    assert report.meta.overall_status == "warning"
    assert report.meta.warning_count >= 1
    assert report.meta.blocking_issue_count == 0


def test_summary_scalar_without_metric_id(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, [], semantic_kind="scalar", axes={}, window=None)
    frame.meta.metric_id = None
    report = session.assess_quality(frame)

    assert report.meta.target_metric_id is None


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
    assert report.meta.overall_status == "ok"


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
