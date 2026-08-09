"""session.compare against two MetricFrames."""

import importlib
import json
from datetime import UTC, date, datetime, timedelta

import ibis
import numpy as np
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.datasource as md
import marivo.semantic as ms
from marivo._temporal import (
    BuiltinPeriodBindingV1,
    FrameTemporalContractV1,
    SemanticPeriodBindingV1,
    TemporalSetSnapshotStore,
    TemporalSnapshotStore,
    TimeScopeContractV1,
    WorkScheduleSnapshotStore,
    certify_period_calendar,
    certify_temporal_set,
    certify_work_schedule,
)
from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyNotApplicableError,
    AttributeAdmissionBlockedError,
    ComponentFrameUnavailableError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents.compare import _align_component_role, compare
from marivo.analysis.intents.observe import observe
from marivo.analysis.policies import (
    day_of_week,
    occurrence_progress,
    period_correspondence,
    period_progress,
    window_bucket,
    working_day_progress,
)
from marivo.analysis.session._layout import read_frame_from_disk
from marivo.datasource.snapshot import DiscoverySnapshot, SnapshotCoverage
from marivo.refs import ref
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.metric_graph import ExactComparisonSemanticsV1
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.shared_fixtures import (
    fiscal_analysis_project_files,
    fiscal_calendar_evidence,
    make_metric_frame,
)

compare_intent = importlib.import_module("marivo.analysis.intents.compare")


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0),"
        "(2, DATE '2026-07-02', 20.0),"
        "(3, DATE '2026-04-01', 5.0),"
        "(4, DATE '2026-04-02', 15.0)"
    )


def _attach_temporal_contract(
    frame: MetricFrame,
    *,
    start: date,
    end: date,
    observation_period,
    scope: TimeScopeContractV1 | None = None,
) -> MetricFrame:
    frame.meta = frame.meta.model_copy(
        update={
            "temporal_contract": FrameTemporalContractV1(
                time_scope=scope or TimeScopeContractV1(kind="absolute", start=start, end=end),
                observation_period=observation_period,
                actual_start=start,
                actual_end=end,
                display_timezone="UTC",
            )
        }
    )
    return frame


def _slice3_snapshot():
    start = date(2026, 1, 1)
    end = start + timedelta(days=56)
    rows = []
    for offset in range((end - start).days):
        week_number = offset // 7 + 1
        rows.append(
            {
                "date": start + timedelta(days=offset),
                "fiscal_month": "M1" if week_number <= 4 else "M2",
                "fiscal_week": f"W{week_number}",
                "prior_key": f"W{week_number - 4}" if week_number > 4 else f"W{week_number + 4}",
            }
        )
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.retail"),
        boundary_timezone="UTC",
        coverage=(start, end),
        rows=rows,
        levels={"fiscal_month": "fiscal_month", "fiscal_week": "fiscal_week"},
        correspondences={"prior_year": ("fiscal_week", "prior_key")},
    )
    return snapshot


def test_temporal_component_attribution_projects_parent_pairs_without_rematching(
    tmp_path, monkeypatch
):
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {
            "role": "time",
            "column": "bucket",
            "grain": "day",
            "time_dimension": "bucket",
            "ref": "sales.orders.bucket",
        },
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        },
    }
    current = make_metric_frame(
        pd.DataFrame({"bucket": ["2026-07-01"], "region": ["north"], "numerator": [10.0]}),
        metric_id="sales.numerator",
        axes=axes,
        measure={"name": "numerator"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )
    baseline = make_metric_frame(
        pd.DataFrame({"bucket": ["2025-07-02"], "region": ["north"], "numerator": [8.0]}),
        metric_id="sales.numerator_baseline",
        axes=axes,
        measure={"name": "numerator"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )
    parent_pairs = pd.DataFrame(
        {
            "region": ["north"],
            "bucket_start_a": ["2026-07-01"],
            "bucket_start_b": ["2025-07-02"],
            "align_key": ['["north", [1, 0]]'],
            "align_quality": ["exact"],
            "presence_status": ["matched"],
        }
    )

    def fail_if_rematched(*_args, **_kwargs):
        raise AssertionError("component attribution must not rerun temporal alignment")

    monkeypatch.setattr(compare_intent, "align_temporal_policy", fail_if_rematched)
    aligned = _align_component_role(
        current,
        baseline,
        alignment=day_of_week(),
        parent_pairs=parent_pairs,
        session=session,
    )

    assert aligned.loc[0, "current"] == pytest.approx(10.0)
    assert aligned.loc[0, "baseline"] == pytest.approx(8.0)
    assert aligned.loc[0, "delta"] == pytest.approx(2.0)
    assert aligned.loc[0, "bucket_start_b"] == "2025-07-02"


def test_compare_returns_delta_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-30"),
        session=s,
    )
    d = compare(q3, q2, alignment=window_bucket(), session=s)
    assert isinstance(d, DeltaFrame)
    assert d.meta.alignment["kind"] == "window_bucket"
    assert d.meta.source_current_ref == q3.ref
    assert d.meta.source_baseline_ref == q2.ref
    assert d.meta.metric_identity == q3.meta.metric_identity
    assert d.meta.baseline_metric_identity == q2.meta.metric_identity
    assert d.meta.comparison_identity is not None
    assert d.meta.comparison_identity.current_artifact_id == q3.ref
    assert d.meta.comparison_identity.baseline_artifact_id == q2.ref
    assert d.meta.comparison_identity.schema == "delta-comparison/v2"
    assert isinstance(d.meta.comparison_identity.semantics, ExactComparisonSemanticsV1)
    df = d.to_pandas()
    assert set(df.columns) >= {"current", "baseline", "delta", "pct_change"}
    assert df.iloc[0]["current"] == pytest.approx(30.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(10.0)

    reversed_delta = compare(
        q2,
        q3,
        alignment=window_bucket(),
        session=s,
    )
    assert reversed_delta.ref != d.ref
    assert reversed_delta.meta.comparison_identity is not None
    assert reversed_delta.meta.comparison_identity.current_artifact_id == q2.ref
    assert reversed_delta.meta.comparison_identity.baseline_artifact_id == q3.ref
    assert reversed_delta.to_pandas().iloc[0]["delta"] == pytest.approx(-10.0)

    store = s._evidence_store()
    assert store is not None
    row = (
        store.read()
        .execute("SELECT subject_payload FROM artifacts WHERE artifact_id = ?", (d.ref,))
        .fetchone()
    )
    assert row is not None
    subject = json.loads(row["subject_payload"])["typed_metric_subject"]
    assert subject["kind"] == "delta_metric"
    assert subject["session_id"] == s.id
    assert subject["comparison"]["current_artifact_id"] == q3.ref
    assert subject["comparison"]["baseline_artifact_id"] == q2.ref


def test_compare_default_bucket_handles_scalar_window_outputs(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-30"),
        session=s,
    )
    d = compare(q3, q2, session=s)
    assert d.to_pandas().iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_day_of_week_executes_and_persists_pairing_evidence(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "sales.orders.created_at",
        }
    }
    current = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2026-05-01", periods=7, freq="D"),
                "value": range(1, 8),
            }
        ),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-05-01", "end": "2026-05-08"},
        session=session,
    )
    baseline = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2026-04-01", periods=7, freq="D"),
                "value": range(11, 18),
            }
        ),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-04-01", "end": "2026-04-08"},
        session=session,
    )
    _attach_temporal_contract(
        current,
        start=date(2026, 5, 1),
        end=date(2026, 5, 8),
        observation_period=BuiltinPeriodBindingV1(level_name="day", boundary_timezone="UTC"),
    )
    _attach_temporal_contract(
        baseline,
        start=date(2026, 4, 1),
        end=date(2026, 4, 8),
        observation_period=BuiltinPeriodBindingV1(level_name="day", boundary_timezone="UTC"),
    )

    delta = compare(current, baseline, alignment=day_of_week(), session=session)

    assert len(delta.to_pandas()) == 7
    assert delta.meta.temporal_contract is not None
    assert delta.meta.temporal_contract.alignment_evidence.paired_points == 7
    assert "paired=7" in delta.render(max_output_bytes=None)


