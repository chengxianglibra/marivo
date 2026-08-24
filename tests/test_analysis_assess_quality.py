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
    resolved_axes = axes or {"time": {"field": "time", "grain": "day"}}
    resolved_measure = measure or {"field": "value", "aggregation": "sum"}
    df = pd.DataFrame(rows)
    if df.empty and not len(df.columns):
        columns = []
        if isinstance(resolved_measure.get("field"), str):
            columns.append(resolved_measure["field"])
        columns.extend(resolved_measure.get("fields", []))
        columns.extend(
            axis.get("field") or axis.get("column")
            for axis in resolved_axes.values()
            if axis.get("field") or axis.get("column")
        )
        df = pd.DataFrame(columns=list(dict.fromkeys(columns)))
    return make_metric_frame(
        df,
        metric_id="sales.revenue",
        axes=resolved_axes,
        measure=resolved_measure,
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
    assert set(df["check_kind"]) == {
        "row_count",
        "metric_row_contract",
        "null_ratio",
        "time_coverage",
        "value_density",
        "duplicate_keys",
    }
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


def test_metric_time_series_gaps_are_warning_only(tmp_path):
    session = session_attach.get_or_create(name="demo")
    full_range = pd.date_range("2026-01-01", periods=10, freq="D")
    rows = [
        {"time": timestamp, "value": 1.0}
        for timestamp in full_range
        if timestamp != pd.Timestamp("2026-01-05")
    ]
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

    sparse = _metric(
        session,
        [rows[0], rows[-1]],
        window={
            "start": "2026-01-01",
            "end": "2026-01-11",
            "grain": "day",
            "time_dimension": "time",
        },
    )
    sparse_report = session.assess_quality(sparse)
    assert sparse_report.meta.overall_status == "warning"
    assert sparse_report.meta.blocking_issue_count == 0


def test_metric_time_coverage_beyond_extent_is_info(tmp_path):
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": timestamp, "value": 1.0}
        for timestamp in pd.date_range("2025-01-01", periods=18, freq="MS")
    ]
    frame = _metric(
        session,
        rows,
        axes={"time": {"field": "time", "grain": "month"}},
        window={
            "start": "2025-01-01",
            "end": "2026-09-01",
            "grain": "month",
            "time_dimension": "time",
        },
    )

    report = session.assess_quality(frame)
    coverage = report.to_pandas().set_index("check_kind").loc["time_coverage"]
    details = json.loads(coverage["details_json"])

    assert coverage["severity"] == "info"
    assert details["requested_expected_buckets"] == 20
    assert details["expected_buckets"] == 18
    assert details["observed_buckets"] == 18
    assert details["beyond_extent_buckets"] == 2
    assert details["window_beyond_extent"] is True
    assert details["data_extent_end"] == "2026-06-01"
    assert details["coverage_ratio"] == 1.0
    assert report.meta.overall_status == "ok"
    assert report.meta.issues == ()


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
    assert coverage["severity"] == "warning"
    assert report.meta.overall_status == "warning"
    assert report.meta.blocking_issue_count == 0
    assert frame.meta.issues == ()
    assert loaded_source.meta.issues == ()
    assert report_issue.check_id == "time_coverage"
    assert report_issue.observed_value == 0.5
    assert report_issue.severity == "warning"
    assert report_issue.expectation == "coverage_ratio == 1.0 within data extent"


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
    assert "duplicate_keys_detected" in {issue.kind for issue in report.meta.issues}
    assert json.loads(duplicate["details_json"])["duplicate_count"] == 2


def test_metric_missing_authoritative_measure_column_is_blocking(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"other": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )

    report = session.assess_quality(frame)
    contract = report.to_pandas().set_index("check_kind").loc["metric_row_contract"]

    assert contract["severity"] == "blocking"
    assert report.meta.overall_status == "blocking"
    assert report.meta.blocking_issue_count == 1
    assert "metric_row_contract_invalid" in {issue.kind for issue in report.meta.issues}


