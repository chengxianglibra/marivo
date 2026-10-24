"""Independent temporal boundaries, full-window evaluation and terminal reads."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal_grid import BoundTimeGrid, bind_grid
from marivo.analysis.core.time_grid import instant
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.errors import AnalysisError
from marivo.refs import ref as ref_factory
from tests.shared_fixtures import (
    DSL_NAMES,
    DslCase,
    DslCaseFactory,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    seed_analysis_dsl_database,
    terminal_runs,
)
from tests.support.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    ("day", "hours", "start", "end"),
    [
        ("2026-03-08", 23, "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
        ("2026-11-01", 25, "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
    ],
)
def test_civil_day_retains_actual_dst_instants(day: str, hours: int, start: str, end: str) -> None:
    scope = exact_scope(
        start=date.fromisoformat(day),
        end=date.fromisoformat(day) + timedelta(days=1),
        timezone="America/New_York",
    )
    grid = bind_grid(scope, mv.grain("day"), report_timezone="America/New_York")
    assert len(grid.cells) == 1
    cell = grid.cells[0]
    assert cell.start.isoformat() == start
    assert cell.end.isoformat() == end
    assert (cell.end - cell.start).total_seconds() == hours * 3600
    assert not cell.partial
    hourly = bind_grid(scope, mv.grain("hour"), report_timezone="America/New_York")
    assert len(hourly.cells) == hours
    assert all((c.end - c.start).total_seconds() == 3600 for c in hourly.cells)


def test_partial_cells_are_retained_by_shared_grid() -> None:
    scope = exact_scope(start="2026-08-01", end="2026-09-01", timezone="UTC")
    week = bind_grid(scope, mv.grain("week"), report_timezone="UTC")
    assert week.cells[0].original_start.isoformat() == "2026-07-27T00:00:00+00:00"
    assert week.cells[0].partial
    assert week.cells[-1].partial
    day = bind_grid(scope, mv.grain("day"), report_timezone="UTC")
    assert len(day.cells) == 31


def test_named_occurrence_scopes_keep_distinct_grid_identity() -> None:
    from marivo._temporal import certify_temporal_set

    snapshot = certify_temporal_set(
        temporal_set_ref=ref_factory.temporal_set("sales.promotions"),
        boundary_timezone="UTC",
        coverage=(date(2026, 8, 1), date(2026, 8, 4)),
        rows=(
            {"id": "a", "start": date(2026, 8, 1), "end": date(2026, 8, 3)},
            {"id": "b", "start": date(2026, 8, 1), "end": date(2026, 8, 3)},
        ),
        occurrence_id="id",
        start="start",
        end="end",
    )
    first = bind_grid(snapshot.occurrence_scope("a"), mv.grain("day"), report_timezone="UTC")
    second = bind_grid(snapshot.occurrence_scope("b"), mv.grain("day"), report_timezone="UTC")
    assert first.scope_digest == second.scope_digest == snapshot.snapshot_digest
    assert first.identity != second.identity


@pytest.mark.parametrize("wall", [datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)])
def test_naive_gap_and_fold_have_no_implicit_resolution(wall: datetime) -> None:
    with pytest.raises(DatasetConstructionError, match="instants"):
        instant(wall, "America/New_York")


def test_grid_wire_preserves_authority_and_rejects_changed_identity() -> None:
    grid = bind_grid(
        exact_scope(start="2026-03-08", end="2026-03-09", timezone="America/New_York"),
        mv.grain("day"),
        report_timezone="America/New_York",
    )
    adapter = TypeAdapter(BoundTimeGrid)
    assert adapter.validate_json(adapter.dump_json(grid), strict=True) == grid
    with pytest.raises(ValueError, match="identity"):
        replace(grid, boundary_timezone="UTC")


def test_aware_endpoint_remains_instant() -> None:
    stamp = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert instant(stamp, "America/New_York") == stamp


def test_certified_grid_keeps_calendar_authority_and_coverage() -> None:
    from marivo._temporal import certify_period_calendar_rows, semantic_grain

    calendar = ref_factory.period_calendar("sales.fiscal")
    snapshot = certify_period_calendar_rows(
        calendar_ref=calendar,
        boundary_timezone="Asia/Shanghai",
        coverage=(date(2026, 8, 1), date(2026, 8, 4)),
        columns=("day", "period"),
        retained_values=tuple(
            {"day": f"2026-08-0{i}", "period": "P1" if i < 3 else "P2"} for i in (1, 2, 3)
        ),
        date_column="day",
        levels={"period": "period"},
    )
    grain = semantic_grain(calendar=calendar, level="period")
    scope = exact_scope(
        start=datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, 16, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
    )
    grid = bind_grid(scope, grain, report_timezone="America/New_York", snapshot=snapshot)
    assert grid.snapshot_digest == snapshot.snapshot_digest
    assert grid.report_timezone == "America/New_York"
    assert grid.boundary_timezone == "Asia/Shanghai"
    assert [(c.start.isoformat(), c.end.isoformat()) for c in grid.cells] == [
        ("2026-07-31T16:00:00+00:00", "2026-08-02T16:00:00+00:00"),
        ("2026-08-02T16:00:00+00:00", "2026-08-03T16:00:00+00:00"),
    ]
    with pytest.raises(ValueError, match="conflicts"):
        bind_grid(scope, grain, report_timezone="UTC", explicit_timezone="UTC", snapshot=snapshot)
    with pytest.raises(ValueError, match="coverage"):
        bind_grid(
            exact_scope(start="2026-07-30", end="2026-08-04", timezone="Asia/Shanghai"),
            grain,
            report_timezone="UTC",
            snapshot=snapshot,
        )


def test_single_second_grid_has_exact_boundaries() -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=1)
    grid = bind_grid(
        exact_scope(start=start, end=end, timezone="UTC"),
        mv.grain("second"),
        report_timezone="UTC",
    )

    assert len(grid.cells) == 1
    assert grid.cells[0].start == start
    assert grid.cells[0].end == end


def test_repeated_hour_minute_cells_keep_both_instant_sequences() -> None:
    grid = bind_grid(
        exact_scope(
            start=datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc),
            end=datetime(2026, 11, 1, 6, 1, tzinfo=timezone.utc),
            timezone="America/New_York",
        ),
        mv.grain("minute"),
        report_timezone="America/New_York",
    )
    assert [(cell.start.isoformat(), cell.end.isoformat()) for cell in grid.cells] == [
        ("2026-11-01T05:59:00+00:00", "2026-11-01T06:00:00+00:00"),
        ("2026-11-01T06:00:00+00:00", "2026-11-01T06:01:00+00:00"),
    ]


def _prepare_temporal_grid_project(root: Path) -> Path:
    root.mkdir()
    database_path = root / "warehouse.duckdb"
    seed_analysis_dsl_database(
        database_path, DSL_NAMES, analysis_dsl_rows("j1"), float_amount=False
    )
    (root / "marivo.toml").write_text('[project]\nname = "analysis-dsl-fixture"\n')
    for relative_path, source in analysis_dsl_project_files(DSL_NAMES, database_path).items():
        destination = (
            root / "models" / relative_path
            if relative_path.startswith("datasources/")
            else root / "models" / "semantic" / relative_path
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)
    return database_path


def _observation(
    case: DslCase, *, start: str, end: str, grain: Literal["day", "month"] | None = None
) -> mv.AnalysisFrame:
    session = case.session
    during = session.time_scope(start=start, end=end)
    population = mv.product(
        during.by(mv.grain(grain)) if grain is not None else during,
        session.members(session.catalog.entities.get("sales.customer")),
    )
    assert isinstance(population, mv.Population)
    return session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        by=population,
    )


def test_member_time_product_executes_on_frame(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    members = session.members(session.catalog.entities.get("sales.customer"))
    population = mv.product(calendar, members)
    assert isinstance(population, mv.Population)
    result = session.frame(population).persist()
    frame = result.to_pandas()
    assert frame.iloc[:, 0].tolist() == [0] * 4 + [1] * 4
    assert frame.iloc[:, 1].tolist() == ["A", "B", "C", "D"] * 2
    directory = frame.attrs["frozen_coordinates"][0]["rows"]
    assert directory["nominal_start"].tolist() == [
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    ]
    pd.testing.assert_frame_equal(session.persisted_frame(result.artifact.id).to_pandas(), frame)


def test_time_product_observation_keeps_empty_cells_and_window_binding(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    assert calendar.scope is not None
    members = session.members(session.catalog.entities.get("sales.customer"))
    metric = session.catalog.metrics.get("sales.revenue")
    population = mv.product(calendar, members)
    assert isinstance(population, mv.Population)
    query = session.observe({"value": metric}, by=population)
    saved = query.persist()
    frame = saved.to_pandas()
    assert frame[("value", "value")].tolist() == [450, 150, 400, 0, 99, 0, 0, 0]
    assert frame.iloc[:, 0].tolist() == [0] * 4 + [1] * 4
    assert frame.iloc[:, 1].tolist() == ["A", "B", "C", "D"] * 2
    original = session.observe({"value": metric}, by=(calendar.scope,)).compute()
    assert original.to_pandas()[("value", "value")].tolist() == [1099]
    assert saved.reduce({"value": saved["value"].sum()}).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [1099]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert saved.to_pandas()[("value", "value")].sum() == 1099
    pd.testing.assert_frame_equal(session.persisted_frame(saved.artifact.id).to_pandas(), frame)


def test_public_grid_observation_and_retained_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    query = _observation(case, start="2026-08-01", end="2026-11-01", grain="month")
    calendar = query.axes[0]
    totals = query.reduce({"value": query["value"].sum()}, by=(calendar,)).compute().to_pandas()
    assert totals[("value", "value")].tolist() == [1000, 99, 0]
    assert totals.attrs["axes"][0]["descriptor"]["timezone"] == "UTC"


def test_coarser_calendar_requires_explicit_source_observation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    daily = _observation(case, start="2026-08-01", end="2026-09-01", grain="day")
    month = case.session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("month"))
    coarse = case.session.observe(
        {"value": case.session.catalog.metrics.get("sales.revenue")}, by=(month,)
    )
    assert coarse.compute().to_pandas()[("value", "value")].tolist() == [1000]
    assert daily.reduce({"value": daily["value"].sum()}).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [1000]


def test_identity_observation_has_no_inferred_relationship(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    population = mv.product(
        session.time_scope(start="2026-08-01", end="2026-09-01"),
        session.members(session.catalog.entities.get("sales.order")),
    )
    assert isinstance(population, mv.Population)
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        by=population,
    )
    assert observed.reduce({"value": observed["value"].sum()}).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [1000]


@pytest.mark.slow
@pytest.mark.release
def test_grid_produce_and_offline_read_are_separate_processes(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "temporal-grid"
    database = _prepare_temporal_grid_project(root)
    environment = dict(os.environ, MARIVO_PROJECT_ROOT=str(root), PYTHONPATH=str(PROJECT_ROOT))
    producer = """