def test_compare_period_progress_and_correspondence_use_certified_snapshot(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    snapshot = _slice3_snapshot()
    TemporalSnapshotStore(session.project_root).publish(
        snapshot,
        definition_digest="sha256:test-definition",
    )
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "fiscal_week",
            "time_dimension": "sales.orders.created_at",
        }
    }
    observation = SemanticPeriodBindingV1(
        calendar_ref="sales.retail",
        snapshot_digest=snapshot.snapshot_digest,
        level_name="fiscal_week",
    )

    def frame(data: pd.DataFrame, *, start: date, end: date, scope: TimeScopeContractV1):
        result = make_metric_frame(
            data,
            metric_id="sales.revenue",
            axes=axes,
            measure={"name": "value"},
            semantic_kind="time_series",
            semantic_model="sales",
            window={"start": start.isoformat(), "end": end.isoformat()},
            session=session,
        )
        return _attach_temporal_contract(
            result,
            start=start,
            end=end,
            observation_period=observation,
            scope=scope,
        )

    progress_current = frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]
                ),
                "period_key": ["W1", "W2", "W3", "W4"],
                "is_complete": [True] * 4,
                "value": [1.0, 2.0, 3.0, 4.0],
            }
        ),
        start=date(2026, 1, 1),
        end=date(2026, 1, 29),
        scope=TimeScopeContractV1(
            kind="calendar_period",
            start=date(2026, 1, 1),
            end=date(2026, 1, 29),
            calendar_ref="sales.retail",
            snapshot_digest=snapshot.snapshot_digest,
            boundary_timezone="UTC",
            level="fiscal_month",
            key="M1",
        ),
    )
    progress_baseline = frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-01-29", "2026-02-05", "2026-02-12", "2026-02-19"]
                ),
                "period_key": ["W5", "W6", "W7", "W8"],
                "is_complete": [True] * 4,
                "value": [11.0, 12.0, 13.0, 14.0],
            }
        ),
        start=date(2026, 1, 29),
        end=date(2026, 2, 26),
        scope=TimeScopeContractV1(
            kind="calendar_period",
            start=date(2026, 1, 29),
            end=date(2026, 2, 26),
            calendar_ref="sales.retail",
            snapshot_digest=snapshot.snapshot_digest,
            boundary_timezone="UTC",
            level="fiscal_month",
            key="M2",
        ),
    )

    progress_delta = compare(
        progress_current,
        progress_baseline,
        alignment=period_progress(),
        session=session,
    )
    correspondence_delta = compare(
        progress_baseline,
        progress_current,
        alignment=period_correspondence(correspondence="prior_year"),
        session=session,
    )

    assert len(progress_delta.to_pandas()) == 4
    assert len(correspondence_delta.to_pandas()) == 4
    assert progress_delta.meta.temporal_contract is not None
    assert correspondence_delta.meta.temporal_contract is not None
    assert progress_delta.meta.temporal_contract.alignment_evidence.paired_points == 4
    assert correspondence_delta.meta.temporal_contract.alignment_evidence.paired_points == 4
    for delta in (progress_delta, correspondence_delta):
        assert delta.evidence_status == "complete"
        assert delta.evidence_digest is not None
        findings = session.evidence.findings(artifact_ref=delta.ref).items
        assert len(findings) == 4
        assert len({finding.canonical_item_key for finding in findings}) == 4


