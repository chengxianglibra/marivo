from __future__ import annotations

import json

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session._load import load_frame
from tests.shared_fixtures import (
    make_metric_frame,
    make_test_delta_contract,
    seeded_time_series_metric_frame,
)


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
    assert report.overall_status == report.meta.overall_status
    assert report.blocking_issue_count == report.meta.blocking_issue_count
    assert report.warning_count == report.meta.warning_count
    assert report.state.materialization == "materialized"

    loaded = load_frame(report.ref, session=session)
    assert loaded.overall_status == report.overall_status
    assert loaded.blocking_issue_count == report.blocking_issue_count
    assert loaded.warning_count == report.warning_count

    with pytest.raises(AttributeError):
        report.overall_status = "warning"


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


def test_hourly_time_coverage_issue_belongs_only_to_quality_report(tmp_path):
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
        issue for issue in report.meta.issues if issue.kind == "time_coverage_incomplete"
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
    assert frame.meta.issues == ()
    assert loaded_source.meta.issues == ()
    assert report_issue.check_id == "time_coverage"
    assert report_issue.observed_value == 0.5
    assert report_issue.expectation == "coverage_ratio >= 0.8"


def test_metric_segmented_duplicate_keys_blocking(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        [{"segment": "US", "value": 1.0}, {"segment": "US", "value": 2.0}],
        semantic_kind="segmented",
        axes={"segment": {"role": "dimension", "field": "segment"}},
        window=None,
    )

    report = session.assess_quality(frame)
    duplicate = report.to_pandas().set_index("check_kind").loc["duplicate_keys"]

    assert duplicate["severity"] == "blocking"
    assert report.meta.issues[0].kind == "duplicate_keys_detected"
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
    # Closed-set: any new check row on this shape must be disclosed explicitly.
    assert ids == {"null_ratio:value", "null_ratio:value2", "row_count", "time_coverage"}

    empty = _metric(session, [], semantic_kind="scalar", axes={})
    empty_report = session.assess_quality(empty)
    assert empty_report.meta.overall_status == "blocking"
    assert empty_report.meta.issues[0].kind == "sample_size_low"


def test_observe_frame_runs_null_ratio_checks(tmp_path):
    """A real observe() frame must execute null_ratio on its value column.

    Issue #54 P2-1: converging ``_measure_columns`` onto typed
    ``measure_bindings`` activates the null_ratio check for production observe
    frames (the legacy ``measure`` dict carries only ``{'name': ...}``). Pin the
    closed check_id set so the activation stays explicit.
    """
    import ibis

    from marivo.analysis.intents.observe import observe
    from marivo.refs import ref
    from tests.shared_fixtures import (
        bootstrap_multi_metric_sales_project,
        seed_multi_metric_tables,
    )

    bootstrap_multi_metric_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    seed_multi_metric_tables(con)
    session = session_attach.get_or_create(name="observe_q", backends={"warehouse": lambda: con})

    frame = observe(
        session.catalog.require(ref.metric("sales.revenue")).ref,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-04"),
        grain=mv.grain("day"),
        session=session,
    )
    report = session.assess_quality(frame)
    ids = set(report.to_pandas()["check_id"])
    assert ids == {"null_ratio:value", "row_count", "time_coverage"}


def test_null_rate_high_blocking_issue_carries_repair(tmp_path):
    """A null_ratio crossing 0.5 must produce a blocking issue with a repair."""
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        [
            {"time": pd.Timestamp("2026-01-01"), "value": None},
            {"time": pd.Timestamp("2026-01-02"), "value": None},
            {"time": pd.Timestamp("2026-01-03"), "value": 1.0},
        ],
    )
    report = session.assess_quality(frame)
    assert report.meta.overall_status == "blocking"
    issues = [issue for issue in report.meta.issues if issue.kind == "null_rate_high"]
    assert len(issues) == 1
    assert issues[0].severity == "blocking"
    assert issues[0].check_id == "null_ratio:value"
    assert issues[0].repair is not None
    assert issues[0].repair.kind == "retry"


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
        axes={"segment": {"role": "dimension", "field": "segment"}},
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