import json
import marivo.analysis as mv
import marivo.semantic as ms
session = mv.session.create('temporal-grid-recovery', timezone='UTC')
calendar = session.time_scope(start='2026-08-01', end='2026-11-01').by(mv.grain('month'))
metric = session.catalog.metrics.get('sales.revenue').window(mv.all_history())
result = session.observe({'value':metric}, by=(calendar,)).persist()
assert result.to_pandas()[('value','value')].tolist() == [1077,1176,1176]
print(json.dumps({'session':session.id,'artifact':result.artifact.id}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", producer],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    produced = json.loads(completed.stdout.splitlines()[-1])
    database.rename(database.with_suffix(".offline"))
    (root / "models").rename(root / "models.offline")
    reader = """
import json, sys
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('terminal calendar read touched source or semantic models')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
SourceSession.execute = forbidden
result = mv.session.resume(sys.argv[1]).persisted_frame(sys.argv[2])
frame = result.to_pandas()
assert frame[('value','value')].tolist() == [1077,1176,1176]
assert frame.attrs['axes'][0]['descriptor']['timezone'] == 'UTC'
assert len(frame.attrs['frozen_coordinates'][0]['rows']) == 3
print(json.dumps({'value':[str(value) for value in frame[('value','value')].tolist()]}))
"""
    recovered = subprocess.run(
        [sys.executable, "-c", reader, produced["session"], produced["artifact"]],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout.splitlines()[-1])["value"] == ["1077", "1176", "1176"]


def test_grid_saved_data_damage_rejects_complete_reads(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    result = _observation(case, start="2026-08-01", end="2026-10-01", grain="month").persist()
    from marivo.analysis.materialization.graph_reading import open_manifest

    data = open_manifest(result._local.store, result._local.session_ref, result.artifact.id).data
    handles = (data.rows, *(attachment.rows for attachment in data.attachments))
    assert len(handles) == 2
    for handle in handles:
        receipt = handle.receipt
        path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        original = path.read_bytes()
        for damage in ("missing", "corrupt"):
            try:
                if damage == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"corrupt terminal calendar data")
                with pytest.raises(AnalysisError):
                    case.session.persisted_frame(result.artifact.id).to_pandas()
            finally:
                path.write_bytes(original)
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(result.artifact.id).to_pandas(), result.to_pandas()
    )


def test_cumulative_anchors_ignore_display_start(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-15", end="2026-10-01").by(mv.grain("month"))
    rules = (
        (mv.all_history(), [1077, 1176]),
        (mv.to_date(grain=mv.grain("month")), [1000, 99]),
        (mv.trailing_duration(duration=timedelta(days=31)), [1000, 499]),
    )
    saved = []
    for rule, expected in rules:
        metric = session.catalog.metrics.get("sales.revenue").window(rule)
        result = session.observe({"value": metric}, by=(calendar,)).persist()
        assert result.to_pandas()[("value", "value")].tolist() == expected
        # This current-row sum consumes both completed endpoints; it never reopens history.
        assert result.reduce({"value": result["value"].sum()}).compute().to_pandas()[
            ("value", "value")
        ].tolist() == [sum(expected)]
        scalar = session.observe(
            {"value": metric}, by=(session.time_scope(start="2026-09-30", end="2026-10-01"),)
        )
        assert scalar.compute().to_pandas()[("value", "value")].tolist() == [expected[-1]]
        saved.append((result, expected))
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    for result, expected in saved:
        assert (
            session.persisted_frame(result.artifact.id).to_pandas()[("value", "value")].tolist()
            == expected
        )


def _seed_two_sample_amounts(case: DslCase, *, overflow: bool = False) -> None:
    import duckdb

    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("DELETE FROM customer WHERE customer_id IN ('C', 'D')")
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("a1", "A", "web", "paid", "2026-08-01T00:00:00Z", 10),
                ("a2", "A", "web", "paid", "2026-08-02T00:00:00Z", 0),
                ("b1", "B", "web", "paid", "2026-08-01T00:00:00Z", 0),
                ("b2", "B", "web", "paid", "2026-08-02T00:00:00Z", 10),
            ],
        )
        if overflow:
            connection.execute('ALTER TABLE "order" ALTER COLUMN amount TYPE DOUBLE')
            connection.execute('UPDATE "order" SET amount = 1e308')