def test_compare_period_progress_uses_frames_from_public_observe(
    semantic_project_factory, monkeypatch
):
    """The certified period binding must survive the public observe handoff."""
    project = semantic_project_factory(fiscal_analysis_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE calendar (calendar_date DATE, fiscal_week VARCHAR, fiscal_month VARCHAR)"
    )
    calendar_rows = []
    cursor = date(2026, 1, 1)
    while cursor < date(2026, 3, 1):
        month = "M1" if cursor.month == 1 else "M2"
        week = f"{month}-W{((cursor.day - 1) // 7) + 1}"
        calendar_rows.append((cursor.isoformat(), week, month))
        cursor += timedelta(days=1)
    backend.raw_sql(
        "INSERT INTO calendar VALUES "
        + ",".join(f"(DATE '{day}', '{week}', '{month}')" for day, week, month in calendar_rows)
    )
    backend.raw_sql("CREATE TABLE events (event_date DATE, amount DOUBLE, user_id INTEGER)")
    backend.raw_sql(
        "INSERT INTO events VALUES "
        "(DATE '2026-01-01', 10, 1), (DATE '2026-01-08', 20, 1), "
        "(DATE '2026-02-01', 30, 1), (DATE '2026-02-08', 40, 2)"
    )

    catalog = ms.SemanticCatalog(project)
    calendar_ref = ref.period_calendar("sales.fiscal")
    catalog.verify(calendar_ref)
    catalog.preview(calendar_ref, using=fiscal_calendar_evidence(project.workspace_dir))
    session = session_attach.get_or_create(
        name="fiscal-compare",
        backends={"warehouse": lambda: backend},
        report_timezone="UTC",
    )
    metric = session.catalog.require(ref.metric("sales.gmv")).ref
    calendar = session.catalog.period_calendars.get("sales.fiscal")
    semantic_week = calendar.grain("fiscal_week")
    current = session.observe(
        metric,
        time_scope=calendar.period("fiscal_month", "M2"),
        grain=semantic_week,
    )
    baseline = session.observe(
        metric,
        time_scope=calendar.period("fiscal_month", "M1"),
        grain=semantic_week,
    )

    delta = session.compare(current, baseline, alignment=period_progress())

    assert len(delta.to_pandas()) == 2
    assert delta.meta.temporal_contract is not None
    assert delta.meta.temporal_contract.alignment_evidence.paired_points == 2


def test_compare_occurrence_progress_anchors_exact_scopes_and_records_drops(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    snapshot = certify_temporal_set(
        temporal_set_ref=ref.temporal_set("sales.campaigns"),
        boundary_timezone="UTC",
        coverage=(date(2025, 1, 1), date(2027, 1, 1)),
        rows=[
            {"id": "spring", "start": date(2026, 3, 1), "end": date(2026, 3, 4)},
            {"id": "legacy", "start": date(2025, 4, 10), "end": date(2025, 4, 12)},
        ],
        occurrence_id="id",
        start="start",
        end="end",
    )
    TemporalSetSnapshotStore(session.project_root).publish(
        snapshot,
        definition_digest="sha256:temporal-set-definition",
    )
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "sales.orders.created_at",
        }
    }
    observation = BuiltinPeriodBindingV1(level_name="day", boundary_timezone="UTC")

    def frame(data: pd.DataFrame, *, start: date, end: date, key: str):
        result = make_metric_frame(
            data,
            metric_id="sales.revenue",
            axes=axes,
            measure={"name": "value"},
            semantic_kind="time_series",
            semantic_model="sales",
            window={"start": start.isoformat(), "end": end.isoformat()},
            session=session,
        )
        return _attach_temporal_contract(
            result,
            start=start,
            end=end,
            observation_period=observation,
            scope=TimeScopeContractV1(
                kind="temporal_occurrence",
                start=start,
                end=end,
                temporal_set_ref="sales.campaigns",
                snapshot_digest=snapshot.snapshot_digest,
                boundary_timezone="UTC",
                key=key,
            ),
        )

    current = frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-03-01", "2026-03-02", "2026-03-03"]),
                "value": [10.0, 20.0, 30.0],
            }
        ),
        start=date(2026, 3, 1),
        end=date(2026, 3, 4),
        key="spring",
    )
    baseline = frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2025-04-10", "2025-04-11"]),
                "value": [8.0, 18.0],
            }
        ),
        start=date(2025, 4, 10),
        end=date(2025, 4, 12),
        key="legacy",
    )

    delta = compare(
        current,
        baseline,
        alignment=occurrence_progress(anchor="start", unmatched="drop"),
        session=session,
    )

    output = delta.to_pandas()
    assert len(output) == 2
    assert list(output["bucket_start_a"]) == [
        pd.Timestamp("2026-03-01"),
        pd.Timestamp("2026-03-02"),
    ]
    evidence = delta.meta.temporal_contract.alignment_evidence
    assert evidence.paired_points == 2
    assert evidence.current_only_points == 1
    assert evidence.dropped_points == 1
    assert delta.meta.temporal_contract.resolved_target_period is None