def test_metric_delta_quality_validates_row_contract(tmp_path):
    session = session_attach.get_or_create(name="demo")
    delta = DeltaFrame(
        _df=pd.DataFrame(
            {
                "current": [2.0],
                "baseline": [1.0],
                "delta": [1.0],
                "pct_change": [1.0],
                "pct_change_status": ["computed"],
            }
        ),
        meta=DeltaFrameMeta(
            **make_test_delta_contract("sales.revenue"),
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
    report = session.assess_quality(delta)
    recovered = session.get_frame(report.ref)

    assert report.meta.report_shape == "delta"
    assert set(report.to_pandas()["check_kind"]) == {"row_count", "delta_row_contract"}
    assert recovered.meta.report_shape == "delta"


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
    assert frame.quality_summary is None
    assert frame.meta.issues == ()
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


def _aware_window_metric(session, rows, *, start, end, grain="hour"):
    """Build a time_series metric whose scope window is tz-aware while the frame
    time column holds naive local wall-clock bucket timestamps (the q08 shape)."""
    return _metric(
        session,
        rows,
        axes={"time": {"field": "time", "grain": grain}},
        window={"start": start, "end": end, "grain": grain, "time_dimension": "time"},
    )


def test_hourly_coverage_aware_scope_naive_frame_matches_bucket_counts(tmp_path):
    """Issue #70: an aware scope window (+08:00) with a naive frame time column
    must report observed/expected as the real bucket counts (12/24 = 0.5), not a
    silent 0.0 — and the report fields must agree."""
    session = session_attach.get_or_create(name="demo")
    frame = _aware_window_metric(
        session,
        [
            {"time": pd.Timestamp("2026-06-30T00:00:00") + pd.Timedelta(hours=h), "value": 1.0}
            for h in range(12)
        ],
        start="2026-06-30T00:00:00+08:00",
        end="2026-07-01T00:00:00+08:00",
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])
    report_issue = next(
        issue for issue in report.meta.issues if issue.kind == "time_coverage_incomplete"
    )

    assert details["expected_buckets"] == 24
    assert details["observed_buckets"] == 12
    assert details["coverage_ratio"] == pytest.approx(0.5)
    assert report_issue.observed_value == pytest.approx(0.5)
    assert report.meta.overall_status == "blocking"


def test_hourly_coverage_aware_scope_13_of_24_reports_approx_ratio(tmp_path):
    """Issue #70: 13 observed hourly buckets against an aware 24-bucket scope
    reports ~0.5417, consistent across details and issue observed_value."""
    session = session_attach.get_or_create(name="demo")
    frame = _aware_window_metric(
        session,
        [
            {"time": pd.Timestamp("2026-06-30T00:00:00") + pd.Timedelta(hours=h), "value": 1.0}
            for h in range(13)
        ],
        start="2026-06-30T00:00:00+08:00",
        end="2026-07-01T00:00:00+08:00",
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])
    report_issue = next(
        issue for issue in report.meta.issues if issue.kind == "time_coverage_incomplete"
    )

    assert details["expected_buckets"] == 24
    assert details["observed_buckets"] == 13
    assert details["coverage_ratio"] == pytest.approx(13 / 24)
    assert report_issue.observed_value == pytest.approx(13 / 24)
    assert report.meta.overall_status == "blocking"


def test_hourly_coverage_full_24_24_aware_scope_reports_one(tmp_path):
    """Issue #70: a complete 24/24 frame under an aware scope reports 1.0 and is
    not falsely zeroed by the aware-vs-naive mixing."""
    session = session_attach.get_or_create(name="demo")
    frame = _aware_window_metric(
        session,
        [
            {"time": pd.Timestamp("2026-06-30T00:00:00") + pd.Timedelta(hours=h), "value": 1.0}
            for h in range(24)
        ],
        start="2026-06-30T00:00:00+08:00",
        end="2026-07-01T00:00:00+08:00",
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])

    assert details["expected_buckets"] == 24
    assert details["observed_buckets"] == 24
    assert details["coverage_ratio"] == pytest.approx(1.0)
    assert report.meta.overall_status == "ok"


def test_hourly_coverage_both_aware_same_timezone(tmp_path):
    """Issue #70: a fully aware frame (same tz as the scope window) still reports
    the correct ratio — no regression from the aware-vs-naive fix."""
    session = session_attach.get_or_create(name="demo")
    rows = [
        {
            "time": pd.Timestamp("2026-06-30T00:00:00", tz="Asia/Shanghai") + pd.Timedelta(hours=h),
            "value": 1.0,
        }
        for h in range(12)
    ]
    frame = _aware_window_metric(
        session,
        rows,
        start="2026-06-30T00:00:00+08:00",
        end="2026-07-01T00:00:00+08:00",
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])

    assert details["expected_buckets"] == 24
    assert details["observed_buckets"] == 12
    assert details["coverage_ratio"] == pytest.approx(0.5)


def test_hourly_coverage_cross_timezone_canonicalization(tmp_path):
    """Issue #70: an aware frame in a different tz than the aware scope window is
    canonicalized to a common timezone (absolute instants), not falsely zeroed."""
    session = session_attach.get_or_create(name="demo")
    # Scope window in UTC+8; frame buckets in UTC (same absolute instants for
    # the first 12 hours: 2026-06-29 16:00Z .. 2026-06-30 03:00Z).
    rows = [
        {
            "time": pd.Timestamp("2026-06-29T16:00:00", tz="UTC") + pd.Timedelta(hours=h),
            "value": 1.0,
        }
        for h in range(12)
    ]
    frame = _aware_window_metric(
        session,
        rows,
        start="2026-06-30T00:00:00+08:00",
        end="2026-07-01T00:00:00+08:00",
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])

    assert details["expected_buckets"] == 24
    assert details["observed_buckets"] == 12
    assert details["coverage_ratio"] == pytest.approx(0.5)