def test_fold_reaggregates_aligned_samples_before_time(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _seed_two_sample_amounts(case)
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    assert calendar.scope is not None
    revenue = session.catalog.metrics.get("sales.revenue")
    members = session.members(session.catalog.entities.get("sales.customer"))
    expected = {
        "first": [10, 0],
        "last": [0, 10],
        "mean": [5, 5],
        "min": [0, 0],
        "max": [10, 10],
    }
    timestamp_samples = session.catalog.fields.get("sales.order.ordered_at")
    daily_samples = mv.product(mv.coordinates(by=(), keys=((),)), calendar)
    daily_revenue = revenue.per(daily_samples)
    metrics = {
        "first": revenue.per(timestamp_samples).first(),
        "last": revenue.per(timestamp_samples).last(),
        "mean": daily_revenue.mean(),
        "min": daily_revenue.min(),
        "max": daily_revenue.max(),
    }
    population = mv.product(calendar.scope, members)
    assert isinstance(population, mv.Population)
    observed = session.observe(metrics, by=population).compute().to_pandas()
    for kind, values in expected.items():
        assert observed[("value", kind)].tolist() == values
    overall = session.observe(metrics, by=(calendar.scope,)).compute().to_pandas()
    for kind in expected:
        assert overall[("value", kind)].tolist() == [10]
    region = session.catalog.fields.get("sales.customer.region")
    regional_population = mv.product(calendar.scope, members.classify(by=(region,)))
    assert isinstance(regional_population, mv.Population)
    regional = session.observe(
        {"value": revenue.per(session.catalog.fields.get("sales.order.ordered_at")).first()},
        by=regional_population,
    )
    frame = regional.compute().to_pandas()
    assert frame.iloc[:, 1].tolist() == ["east"]
    assert frame[("value", "value")].tolist() == [10]


def test_fold_rejects_float64_overflow_before_source_spatial_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _seed_two_sample_amounts(case, overflow=True)
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    assert calendar.scope is not None
    metric = (
        session.catalog.metrics.get("sales.revenue")
        .per(session.catalog.fields.get("sales.order.ordered_at"))
        .first()
    )
    population = mv.product(
        calendar.scope,
        session.members(session.catalog.entities.get("sales.customer")),
    )
    assert isinstance(population, mv.Population)
    query = session.observe(
        {"value": metric},
        by=population,
    )
    assert query.compute().to_pandas()[("value", "value")].tolist() == [1e308, 1e308]
    with pytest.raises(AnalysisError) as failure:
        session.observe({"value": metric}, by=(calendar.scope,)).compute()
    from marivo.semantic.errors import SemanticExpressionError

    cause = failure.value.__cause__
    assert isinstance(cause, SemanticExpressionError)
    assert cause.expected == "finite values in the admitted numeric type"
    assert cause.received is not None and "internal sample reduction" in cause.received
    assert not hasattr(failure.value, "run_ref")
    assert terminal_runs(session) == ()


@pytest.mark.parametrize("wall", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_source_wall_clock_gap_and_fold_follow_native_interpretation(
    analysis_dsl_case_factory: DslCaseFactory,
    wall: str,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute('ALTER TABLE "order" ALTER ordered_at TYPE TIMESTAMP')
        connection.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)', ["x", "A", "web", "paid", wall, 10]
        )
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(
        path.read_text().replace(
            "parse=ms.timestamp()", "parse=ms.timestamp(timezone='America/New_York')"
        )
    )
    ms.load(workspace_dir=case.root)
    query = _observation(
        case, start=wall[:10], end=(date.fromisoformat(wall[:10]) + timedelta(days=1)).isoformat()
    )
    result = query.compute().to_pandas()
    assert result[("value", "value")].sum() == 10


def test_cumulative_ratio_binds_every_component_to_the_endpoint(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-15", end="2026-10-01").by(mv.grain("month"))
    ratio = session.catalog.metrics.get("sales.revenue") / session.catalog.metrics.get(
        "sales.order_count"
    )
    population = mv.product(
        calendar, session.members(session.catalog.entities.get("sales.customer"))
    )
    assert isinstance(population, mv.Population)
    query = session.observe(
        {"value": ratio.window(mv.all_history())},
        by=population,
    )
    result = session.observe({"value": ratio.window(mv.all_history())}, by=(calendar,)).persist()
    assert result.to_pandas()[("value", "value")].tolist() == [269.25, 235.2]
    pd.testing.assert_frame_equal(
        session.persisted_frame(result.artifact.id).to_pandas(), result.to_pandas()
    )


def test_raw_instant_membership_keeps_native_projection_without_rule_audit(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import duckdb
    import ibis
    import ibis.expr.types as ir
    import sqlglot
    from sqlglot import expressions as sge

    from marivo.analysis.compiler import source_time
    from tests.support.source_trace import capture_source

    case = analysis_dsl_case_factory("j1")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    # The fixture stores native TIMESTAMPTZ instants. This independent raw SQL
    # oracle bypasses every compiler-owned projection and predicate helper.
    with duckdb.connect(str(case.database_path)) as connection:
        selected = connection.execute(
            'SELECT order_id FROM "order" WHERE ordered_at >= ? AND ordered_at < ? '
            "ORDER BY order_id",
            [start, end],
        ).fetchall()
        expected = connection.execute(
            "SELECT c.customer_id, coalesce(sum(o.amount), 0) FROM customer c "
            'LEFT JOIN "order" o ON o.customer_id = c.customer_id '
            "AND o.ordered_at >= ? AND o.ordered_at < ? "
            "GROUP BY c.customer_id ORDER BY c.customer_id",
            [start, end],
        ).fetchall()
    assert selected == [("j1_a",), ("j1_b",), ("j1_c",)]
    assert expected == [("A", 450), ("B", 150), ("C", 400), ("D", 0)]
    original = source_time.render
    calls: list[str] = []

    def disagree(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
        calls.append(zone)
        return original(zone, value) + ibis.interval(hours=1)

    monkeypatch.setattr(source_time, "render", disagree)
    backend = ibis.duckdb.connect(case.database_path)
    try:
        orders = backend.table("order")
        projected = backend.execute(
            orders.select(
                order_id=orders.order_id, point=source_time.render("UTC", orders.ordered_at)
            )
        ).set_index("order_id")
        assert projected.loc["j1_july", "point"] == datetime(2026, 8, 1, 0, 59, 59)
        assert projected.loc["j1_c", "point"] == datetime(2026, 9, 1, 0, 59, 59)
    finally:
        backend.disconnect()
    trace = capture_source(monkeypatch)
    result = _observation(case, start="2026-08-01", end="2026-09-01").compute().to_pandas()
    assert (
        list(zip(result[("coordinate", "axis.1")], result[("value", "value")], strict=True))
        == expected
    )
    assert calls and set(calls) == {"UTC"}
    # Native disagreement must not trigger a standalone timezone-rule audit.
    # Every governed submission still reads the requested business relations.
    assert trace.native_sql
    for sql in trace.native_sql:
        tables = {table.name for table in sqlglot.parse_one(sql, read="duckdb").find_all(sge.Table)}
        assert tables & {"order", "customer"}


def test_fixed_date_windows_keep_authority_on_a_foreign_zone_grid(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute('ALTER TABLE "order" ALTER COLUMN ordered_at TYPE DATE')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("one", "A", "web", "paid", "2026-08-01", 10),
                ("two", "A", "web", "paid", "2026-08-02", 20),
            ],
        )
    path = case.root / "models/semantic/sales/models.py"
    path.write_text(path.read_text().replace("parse=ms.timestamp()", "parse=None"))
    ms.load(workspace_dir=case.root)
    session = mv.session.create("date-window", timezone="Asia/Tokyo")
    grid = session.time_scope(start="2026-08-01", end="2026-08-03", timezone="America/New_York").by(
        mv.grain("day")
    )
    population = mv.product(grid, session.members(session.catalog.entities.get("sales.customer")))
    assert isinstance(population, mv.Population)
    query = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        by=population,
    )
    frame = (
        session.observe({"value": session.catalog.metrics.get("sales.revenue")}, by=(grid,))
        .compute()
        .to_pandas()
    )
    assert frame[("value", "value")].tolist() == [10, 20]
    assert frame.attrs["axes"][0]["descriptor"]["timezone"] == "America/New_York"


def test_empty_fold_keeps_typed_terminal_output(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    query = session.observe(
        {
            "value": session.catalog.metrics.get("sales.revenue")
            .per(mv.product(mv.coordinates(by=(), keys=((),)), calendar))
            .max()
        },
        by=session.members(session.catalog.entities.get("sales.customer")),
    )
    result = query.persist()
    assert result.to_pandas().empty
    total = (
        session.observe(
            {
                "value": session.catalog.metrics.get("sales.revenue")
                .per(mv.product(mv.coordinates(by=(), keys=((),)), calendar))
                .max()
            }
        )
        .compute()
        .to_pandas()
    )
    assert total[("value", "value")].tolist() == [0]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    pd.testing.assert_frame_equal(
        session.persisted_frame(result.artifact.id).to_pandas(), result.to_pandas()
    )


def test_selected_groups_keep_sample_selection_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    category = session.read(members, {"region": region})
    selected_rows = category.where(category["region"] == "east")
    selected = session.population_from(
        selected_rows, on=((selected_rows.axes[0], session.catalog.entities.get("sales.customer")),)
    )
    calendar = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("day"))
    selected_groups = selected.classify(by=(region,))
    assert isinstance(selected_groups, mv.Population)
    query = session.observe(
        {
            "value": session.catalog.metrics.get("sales.revenue")
            .per(mv.product(mv.coordinates(by=(), keys=((),)), calendar))
            .max()
        },
        by=selected_groups,
        where=mv.in_population(selected),
    )
    result = query.persist()
    frame = result.to_pandas()
    assert tuple(axis.role for axis in result.axes) == ("field",)
    assert frame[("coordinate", result.axes[0].token)].tolist() == ["east"]
    assert frame[("value", "value")].tolist() == [450]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    pd.testing.assert_frame_equal(session.persisted_frame(result.artifact.id).to_pandas(), frame)


def test_observe_help_example_preserves_totals_and_ratio_grain(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo._help.render import render_help_text

    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    count = session.catalog.metrics.get("sales.order_count")
    calendar = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("month"))
    field = session.catalog.fields.get("sales.customer.region")
    metrics = {"amount": revenue, "count": count, "average": revenue / count}
    page = render_help_text("analysis.session.observe")[0]
    code = page.split("Example:\n", 1)[1].split("\nSee:", 1)[0]
    namespace: dict[str, object] = {
        "session": session,
        "metrics": metrics,
        "calendar": calendar,
        "field": field,
    }
    exec(code, namespace)
    result = namespace["result"]
    assert isinstance(result, mv.AnalysisFrame)
    overall = session.observe(metrics, by=(calendar,))
    detail = session.observe(metrics, by=(calendar, field))
    direct = overall.compute().to_pandas()
    pd.testing.assert_frame_equal(result.compute().to_pandas(), detail.compute().to_pandas())
    assert direct[("value", "amount")].tolist() == [1000]
    assert direct[("value", "count")].tolist() == [3]
    assert direct[("value", "average")].tolist() == pytest.approx([1000 / 3])
    grouped = detail.compute().to_pandas()
    assert grouped[("value", "average")].mean() == 350


@pytest.mark.slow
@pytest.mark.release
def test_non_key_outer_and_top_order_survive_cold_filter_reduce_republish(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import json
    import os
    import shutil
    import subprocess
    import sys

    import duckdb

    from tests.analysis.materialization.domain_recovery_worker import snapshot
    from tests.shared_fixtures import run_ids
    from tests.support.json import checked, encode, read

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE customer SET region=lower(customer_id)")
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("aug-c", "C", "web", "paid", "2026-08-01T00:00:00Z", 30),
                ("aug-a", "A", "web", "paid", "2026-08-01T00:00:00Z", 20),
                ("jul-b", "B", "web", "paid", "2026-07-01T00:00:00Z", 40),
                ("jul-c", "C", "web", "paid", "2026-07-01T00:00:00Z", 10),
            ],
        )
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    region = session.catalog.fields.get("sales.customer.region")
    current = session.observe(
        {"value": metric},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("month")), region),
    )
    baseline = session.observe(
        {"value": metric},
        by=(session.time_scope(start="2026-07-01", end="2026-08-01").by(mv.grain("month")), region),
    )
    left = current.top(2, by=current["value"]).compute()
    right = baseline.top(2, by=baseline["value"]).compute()
    assert left.to_pandas().iloc[:, 1].tolist() == ["c", "a"]
    assert right.to_pandas().iloc[:, 1].tolist() == ["b", "c"]
    outer = left.compare(
        {"reference": right},
        on={"reference": tuple(zip(left.axes, right.axes, strict=True))},
        match={"reference": mv.ordinal()},
    ).compute()
    top = outer.top(2, by=outer["value"]).compute()
    assert terminal_runs(session) == ()
    outer_rows, top_rows = outer.to_pandas(), top.to_pandas()
    assert outer_rows.iloc[:, 1].tolist() == ["c", "a", "b"]
    assert top_rows.iloc[:, 1].tolist() == ["c", "a"]
    assert outer_rows[("presence", "current.present")].tolist() == [True, True, False]
    assert outer_rows[("presence", "reference.present")].tolist() == [True, False, True]
    assert outer_rows[("value", "value")].isna().tolist() == [False, False, True]
    assert outer_rows[("value", "reference.baseline.value")].isna().tolist() == [False, True, False]
    assert outer_rows[("value", "reference.absolute.value")].iloc[0] == 20
    assert outer_rows[("value", "reference.absolute.value")].isna().tolist() == [False, True, True]
    assert outer_rows.attrs["units"] == (
        ("value", "CNY"),
        ("reference.baseline.value", "CNY"),
        ("reference.absolute.value", "CNY"),
        ("reference.relative.value", None),
    )
    assert len(outer_rows.attrs["frozen_coordinates"]) == 3
    saved_outer, saved_top = outer.persist(), top.persist()
    assert len(run_ids(session)) == 2
    state = checked(
        {
            "session": session.id,
            "producer_pid": os.getpid(),
            "runs": sorted(run_ids(session)),
            "outputs": {
                "outer": {
                    "artifact": saved_outer.artifact.id,
                    "snapshot": snapshot(saved_outer, frame=outer_rows),
                },
                "top": {
                    "artifact": saved_top.artifact.id,
                    "snapshot": snapshot(saved_top, frame=top_rows),
                },
            },
        }
    )
    state_path = case.root / "non-key-order.json"
    state_path.write_bytes(encode(state))
    shutil.rmtree(case.root / "models")
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    worker = """
import os, sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo import _temporal
from marivo.analysis.core import frame_calendar
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource import timezone as source_timezone
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import checked, encode, obj, read
root=Path(sys.argv[1])
os.chdir(root)
os.environ['MARIVO_PROJECT_ROOT']=str(root)
state=obj(read(root/'non-key-order.json'))
with ExitStack() as guards:
    for target,name,replacement in (
        (ms,'load',forbidden), (SemanticProject,'load',forbidden),
        (Session,'catalog',property(forbidden)), (SourceSession,'__init__',forbidden),
        (SourceSession,'bind',forbidden), (SourceSession,'compile',forbidden),
        (SourceSession,'execute',forbidden), (duckdb,'connect',forbidden),
        (ibis.duckdb,'connect',forbidden), (frame_calendar,'freeze_calendar',forbidden),
        (frame_source_preparation,'open_query_sources',forbidden),
        (source_timezone,'probe_engine_timezone',forbidden),
        (source_timezone,'_resolve_system_timezone',forbidden),
        (_temporal,'ZoneInfo',forbidden),
    ):
        guards.enter_context(patch.object(target,name,replacement))
    session=mv.session.resume(state['session'])
    before=run_ids(session)
    if sys.argv[2]=='continue':
        assert sorted(before)==state['runs']
        outputs=obj(state['outputs'])
        original={}
        for name,entry in outputs.items():
            item=obj(entry)
            saved=session.persisted_frame(item['artifact'])
            rows=saved.to_pandas()
            assert snapshot(saved,frame=rows)==item['snapshot']
            original[name]=saved
        outer,top=original['outer'],original['top']
        assert outer.to_pandas().iloc[:,1].tolist()==['c','a','b']
        assert top.to_pandas().iloc[:,1].tolist()==['c','a']
        filtered=outer.where(outer['current.present']).compute()
        rows=filtered.to_pandas()
        assert rows.iloc[:,1].tolist()==['c','a']
        assert rows[('presence','reference.present')].tolist()==[True,False]
        assert rows[('value','reference.absolute.value')].iloc[0]==20
        assert rows[('value','reference.absolute.value')].isna().tolist()==[False,True]
        reduced=filtered.reduce({'total':filtered['reference.absolute.value'].sum()},by=(filtered.axes[0],)).compute()
        totals=reduced.to_pandas()
        assert totals[('value','total')].tolist()==[30-10]
        assert reduced.presence_names==()
        assert totals.attrs['units']==(('total','CNY'),)
        assert len(totals.attrs['frozen_coordinates'])==1
        assert run_ids(session)==before
        copied={}
        for name,result in (('filtered',filtered),('reduced',reduced)):
            rows=result.to_pandas()
            saved=result.persist()
            reopened=session.persisted_frame(saved.artifact.id)
            assert snapshot(reopened,frame=reopened.to_pandas())==snapshot(saved,frame=rows)
            copied[name]={'artifact':saved.artifact.id,'snapshot':snapshot(saved,frame=rows)}
        assert len(run_ids(session))==len(before)+2
        (root/'non-key-copied.json').write_bytes(encode(checked({'outputs':copied,'runs':sorted(run_ids(session))})))
    else:
        copied=obj(read(root/'non-key-copied.json'))
        assert sorted(before)==copied['runs']
        for name,entry in obj(copied['outputs']).items():
            item=obj(entry)
            saved=session.persisted_frame(item['artifact'])
            rows=saved.to_pandas()
            assert snapshot(saved,frame=rows)==item['snapshot']
            if name=='filtered':
                assert rows.iloc[:,1].tolist()==['c','a']
            else:
                assert rows[('value','total')].tolist()==[20]
        assert run_ids(session)==before
    print(encode(checked({'pid':os.getpid(),'runs':sorted(run_ids(session))})).decode())
"""
    reports = []
    for phase in ("continue", "reopen"):
        completed = subprocess.run(
            [sys.executable, "-c", worker, str(case.root), phase],
            cwd=PROJECT_ROOT,
            env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="America/Los_Angeles"),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(json.loads(completed.stdout.splitlines()[-1]))
    assert len({os.getpid(), *(report["pid"] for report in reports)}) == 3
    assert read(case.root / "non-key-copied.json")