def test_compare_occurrence_progress_uses_frames_from_public_observe(tmp_path):
    """Occurrence provenance must survive catalog scope selection and observe."""
    bootstrap_sales_project(tmp_path)
    backend = ibis.duckdb.connect(":memory:")
    session = session_attach.get_or_create(
        name="demo",
        backends={"warehouse": lambda: backend},
        report_timezone="UTC",
    )
    _seed(backend)
    snapshot = certify_temporal_set(
        temporal_set_ref=ref.temporal_set("sales.campaigns"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2027, 1, 1)),
        rows=[
            {"id": "spring", "start": date(2026, 7, 1), "end": date(2026, 7, 3)},
            {"id": "legacy", "start": date(2026, 4, 1), "end": date(2026, 4, 3)},
        ],
        occurrence_id="id",
        start="start",
        end="end",
    )
    TemporalSetSnapshotStore(session.project_root).publish(
        snapshot,
        definition_digest="sha256:temporal-set-definition",
    )

    metric = ref.metric("sales.revenue")
    current = session.observe(
        metric,
        time_scope=snapshot.occurrence_scope("spring"),
        grain=mv.grain("day"),
    )
    baseline = session.observe(
        metric,
        time_scope=snapshot.occurrence_scope("legacy"),
        grain=mv.grain("day"),
    )
    delta = session.compare(
        current,
        baseline,
        alignment=occurrence_progress(anchor="start", unmatched="drop"),
    )

    assert len(delta.to_pandas()) == 2
    assert delta.meta.temporal_contract is not None
    evidence = delta.meta.temporal_contract.alignment_evidence
    assert evidence.paired_points == 2
    assert delta.evidence_status == "complete"
    assert delta.evidence_digest is not None
    findings = session.evidence.findings(artifact_ref=delta.ref).items
    assert len(findings) == 2
    assert len({finding.canonical_item_key for finding in findings}) == 2
    assert all(
        finding.derivation.source_fields[:2] == ("bucket_start_a", "bucket_start_b")
        for finding in findings
    )


def test_compare_working_day_progress_uses_exact_schedule_and_excludes_nonworking_rows(
    tmp_path,
):
    bootstrap_sales_project(tmp_path)
    datasets_path = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    datasets_path.write_text(
        datasets_path.read_text(encoding="utf-8")
        + "\n"
        + "schedule_date = ms.time_dimension_column(name='schedule_date', entity=orders, column='created_at', granularity='day')\n"
        + "is_working = ms.dimension_column(name='is_working', entity=orders, column='is_working')\n"
        + "sales_schedule = ms.work_schedule(name='sales_schedule', date=schedule_date, is_working=is_working, boundary_timezone='UTC', coverage=(__import__('datetime').date(2026, 1, 1), __import__('datetime').date(2026, 2, 5)))\n",
        encoding="utf-8",
    )
    session = session_attach.get_or_create(name="demo")
    schedule_ref = ref.work_schedule("sales.sales_schedule")
    schedule_rows = [
        {
            "date": date(2026, 1, 1) + timedelta(days=index),
            "is_working": index not in {1, 3, 32, 34},
        }
        for index in range(35)
    ]
    schedule_snapshot = certify_work_schedule(
        work_schedule_ref=schedule_ref,
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 2, 5)),
        rows=schedule_rows,
        date_column="date",
        is_working="is_working",
    )
    schedule_evidence = DiscoverySnapshot(
        id="schedule-evidence",
        datasource=ref.datasource("warehouse"),
        source=md.table("orders"),
        scope=md.unpruned(max_rows=35, timeout_seconds=30),
        columns=("created_at", "is_working"),
        schema_fingerprint="schedule-v1",
        profiles=(),
        coverage=SnapshotCoverage(
            observed_row_count=35,
            retained_row_count=35,
            scope_exhaustion="exhaustive",
            scope_exactness="scope_exact",
            sampling_method="first_rows_limit",
            pushed_predicate=(),
        ),
        persist_values=True,
        value_evidence_state="available",
        cache_status="fresh",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        _project_root=tmp_path,
        retained_values=tuple(
            (row["date"].isoformat(), row["is_working"]) for row in schedule_rows
        ),
    )
    session.catalog.preview(schedule_ref, using=schedule_evidence)
    schedule_entry = session.catalog.work_schedules.get(schedule_ref)
    assert schedule_entry.details().snapshot_status == "current"

    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "sales.orders.schedule_date",
        }
    }
    observation = BuiltinPeriodBindingV1(level_name="day", boundary_timezone="UTC")

    def make_schedule_frame(
        data: pd.DataFrame, *, start: date | datetime, end: date | datetime
    ) -> MetricFrame:
        frame = make_metric_frame(
            data,
            metric_id="sales.revenue",
            axes=axes,
            measure={"name": "value"},
            semantic_kind="time_series",
            semantic_model="sales",
            window={"start": start.isoformat(), "end": end.isoformat()},
            session=session,
        )
        return _attach_temporal_contract(
            frame,
            start=start,
            end=end,
            observation_period=observation,
        )

    current = make_schedule_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
                ),
                "value": [10.0, 20.0, 30.0, 40.0],
            }
        ),
        start=datetime(2026, 1, 1, 12),
        end=datetime(2026, 1, 5),
    )
    baseline = make_schedule_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-02-01", "2026-02-02", "2026-02-03", "2026-02-04"]
                ),
                "value": [1.0, 2.0, 3.0, 4.0],
            }
        ),
        start=datetime(2026, 2, 1, 12),
        end=datetime(2026, 2, 5),
    )

    delta = compare(
        current,
        baseline,
        alignment=working_day_progress(schedule=schedule_entry),
        session=session,
    )
    output = delta.to_pandas()
    assert len(output) == 2
    assert list(output["bucket_start_a"]) == [
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-03"),
    ]
    evidence = delta.meta.temporal_contract.alignment_evidence
    assert evidence.paired_points == 2
    assert evidence.policy_excluded_current_points == 2
    assert evidence.policy_excluded_baseline_points == 2
    assert delta.meta.temporal_contract.work_schedule is not None
    assert delta.meta.temporal_contract.work_schedule.work_schedule_ref == schedule_ref.path
    assert (
        delta.meta.temporal_contract.work_schedule.snapshot_digest
        == schedule_snapshot.snapshot_digest
    )
    snapshot_path = (
        WorkScheduleSnapshotStore(tmp_path)._directory(schedule_ref)
        / f"{schedule_snapshot.snapshot_digest}.json"
    )
    snapshot_path.unlink()
    recovered = session.get_frame(delta.ref)
    pd.testing.assert_frame_equal(recovered.to_pandas(), delta.to_pandas())
    assert recovered.evidence_status == "complete"
    assert recovered.evidence_digest == delta.evidence_digest
    findings = session.evidence.findings(artifact_ref=delta.ref).items
    assert len(findings) == 2
    assert len({finding.canonical_item_key for finding in findings}) == 2