def test_metric_missing_authoritative_time_column_blocks_without_crashing(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={"time": {"field": "time", "grain": "day"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={
            "start": "2026-01-01",
            "end": "2026-01-02",
            "grain": "day",
            "time_dimension": "time",
        },
        session=session,
    )

    report = session.assess_quality(frame)
    checks = report.to_pandas().set_index("check_kind")

    assert checks.loc["metric_row_contract", "severity"] == "blocking"
    assert checks.loc["duplicate_keys", "severity"] == "ok"
    assert report.meta.overall_status == "blocking"


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
    assert ids == {
        "null_ratio:value",
        "null_ratio:value2",
        "value_density:value",
        "value_density:value2",
        "row_count",
        "metric_row_contract",
        "time_coverage",
        "duplicate_keys",
    }

    empty = _metric(session, [], semantic_kind="scalar", axes={})
    empty_report = session.assess_quality(empty)
    assert empty_report.meta.overall_status == "warning"
    assert empty_report.meta.blocking_issue_count == 0
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
    assert ids == {
        "null_ratio:value",
        "value_density:value",
        "row_count",
        "metric_row_contract",
        "time_coverage",
        "duplicate_keys",
    }


def test_multi_metric_observe_produces_joint_metric_attributed_quality_report(tmp_path):
    """Joint quality runs frame checks once and binds measure checks to exact metrics."""
    import ibis

    import marivo.semantic as ms
    from marivo.analysis.evidence.types import QualityCheckResult
    from tests.shared_fixtures import bootstrap_multi_metric_sales_project

    bootstrap_multi_metric_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    months = pd.date_range("2026-01-01", periods=13, freq="MS")
    con.create_table(
        "orders",
        pd.DataFrame(
            {
                "order_id": range(1, 14),
                "created_at": months,
                "amount": [None] * 8 + [10.0] * 5,
                "region": ["north"] * 13,
                "user_id": [100] * 13,
            }
        ),
    )
    con.create_table(
        "users",
        pd.DataFrame({"user_id": [100], "signed_up_at": [pd.Timestamp("2026-01-01")]}),
    )
    session = session_attach.get_or_create(
        name="multi_quality",
        backends={"warehouse": lambda: con},
    )
    revenue = session.catalog.require(ms.ref.metric("sales.revenue")).ref
    order_count = session.catalog.require(ms.ref.metric("sales.order_count")).ref
    frame = session.observe(
        metrics=[revenue, order_count],
        time_scope=mv.time_scope(start="2026-01-01", end="2027-02-01"),
        grain=mv.grain("month"),
    )

    report = session.assess_quality(frame)
    rows = report.to_pandas()

    assert rows["check_id"].tolist() == [
        "row_count",
        "metric_row_contract",
        "null_ratio:revenue",
        "null_ratio:order_count",
        "time_coverage",
        "value_density:revenue",
        "value_density:order_count",
        "duplicate_keys",
    ]
    assert rows["metric_id"].tolist() == [
        None,
        None,
        "sales.revenue",
        "sales.order_count",
        None,
        "sales.revenue",
        "sales.order_count",
        None,
    ]
    assert report.overall_status == "warning"
    assert report.blocking_issue_count == 0
    assert report.meta.target_metric_id is None

    rendered = report.render()
    assert "checks=8" in rendered
    assert "blocking=0" in rendered
    assert "attention:" in rendered
    assert "null_ratio:revenue" in rendered
    assert "sales.revenue" in rendered
    assert "row_count" not in rendered
    assert "value_density:revenue" not in rendered
    full_rendered = report.render(max_output_bytes=None)
    assert "row_count" in full_rendered
    assert "null_ratio:order_count" in full_rendered

    issue = next(item for item in report.meta.issues if item.kind == "null_rate_high")
    assert issue.evaluated_scope.metric_ids == ("sales.revenue",)
    assert issue.repair is not None
    assert "sales.revenue" in issue.repair.action
    assert "metric=sales.revenue" in report.contract().render()

    assert report.evidence_digest is not None
    assert report.evidence_digest.quality is not None
    assert report.evidence_digest.quality.evaluated_check_count == 8
    revenue_null = next(
        item
        for item in report.evidence_digest.items
        if isinstance(item, QualityCheckResult) and item.check_id == "null_ratio:revenue"
    )
    assert revenue_null.subject.metric == "sales.revenue"
    assert revenue_null.scope.metric_ids == ("sales.revenue",)
    frame_level = next(
        item
        for item in report.evidence_digest.items
        if isinstance(item, QualityCheckResult) and item.check_id == "row_count"
    )
    assert frame_level.subject.metric is None
    assert frame_level.scope.metric_ids == ("sales.revenue", "sales.order_count")
    digest_rendered = report.evidence_digest.render(max_output_bytes=None)
    assert "subject=sales.revenue quality_check id=null_ratio:revenue" in digest_rendered
    assert "subject=subject quality_check id=row_count" in digest_rendered

    loaded = load_frame(report.ref, session=session)
    assert loaded.to_pandas()["metric_id"].tolist() == rows["metric_id"].tolist()
    assert loaded.evidence_digest == report.evidence_digest

    region = session.catalog.require(ms.ref.dimension("sales.orders.region")).ref
    shape_cases = (
        ("scalar", {}),
        ("segmented", {"dimensions": [region]}),
        (
            "panel",
            {
                "time_scope": mv.time_scope(start="2026-01-01", end="2027-02-01"),
                "grain": mv.grain("month"),
                "dimensions": [region],
            },
        ),
    )
    for expected_shape, observe_kwargs in shape_cases:
        shaped = session.observe(metrics=[revenue, order_count], **observe_kwargs)
        shaped_report = session.assess_quality(shaped)
        shaped_rows = shaped_report.to_pandas()
        assert shaped.semantic_shape == expected_shape
        assert (
            shaped_rows.loc[
                shaped_rows["check_kind"].isin({"row_count", "time_coverage", "duplicate_keys"}),
                "metric_id",
            ]
            .isna()
            .all()
        )
        assert set(
            shaped_rows.loc[
                shaped_rows["check_kind"].isin({"null_ratio", "value_density"}), "metric_id"
            ]
        ) == {"sales.revenue", "sales.order_count"}


def test_metric_time_series_near_constant_empty_warning(tmp_path):
    """A long span with nearly all-zero values must flag value_density_low.

    Issue #104: a managed funnel metric that is 0 in 12 of 13 months (and 6 in
    one) is indistinguishable from a healthy metric by the current quality
    surface, so an authoring/join defect (or a data-generation bug) sails
    through ``analysis_ready`` with no diagnostic.  The value_density check
    must surface a warning + typed issue over a sufficiently long span.
    """
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": pd.Timestamp("2026-01-01") + pd.DateOffset(months=i), "value": 0.0}
        for i in range(12)
    ]
    rows.append({"time": pd.Timestamp("2027-01-01"), "value": 6.0})
    frame = _metric(
        session,
        rows,
        axes={"time": {"field": "time", "grain": "month"}},
        window={
            "start": "2026-01-01",
            "end": "2027-02-01",
            "grain": "month",
            "time_dimension": "time",
        },
    )

    report = session.assess_quality(frame)
    density = report.to_pandas().set_index("check_kind").loc["value_density"]
    details = json.loads(density["details_json"])

    assert density["severity"] == "warning"
    assert details["nonzero_count"] == 1
    assert details["cell_count"] == 13
    assert details["time_bucket_count"] == 13
    assert details["value_density"] == pytest.approx(1 / 13)
    assert details["empty_ratio"] == pytest.approx(12 / 13)
    issues = [issue for issue in report.meta.issues if issue.kind == "value_density_low"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].observed_value == pytest.approx(1 / 13)
    assert issues[0].expectation == "value_density >= 0.1"
    assert issues[0].repair is not None
    assert report.meta.overall_status == "warning"


def test_metric_time_series_nonzero_values_have_full_value_density(tmp_path):
    """A span whose values are all non-zero must not flag value_density."""
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": pd.Timestamp("2026-01-01") + pd.DateOffset(months=i), "value": 1.0}
        for i in range(13)
    ]
    frame = _metric(
        session,
        rows,
        axes={"time": {"field": "time", "grain": "month"}},
        window={
            "start": "2026-01-01",
            "end": "2027-02-01",
            "grain": "month",
            "time_dimension": "time",
        },
    )

    report = session.assess_quality(frame)
    density = report.to_pandas().set_index("check_kind").loc["value_density"]
    details = json.loads(density["details_json"])

    assert density["severity"] == "ok"
    assert details["value_density"] == pytest.approx(1.0)
    assert report.meta.overall_status == "ok"