@pytest.mark.release
@pytest.mark.slow
@pytest.mark.parametrize("rule_name", ["periods", "years"])
def test_calendar_rules_keep_captured_facts_across_carriers(
    analysis_dsl_case_factory: DslCaseFactory,
    rule_name: Literal["periods", "years"],
) -> None:
    import os
    import shutil
    import subprocess
    import sys

    import duckdb

    from tests.analysis.materialization.domain_recovery_worker import snapshot
    from tests.support.json import checked, encode

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            ["previous-year", "A", "web", "paid", "2025-08-01T00:00:00Z", 50],
        )
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    current_scope = session.time_scope(start="2026-08-01", end="2026-09-01")
    year = rule_name == "years"
    previous_scope = session.time_scope(
        start="2025-08-01" if year else "2026-07-01",
        end="2025-09-01" if year else "2026-08-01",
    )
    rule = mv.offset(years=-1) if year else mv.offset(periods=-1)
    left = session.observe({"value": metric}, by=(current_scope.by(mv.grain("month")),))
    right = session.observe({"value": metric}, by=(previous_scope.by(mv.grain("month")),))
    expected_right = 50 if year else 77
    left_memory, right_memory = left.compute(), right.compute()
    left_saved, right_saved = left_memory.persist(), right_memory.persist()
    for first, second in ((left, right), (left_memory, right_memory), (left_saved, right_saved)):
        combined = first.compare(
            {"inferred": second, "explicit": second},
            on={"explicit": ((first.axes[0], second.axes[0]),)},
            match={"inferred": rule, "explicit": rule},
        ).compute()
        actual = combined.to_pandas()
        for field in ("baseline.value", "absolute.value", "relative.value"):
            pd.testing.assert_series_equal(
                actual[("value", f"inferred.{field}")],
                actual[("value", f"explicit.{field}")],
                check_names=False,
            )
        pd.testing.assert_series_equal(
            actual[("presence", "inferred.present")],
            actual[("presence", "explicit.present")],
            check_names=False,
        )
        assert actual[("value", "value")].tolist() == [1000]
        assert actual[("value", "inferred.baseline.value")].tolist() == [expected_right]
        assert actual[("value", "inferred.absolute.value")].tolist() == [1000 - expected_right]
        assert actual[("presence", "current.present")].tolist() == [True]
        assert actual[("presence", "inferred.present")].tolist() == [True]
        assert actual[("presence", "explicit.present")].tolist() == [True]
        assert len(actual.attrs["frozen_coordinates"]) == 5
        assert actual.attrs["axes"][0]["role"] == "calendar"
    scalar = session.observe({"value": metric}, by=(current_scope,)).compute()
    with pytest.raises(AnalysisError, match="ordinal"):
        scalar.compare({"reference": scalar}, match={"reference": rule})
    partial = session.observe(
        {"value": metric},
        by=(session.time_scope(start="2026-08-15", end="2026-09-01").by(mv.grain("month")),),
    ).compute()
    empty_partial = partial.where(partial["value"] > 10000).compute()
    for how in ("exact", "outer"):
        with pytest.raises(AnalysisError, match=r"complete|prefix"):
            empty_partial.compare({"reference": right_memory}, match={"reference": rule}).compute()
    state = checked(
        {
            "session": session.id,
            "left": left_saved.artifact.id,
            "right": right_saved.artifact.id,
            "snapshots": [snapshot(left_saved), snapshot(right_saved)],
            "rule": rule_name,
            "right_value": expected_right,
        }
    )
    (case.root / "calendar-rules.json").write_bytes(encode(state))
    shutil.rmtree(case.root / "models")
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    worker = """
import os, sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo import _temporal
from marivo.analysis.core import frame_calendar
from marivo.analysis.materialization import frame_source_preparation
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import obj, read
root=Path(sys.argv[1]); os.chdir(root); os.environ['MARIVO_PROJECT_ROOT']=str(root)
state=obj(read(root/'calendar-rules.json'))
with ExitStack() as guards:
    for target,name,replacement in ((ms,'load',forbidden),(Session,'catalog',property(forbidden)),(SourceSession,'__init__',forbidden),(duckdb,'connect',forbidden),(ibis.duckdb,'connect',forbidden),(frame_source_preparation,'open_query_sources',forbidden),(frame_calendar,'freeze_calendar',forbidden),(_temporal,'ZoneInfo',forbidden)):
        guards.enter_context(patch.object(target,name,replacement))
    session=mv.session.resume(state['session']); before=run_ids(session)
    left=session.persisted_frame(state['left']); right=session.persisted_frame(state['right'])
    assert [snapshot(left),snapshot(right)]==state['snapshots']
    rule=mv.offset(years=-1) if state['rule']=='years' else mv.offset(periods=-1) if state['rule']=='periods' else mv.ordinal()
    output=left.compare({'reference': right},match={'reference': rule}).compute()
    rows=output.to_pandas()
    assert rows[('value','value')].tolist()==[1000]
    assert rows[('value','reference.baseline.value')].tolist()==[state['right_value']]
    assert rows[('value','reference.absolute.value')].tolist()==[1000-state['right_value']]
    assert len(rows.attrs['frozen_coordinates'])==3
    assert run_ids(session)==before
    print(os.getpid())
"""
    completed = subprocess.run(
        [sys.executable, "-c", worker, str(case.root)],
        cwd=PROJECT_ROOT,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="America/Los_Angeles"),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert int(completed.stdout.splitlines()[-1]) != os.getpid()