def test_compare_working_day_progress_uses_frames_from_public_observe(tmp_path):
    """Schedule alignment must consume the same frames users get from observe."""
    bootstrap_sales_project(tmp_path)
    datasets_path = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    datasets_path.write_text(
        datasets_path.read_text(encoding="utf-8")
        + "\n"
        + "schedule_date = ms.time_dimension_column(name='schedule_date', entity=orders, column='created_at', granularity='day')\n"
        + "is_working = ms.dimension_column(name='is_working', entity=orders, column='is_working')\n"
        + "sales_schedule = ms.work_schedule(name='sales_schedule', date=schedule_date, is_working=is_working, boundary_timezone='UTC', coverage=(__import__('datetime').date(2026, 1, 1), __import__('datetime').date(2026, 2, 5)))\n",
        encoding="utf-8",
    )
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, is_working BOOLEAN)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        + ",".join(
            f"({index + 1}, DATE '2026-01-{index + 1:02d}', {index + 1}.0, "
            f"{str(index not in {1, 3}).upper()})"
            for index in range(10)
        )
    )
    session = session_attach.get_or_create(
        name="schedule-observe",
        backends={"warehouse": lambda: backend},
        report_timezone="UTC",
    )
    schedule_ref = ref.work_schedule("sales.sales_schedule")
    schedule_rows = [
        {
            "date": date(2026, 1, 1) + timedelta(days=index),
            "is_working": index not in {1, 3, 32, 34},
        }
        for index in range(35)
    ]
    schedule_snapshot = certify_work_schedule(
        work_schedule_ref=schedule_ref,
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2026, 2, 5)),
        rows=schedule_rows,
        date_column="date",
        is_working="is_working",
    )
    schedule_evidence = DiscoverySnapshot(
        id="schedule-observe-evidence",
        datasource=ref.datasource("warehouse"),
        source=md.table("orders"),
        scope=md.unpruned(max_rows=35, timeout_seconds=30),
        columns=("created_at", "is_working"),
        schema_fingerprint="schedule-observe-v1",
        profiles=(),
        coverage=SnapshotCoverage(
            observed_row_count=35,
            retained_row_count=35,
            scope_exhaustion="exhaustive",
            scope_exactness="scope_exact",
            sampling_method="first_rows_limit",
            pushed_predicate=(),
        ),
        persist_values=True,
        value_evidence_state="available",
        cache_status="fresh",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        _project_root=tmp_path,
        retained_values=tuple(
            (row["date"].isoformat(), row["is_working"]) for row in schedule_rows
        ),
    )
    session.catalog.preview(schedule_ref, using=schedule_evidence)
    metric = ref.metric("sales.revenue")
    current = session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-01-05"),
        grain=mv.grain("day"),
    )
    baseline = session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-01-06", end="2026-01-11"),
        grain=mv.grain("day"),
    )

    delta = session.compare(
        current,
        baseline,
        alignment=working_day_progress(schedule=schedule_ref, unmatched="drop"),
    )

    assert len(delta.to_pandas()) == 2
    assert delta.meta.temporal_contract is not None
    evidence = delta.meta.temporal_contract.alignment_evidence
    assert evidence.paired_points == 2
    assert evidence.policy_excluded_current_points == 2


def test_period_progress_rejects_builtin_coarse_source_against_semantic_target(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    snapshot = _slice3_snapshot()
    TemporalSnapshotStore(session.project_root).publish(
        snapshot,
        definition_digest="sha256:test-definition",
    )
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "week",
            "time_dimension": "sales.orders.created_at",
        }
    }
    binding = BuiltinPeriodBindingV1(level_name="week", boundary_timezone="UTC")
    scope = TimeScopeContractV1(
        kind="calendar_period",
        start=date(2026, 1, 1),
        end=date(2026, 1, 29),
        calendar_ref="sales.retail",
        snapshot_digest=snapshot.snapshot_digest,
        boundary_timezone="UTC",
        level="fiscal_month",
        key="M1",
    )
    frames = []
    for values, start, end in (
        ([1.0, 2.0], date(2026, 1, 1), date(2026, 1, 15)),
        ([11.0, 12.0], date(2026, 1, 29), date(2026, 2, 12)),
    ):
        frame = make_metric_frame(
            pd.DataFrame(
                {
                    "bucket_start": pd.to_datetime([start, start + timedelta(days=7)]),
                    "value": values,
                }
            ),
            metric_id="sales.revenue",
            axes=axes,
            measure={"name": "value"},
            semantic_kind="time_series",
            semantic_model="sales",
            window={"start": start.isoformat(), "end": end.isoformat()},
            session=session,
        )
        frames.append(
            _attach_temporal_contract(
                frame, start=start, end=end, observation_period=binding, scope=scope
            )
        )

    with pytest.raises(
        AlignmentPolicyNotApplicableError, match="source and target period authority"
    ):
        compare(frames[0], frames[1], alignment=period_progress(), session=session)