def test_metric_panel_span_guard_counts_time_buckets_not_cells(tmp_path):
    """A short-but-wide panel must not read as a long span.

    Issue #104 P3: the value_density span guard counted rows (time × segment
    cells) rather than distinct time buckets, so a 4-month × 4-segment panel
    (16 cells, all empty) would falsely warn.  The guard must key off the time
    axis's distinct bucket count, so a 4-bucket span stays green even though
    it has 16 cells.
    """
    session = session_attach.get_or_create(name="demo")
    regions = ["north", "south", "east", "west"]
    months = [pd.Timestamp("2026-01-01") + pd.DateOffset(months=i) for i in range(4)]
    rows = [{"time": t, "region": r, "value": 0.0} for t in months for r in regions]
    frame = _metric(
        session,
        rows,
        semantic_kind="panel",
        axes={"time": {"field": "time", "grain": "month"}, "region": {"field": "region"}},
        window={
            "start": "2026-01-01",
            "end": "2026-05-01",
            "grain": "month",
            "time_dimension": "time",
        },
    )

    report = session.assess_quality(frame)
    density = report.to_pandas().set_index("check_kind").loc["value_density"]
    details = json.loads(density["details_json"])

    assert details["cell_count"] == 16
    assert details["time_bucket_count"] == 4
    assert density["severity"] == "ok"