@pytest.mark.parametrize(
    ("baseline_additivity", "baseline_aggregation", "baseline_status_time_dimension"),
    [
        (None, "sum", None),
        ("additive", "mean", None),
        ("additive", "sum", "sales.orders.snapshot_at"),
    ],
)
def test_compare_fails_attribution_closed_when_metric_semantics_differ(
    tmp_path,
    baseline_additivity,
    baseline_aggregation,
    baseline_status_time_dimension,
):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    axes = {
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        }
    }
    current = make_metric_frame(
        pd.DataFrame({"region": ["NORTH"], "value": [30.0]}),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        additivity="additive",
        aggregation="sum",
        session=session,
    )
    baseline = make_metric_frame(
        pd.DataFrame({"region": ["NORTH"], "value": [20.0]}),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        additivity=baseline_additivity,
        aggregation=baseline_aggregation,
        status_time_dimension=baseline_status_time_dimension,
        session=session,
    )

    delta = compare(current, baseline, session=session)

    assert delta.meta.additivity is None
    assert delta.meta.aggregation is None
    assert delta.meta.status_time_dimension is None
    with pytest.raises(AttributeAdmissionBlockedError) as exc_info:
        session.attribute(
            delta,
            axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        )
    assert exc_info.value._context["blocker"] == "unsupported_aggregate"


def test_compare_rejects_delta_frame_as_second_argument(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-30"),
        session=s,
    )
    delta = compare(q3, q2, session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(q3, delta, session=s)  # type: ignore[arg-type]

    rendered = str(exc_info.value)
    assert (
        "SemanticKindMismatchError: compare(current, baseline) expected MetricFrame for `baseline`, got DeltaFrame."
        in rendered
    )
    assert "Repair:" in rendered
    assert "delta = session.compare(cur, base, alignment=mv.window_bucket())" in rendered
    assert exc_info.value._context["expected_kind"] == "metric_frame"
    assert exc_info.value._context["got_kind"] == "delta_frame"


def test_compare_semantic_kind_mismatch_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
        grain=mv.grain("day"),
        session=s,
    )
    with pytest.raises(SemanticKindMismatchError):
        compare(a, b, session=s)