def test_null_rate_high_warning_issue_carries_repair(tmp_path):
    """A high null ratio remains actionable without blocking analysis."""
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
    assert report.meta.overall_status == "warning"
    assert report.meta.blocking_issue_count == 0
    issues = [issue for issue in report.meta.issues if issue.kind == "null_rate_high"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
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
    issue = next(issue for issue in report.meta.issues if issue.kind == "sample_size_low")
    assert issue.observed_value == 1
    assert issue.expectation == "row_count >= 5"
    assert report.evidence_digest is not None
    finding = next(
        item
        for item in report.evidence_digest.items
        if getattr(item, "check_id", None) == "row_count"
    )
    assert finding.expectation_parameters == {"threshold": 5}
    assert finding.expectation_condition_passed is False


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
    assert report.to_pandas()["metric_id"].isna().all()
    assert set(report.to_pandas()["check_kind"]) == {
        "row_count",
        "delta_row_contract",
        "delta_math",
    }
    assert recovered.meta.report_shape == "delta"

    corrupted = DeltaFrame(
        _df=delta._dataframe_copy().assign(delta=99.0),
        meta=delta.meta.model_copy(update={"ref": "frame_delta_corrupted"}),
    )
    corrupted_report = session.assess_quality(corrupted)
    math_check = corrupted_report.to_pandas().set_index("check_kind").loc["delta_math"]
    assert math_check["severity"] == "blocking"
    assert corrupted_report.meta.overall_status == "blocking"
    assert "delta_math_invalid" in {issue.kind for issue in corrupted_report.meta.issues}

    unsigned_rows = pd.DataFrame(
        {
            "current": pd.Series([0], dtype="UInt64"),
            "baseline": pd.Series([4], dtype="UInt64"),
        }
    )
    unsigned_rows["delta"] = unsigned_rows["current"] - unsigned_rows["baseline"]
    unsigned_rows["pct_change"] = unsigned_rows["delta"].astype("float64") / 4.0
    unsigned_rows["pct_change_status"] = "computed"
    unsigned_corruption = DeltaFrame(
        _df=unsigned_rows,
        meta=delta.meta.model_copy(update={"ref": "frame_delta_unsigned_corrupted"}),
    )

    unsigned_report = session.assess_quality(unsigned_corruption)
    unsigned_math_check = unsigned_report.to_pandas().set_index("check_kind").loc["delta_math"]
    assert unsigned_math_check["severity"] == "blocking"
    assert unsigned_report.meta.overall_status == "blocking"


def test_quality_report_render_surfaces_check_results(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)
    report = session.assess_quality(frame)
    rendered = report.render()
    assert f"status={report.meta.overall_status}" in rendered
    assert f"blocking={report.meta.blocking_issue_count}" in rendered
    assert f"warning={report.meta.warning_count}" in rendered
    assert "info=0" in rendered
    assert f"checks={report.meta.row_count}" in rendered
    assert f"ok={report.meta.row_count}" in rendered
    assert "attention: none" in rendered
    for check_id in report._df["check_id"].head(5):
        assert str(check_id) not in rendered
        assert str(check_id) in report.render(max_output_bytes=None)
    assert "summary()" not in rendered


def test_summary_reflects_empty_result_warning(tmp_path):
    session = session_attach.get_or_create(name="demo")
    empty = _metric(session, [], semantic_kind="scalar", axes={})
    report = session.assess_quality(empty)

    assert report.meta.overall_status == "warning"
    assert report.meta.blocking_issue_count == 0
    assert (report.to_pandas()["severity"] == "warning").any()


def test_repr_contains_identity_and_show_hint(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = seeded_time_series_metric_frame(session=session, n_buckets=5)
    report = session.assess_quality(frame)

    r = repr(report)
    assert "QualityReport" in r
    assert f"ref={report.ref}" in r
    assert "status=ok" in r
    assert "blocking=0" in r
    assert "rows=6" in r
    assert "call .show() to inspect" in r


def test_summary_reflects_warning(tmp_path):
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": timestamp, "value": 1.0}
        for timestamp in pd.date_range("2026-01-01", periods=10, freq="D")
        if timestamp != pd.Timestamp("2026-01-05")
    ]
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
    assert report.meta.overall_status == "warning"
    assert report.meta.blocking_issue_count == 0


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
    assert report.meta.overall_status == "warning"
    assert report.meta.blocking_issue_count == 0


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