def test_compare_rejects_non_alignment_policy(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(a, b, alignment="window_bucket", session=s)  # type: ignore[arg-type]

    assert exc_info.value._context["expected_kind"] == "AlignmentPolicy"
    assert exc_info.value._context["got_kind"] == "str"


def test_compare_rejects_loose_align_parameter(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-30"),
        session=s,
    )

    with pytest.raises(TypeError):
        compare(q3, q2, align="sample", session=s)  # type: ignore[call-arg]


def test_window_bucket_aligns_equal_length_time_series_by_ordinal_bucket(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=s,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-03"),
        grain=mv.grain("day"),
        session=s,
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert len(df) == 2
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-04-01", "2026-04-02"]
    assert list(df["delta"]) == [pytest.approx(5.0), pytest.approx(5.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"
    assert delta.meta.alignment["strict_lengths"] is False


def test_window_bucket_ordinal_rejects_time_series_grain_mismatch(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-04-01", "2026-04-02"], "revenue": [5.0, 15.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "hour"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, base, alignment=window_bucket(), session=s)

    assert exc_info.value._context["kind"] == "WindowBucketGrainMismatch"
    assert exc_info.value._context["current_grain"] == "day"
    assert exc_info.value._context["baseline_grain"] == "hour"


def test_window_bucket_no_overlap_different_expected_counts_uses_outer_ordinal_union(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
        grain=mv.grain("day"),
        session=s,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-02"),
        grain=mv.grain("day"),
        session=s,
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert len(df) == 2
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert str(df.iloc[0]["bucket_start_b"]) == "2026-04-01"
    assert pd.isna(df.iloc[1]["bucket_start_b"])
    assert df.iloc[0]["delta"] == pytest.approx(5.0)
    assert pd.isna(df.iloc[1]["baseline"])
    assert pd.isna(df.iloc[1]["delta"])
    assert delta.meta.alignment["mode"] == "ordinal_bucket"
    assert delta.meta.alignment["coverage"]["paired_buckets"] == 1
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 1
    assert delta.meta.alignment["coverage"]["baseline_unpaired_buckets"] == 0


def test_window_bucket_strict_lengths_rejects_different_expected_counts(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-02"),
        grain=mv.grain("day"),
        session=s,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-03"),
        grain=mv.grain("day"),
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            cur,
            base,
            alignment=window_bucket(strict_lengths=True),
            session=s,
        )

    assert "equal expected bucket counts" in str(exc_info.value)
    assert exc_info.value._context["kind"] == "WindowBucketExpectedCountMismatch"


def test_window_bucket_overlapping_windows_use_ordinal_mode_by_default(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-02", "2026-07-03"], "revenue": [7.0, 9.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-02", "end": "2026-07-04", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-07-02", "2026-07-03"]
    assert list(df["delta"]) == [pytest.approx(3.0), pytest.approx(11.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"


def test_window_bucket_calendar_mode_outer_joins_bucket_keys(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-03"], "revenue": [10.0, 30.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [8.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )

    delta = compare(
        cur,
        base,
        alignment=window_bucket(mode="calendar_bucket"),
        session=s,
    )

    df = delta.to_pandas()
    assert "bucket_start_b" not in df.columns
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert df.iloc[0]["delta"] == pytest.approx(2.0)
    assert pd.isna(df.iloc[1]["current"])
    assert pd.isna(df.iloc[2]["baseline"])
    assert delta.meta.alignment["mode"] == "calendar_bucket"


def test_window_bucket_february_to_march_daily_uses_outer_ordinal_union(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2026-02-01", periods=28, freq="D"),
                "revenue": [float(value) for value in range(1, 29)],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-02-01", "end": "2026-03-01", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2026-03-01", periods=31, freq="D"),
                "revenue": [float(value) for value in range(101, 132)],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-03-01", "end": "2026-04-01", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert len(df) == 31
    assert str(df.iloc[0]["bucket_start"]).startswith("2026-02-01")
    assert str(df.iloc[0]["bucket_start_b"]).startswith("2026-03-01")
    assert pd.isna(df.iloc[28]["bucket_start"])
    assert str(df.iloc[28]["bucket_start_b"]).startswith("2026-03-29")
    assert delta.meta.alignment["coverage"]["paired_buckets"] == 28
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 0
    assert delta.meta.alignment["coverage"]["baseline_unpaired_buckets"] == 3


def test_window_bucket_leap_year_february_returns_rows_by_default(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2024-02-01", periods=29, freq="D"),
                "revenue": [1.0] * 29,
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2024-02-01", "end": "2024-03-01", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2025-02-01", periods=28, freq="D"),
                "revenue": [1.0] * 28,
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2024-02-01", "end": "2024-03-01", "grain": "day"},
        session=s,
    )
    base.meta = base.meta.model_copy(
        update={"window": {"start": "2025-02-01", "end": "2025-03-01", "grain": "day"}}
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert len(df) == 29
    assert pd.isna(df.iloc[28]["bucket_start_b"])
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 1


def test_alignment_policy_window_bucket_defaults_dump_explicit_mode():
    policy = window_bucket()

    assert policy.model_dump(mode="json") == {
        "kind": "window_bucket",
        "mode": "ordinal_bucket",
        "strict_lengths": False,
    }


def test_window_bucket_no_overlap_uses_window_spine_for_sparse_time_series(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-04-01"], "revenue": [5.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-04-01", "end": "2026-04-03", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-04-01", "2026-04-02"]
    assert df.iloc[0]["delta"] == pytest.approx(5.0)
    assert pd.isna(df.iloc[1]["baseline"])
    assert pd.isna(df.iloc[1]["delta"])
    assert delta.meta.alignment["coverage"]["baseline"]["missing_buckets"] == 1


@pytest.mark.parametrize(
    ("current", "baseline", "expected_pct", "expected_status"),
    [
        (10.0, 0.0, np.nan, "from_zero_growth"),
        (-5.0, 0.0, np.nan, "from_zero_decline"),
        (0.0, 0.0, np.nan, "zero_baseline_no_change"),
        (-50.0, -100.0, 0.5, "computed"),
        (10.0, np.nan, np.nan, "not_computable"),
    ],
)
def test_compare_pct_change_status_handles_zero_missing_and_negative_baseline(
    tmp_path, current, baseline, expected_pct, expected_status
):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo")
    cur = make_metric_frame(
        pd.DataFrame({"value": [current]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({"value": [baseline]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )

    delta = compare(cur, base, session=s)

    row = delta.to_pandas().iloc[0]
    if pd.notna(baseline):
        assert row["delta"] == pytest.approx(current - baseline)
    else:
        assert pd.isna(row["delta"])
    if pd.isna(expected_pct):
        assert pd.isna(row["pct_change"])
    else:
        assert row["pct_change"] == expected_pct
    assert row["pct_change_status"] == expected_status


def test_compare_scalar_rejects_multirow_inputs(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo")
    cur = make_metric_frame(
        pd.DataFrame({"value": [10.0, 11.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({"value": [8.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, base, session=s)

    assert exc_info.value._context["kind"] == "ScalarCompareRequiresSingleRow"
    assert exc_info.value._context["current_rows"] == 2
    assert exc_info.value._context["baseline_rows"] == 1


def test_window_bucket_no_overlap_supports_quarter_grain(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": ["2026-04-01", "2026-07-01"],
                "revenue": [100.0, 200.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "quarter"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-04-01", "end": "2026-10-01", "grain": "quarter"},
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": ["2025-04-01", "2025-07-01"],
                "revenue": [80.0, 150.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "quarter"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2025-04-01", "end": "2025-10-01", "grain": "quarter"},
        session=s,
    )

    delta = compare(cur, base, alignment=window_bucket(), session=s)

    df = delta.to_pandas()
    assert list(df["bucket_start"].astype(str)) == ["2026-04-01", "2026-07-01"]
    assert list(df["bucket_start_b"].astype(str)) == ["2025-04-01", "2025-07-01"]
    assert list(df["delta"]) == [pytest.approx(20.0), pytest.approx(50.0)]


def test_compare_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    d = compare(a, b, alignment=window_bucket(), session=s)
    compare_jobs = [j for j in s.jobs() if j.intent == "compare"]
    assert len(compare_jobs) == 1
    assert compare_jobs[0].output_frame_ref == d.ref
    assert (s._layout.frames_dir / d.ref / "data.parquet").is_file()
    job_record = s.job(compare_jobs[0].id)
    assert job_record["params"]["alignment"]["kind"] == "window_bucket"
    assert job_record["schema"] == "marivo.analysis_job/v2"
    assert job_record["subject"]["kind"] == "delta_metric"
    assert "semantic_model" not in job_record

    persisted_meta = json.loads((s._layout.frames_dir / d.ref / "meta.json").read_text())
    assert {"metric_id", "semantic_model", "status_time_dimension"}.isdisjoint(persisted_meta)
    assert persisted_meta["comparison_identity"]["current"]["metric_ref"]["path"] == (
        "sales.revenue"
    )
    assert persisted_meta["catalog_definition_fingerprint"]
    assert persisted_meta["temporal_contract"]["alignment_policy"]["kind"] == "window_bucket"
    assert persisted_meta["temporal_contract"]["alignment_evidence"]["execution_path"] == "local"
    loaded = s.get_frame(d.ref)
    assert loaded.meta.metric_id == "sales.revenue"
    assert loaded.meta.semantic_model == "sales"
    assert loaded.meta.temporal_contract == d.meta.temporal_contract


def test_compare_works_in_read_only_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_write = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s_write)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s_write)
    s_write.close()
    session_attach._reset_process_state()
    s_read = session_attach.get_or_create(name="demo", use_datasources=False)
    assert s_read.is_read_only
    df_a, meta_a = read_frame_from_disk(s_read._layout, a.ref)
    df_b, meta_b = read_frame_from_disk(s_read._layout, b.ref)
    d = compare(
        MetricFrame(_df=df_a, meta=MetricFrameMeta(**meta_a)),
        MetricFrame(_df=df_b, meta=MetricFrameMeta(**meta_b)),
        alignment=window_bucket(),
        session=s_read,
    )
    assert isinstance(d, DeltaFrame)


def test_compare_works_in_read_only_session_no_backend(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    # Re-open session without backend -> read-only, but compare still works.
    session_attach._reset_process_state()
    s_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    d = compare(a, b, session=s_ro)
    assert isinstance(d, DeltaFrame)


def test_compare_works_after_archive_reopen(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    session_attach._reset_process_state()
    # Re-open without backends; compare still works since it only needs
    # persisted frame data, not a live backend connection.
    s_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    d = compare(a, b, session=s_ro)
    assert isinstance(d, DeltaFrame)


def test_compare_component_aware_scalar_missing_component_ref_fails_closed(tmp_path):
    s = session_attach.get_or_create(name="demo")
    current = make_metric_frame(
        pd.DataFrame({"failure_rate": [0.25]}),
        metric_id="sales.failure_rate",
        axes={},
        measure={"name": "failure_rate"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    baseline = make_metric_frame(
        pd.DataFrame({"failure_rate": [0.10]}),
        metric_id="sales.failure_rate",
        axes={},
        measure={"name": "failure_rate"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    current.meta = current.meta.model_copy(
        update={
            "composition": {
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            }
        }
    )

    with pytest.raises(ComponentFrameUnavailableError):
        compare(current, baseline, session=s)


def _bootstrap_unit_sales_project(tmp_path) -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', "
        "name='revenue',  unit='CNY')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def test_compare_propagates_metric_unit_to_delta_meta(tmp_path):
    _bootstrap_unit_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-31"),
        session=s,
    )
    assert q3.meta.unit == "CNY"
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start="2026-04-01", end="2026-04-30"),
        session=s,
    )
    d = compare(q3, q2, session=s)
    assert d.meta.unit == "CNY"
    assert "unit=CNY" in d._repr_identity()


def _bootstrap_compare_axis_project(tmp_path) -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def shipped_date(orders):\n"
        "    return orders.shipped_at.cast('date')\n\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region\n\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _compare_axis_session(tmp_path):
    _bootstrap_compare_axis_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, shipped_at DATE, "
        "amount DOUBLE, region VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', DATE '2026-07-03', 10.0, 'NORTH'),"
        "(2, DATE '2026-08-01', DATE '2026-08-03', 20.0, NULL)"
    )
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def test_compare_key_schema_is_stable_across_observed_null_distribution(tmp_path):
    session = _compare_axis_session(tmp_path)
    metric = make_ref("sales.revenue", SemanticKind.METRIC)
    region = make_ref("sales.orders.region", SemanticKind.DIMENSION)
    july = observe(
        metric,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        dimensions=[region],
        session=session,
    )
    august = observe(
        metric,
        time_scope=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        dimensions=[region],
        session=session,
    )

    assert july.meta.key_schema is not None
    assert august.meta.key_schema is not None
    assert july.meta.key_schema.fingerprint == august.meta.key_schema.fingerprint
    delta = compare(august, july, session=session)
    assert len(delta.to_pandas()) == 2


def test_compare_rejects_different_explicit_time_dimension_identities(tmp_path):
    session = _compare_axis_session(tmp_path)
    metric = make_ref("sales.revenue", SemanticKind.METRIC)
    time_scope = mv.time_scope(start="2026-07-01", end="2026-09-01")
    ordered = observe(
        metric,
        time_scope=time_scope,
        grain=mv.grain("day"),
        time_dimension=make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION),
        session=session,
    )
    shipped = observe(
        metric,
        time_scope=time_scope,
        grain=mv.grain("day"),
        time_dimension=make_ref("sales.orders.shipped_date", SemanticKind.TIME_DIMENSION),
        session=session,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(ordered, shipped, session=session)
    assert exc_info.value._context["kind"] == "TimeDimensionIdentityMismatch"
    assert exc_info.value._context["current_time_dimension"] == "sales.orders.order_date"
    assert exc_info.value._context["baseline_time_dimension"] == "sales.orders.shipped_date"


@pytest.mark.parametrize("conflicting_name", ["current", "baseline", "delta"])
def test_compare_time_series_rejects_time_column_colliding_with_protocol(
    tmp_path, conflicting_name: str
) -> None:
    """A time_series time column named like a compare protocol column must fail
    closed with a typed error instead of a pandas raw exception (issue #39,
    time_series path)."""
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo")
    cur = make_metric_frame(
        pd.DataFrame({conflicting_name: ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": conflicting_name, "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
        pd.DataFrame({conflicting_name: ["2026-04-01", "2026-04-02"], "revenue": [5.0, 15.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": conflicting_name, "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(cur, base, alignment=window_bucket(), session=s)

    error = exc_info.value
    assert error._context["reason"] == "protocol_column_collision"
    assert conflicting_name in error._context["conflicting_columns"]
    assert error.location == "session.compare"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"
