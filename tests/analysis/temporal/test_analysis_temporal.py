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
from marivo._temporal import time_scope
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid, coarsening, instant
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import (
    DSL_NAMES,
    DslCase,
    DslCaseFactory,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    seed_analysis_dsl_database,
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
    scope = time_scope(
        start=date.fromisoformat(day), end=date.fromisoformat(day) + timedelta(days=1)
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


def test_partial_cells_and_crossing_week_rejection() -> None:
    scope = time_scope(start="2026-08-01", end="2026-09-01")
    week = bind_grid(scope, mv.grain("week"), report_timezone="UTC")
    month = bind_grid(scope, mv.grain("month"), report_timezone="UTC")
    assert week.cells[0].original_start.isoformat() == "2026-07-27T00:00:00+00:00"
    assert week.cells[0].partial
    assert week.cells[-1].partial
    with pytest.raises(DatasetConstructionError, match="crosses"):
        coarsening(week, month)
    day = bind_grid(scope, mv.grain("day"), report_timezone="UTC")
    assert len(coarsening(day, month)) == 31


@pytest.mark.parametrize("wall", [datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)])
def test_naive_gap_and_fold_have_no_implicit_resolution(wall: datetime) -> None:
    with pytest.raises(DatasetConstructionError, match="instants"):
        instant(wall, "America/New_York")


def test_grid_wire_preserves_authority_and_rejects_changed_identity() -> None:
    grid = bind_grid(
        time_scope(start="2026-03-08", end="2026-03-09"),
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

    calendar = ms.ref.period_calendar("sales.fiscal")
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
    scope = time_scope(
        start=datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, 16, tzinfo=timezone.utc),
    )
    grid = bind_grid(scope, grain, report_timezone="America/New_York", snapshot=snapshot)
    assert grid.snapshot_digest == snapshot.snapshot_digest
    assert grid.report_timezone == "America/New_York"
    assert grid.boundary_timezone == "Asia/Shanghai"
    assert [(c.start.isoformat(), c.end.isoformat()) for c in grid.cells] == [
        ("2026-07-31T16:00:00+00:00", "2026-08-02T16:00:00+00:00"),
        ("2026-08-02T16:00:00+00:00", "2026-08-03T16:00:00+00:00"),
    ]
    with pytest.raises(AnalysisError, match="conflicts"):
        bind_grid(scope, grain, report_timezone="UTC", explicit_timezone="UTC", snapshot=snapshot)
    with pytest.raises(AnalysisError, match="coverage"):
        bind_grid(
            time_scope(start="2026-07-30", end="2026-08-04"),
            grain,
            report_timezone="UTC",
            snapshot=snapshot,
        )


def test_time_product_codec_round_trips_closed_parameters() -> None:
    from marivo.analysis.core.model import Binding, DomainSignature
    from marivo.analysis.core.rules import RuleParameters, TimeProduct

    domain = DomainSignature(
        Binding("session", "owner", "input", "scope"), "singleton", (), (), "all"
    )
    adapter: TypeAdapter[RuleParameters] = TypeAdapter(RuleParameters)
    for value in (TimeProduct(domain, "time_product"),):
        recovered = adapter.validate_json(adapter.dump_json(value), strict=True)
        assert type(recovered) is type(value)
        assert recovered == value


def test_trailing_day_uses_seconds_across_civil_dst_day() -> None:
    from marivo.analysis.core.time_grid import GridPoint, bind_cumulative

    grid = bind_grid(
        time_scope(start="2026-03-08", end="2026-03-09"),
        mv.grain("day"),
        report_timezone="America/New_York",
    )
    binding = bind_cumulative(("trailing", 1, "day"), GridPoint(grid, "end"), "America/New_York")
    assert binding.windows[0].start == "2026-03-08T04:00:00+00:00"
    assert binding.windows[0].end == "2026-03-09T04:00:00+00:00"
    assert grid.cells[0].start.isoformat() == "2026-03-08T05:00:00+00:00"


def test_sample_state_rejects_duplicate_keys_and_nonfinite_components() -> None:
    from marivo.analysis.methods.temporal_fold import decode_samples

    for payload in (
        "2026-08-01T00:00:00~10~1;2026-08-01T00:00:00~20~1",
        "2026-08-01T00:00:00~inf~1",
        "2026-08-01T00:00:00~10~0",
    ):
        with pytest.raises(ValueError):
            decode_samples(payload)


def test_named_occurrences_with_equal_bounds_are_not_one_partition() -> None:
    from marivo._temporal import certify_temporal_set

    snapshot = certify_temporal_set(
        temporal_set_ref=ms.ref.temporal_set("sales.promotions"),
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
    with pytest.raises(AnalysisError, match="occurrences"):
        coarsening(first, second)


def test_single_second_grid_has_bounded_construction_cost() -> None:
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from datetime import datetime, timedelta, timezone
import marivo.analysis as mv
from marivo._temporal import time_scope
from marivo.analysis.core.time_grid import bind_grid
start = datetime(2026, 8, 1, tzinfo=timezone.utc)
grid = bind_grid(time_scope(start=start, end=start + timedelta(seconds=1)),
                 mv.grain('second'), report_timezone='UTC')
assert len(grid.cells) == 1
assert grid.cells[0].start == start
assert grid.cells[0].end == start + timedelta(seconds=1)
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_repeated_hour_minute_cells_keep_both_instant_sequences() -> None:
    grid = bind_grid(
        time_scope(
            start=datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc),
            end=datetime(2026, 11, 1, 6, 1, tzinfo=timezone.utc),
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
) -> mv.Query:
    session = case.session
    during = session.time_scope(start=start, end=end)
    return session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=during.by(mv.grain(grain)) if grain is not None else during,
        by=session.members(ms.ref.entity("sales.customer")),
    )


def test_member_time_product_executes_on_unified_query(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    members = session.members(ms.ref.entity("sales.customer"))
    domain = mv.product(calendar, members)
    result = session.query(domain).execute()
    frame = result.to_pandas()
    assert len(frame) == 8
    assert (
        frame.iloc[:, 0].tolist()
        == [datetime(2026, 8, 1, tzinfo=timezone.utc)] * 4
        + [datetime(2026, 8, 2, tzinfo=timezone.utc)] * 4
    )
    assert frame.iloc[:, 1].tolist() == ["A", "B", "C", "D"] * 2
    pd.testing.assert_frame_equal(session.artifact(result.id).to_pandas(), frame)


def test_time_product_observation_keeps_empty_cells_and_window_binding(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    members = session.members(ms.ref.entity("sales.customer"))
    query = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        by=mv.product(calendar, members),
        during=calendar,
    )
    frame = query.execute().to_pandas()
    assert len(frame) == 8
    assert frame[("value", "value")].tolist() == [450, 150, 400, 0, 99, 0, 0, 0]
    assert query.rollup().execute().to_pandas()[("value", "value")].tolist() == [1099]
    saved = query.execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    exported = saved.to_pandas()
    # Explicit pandas composition consumes the terminal data, including every key.
    assert exported[("value", "value")].sum() == 1099
    pd.testing.assert_frame_equal(session.artifact(saved.id).to_pandas(), frame)


def test_public_grid_observation_and_retained_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    query = _observation(case, start="2026-08-01", end="2026-11-01", grain="month")
    calendar = case.session.time_scope(start="2026-08-01", end="2026-11-01").by(mv.grain("month"))
    totals = query.rollup(by=(calendar,)).execute().to_pandas()
    assert totals[("value", "value")].tolist() == [1000, 99, 0]
    assert totals.attrs["marivo"]["axes"][0]["timezone"] == "UTC"


def test_public_whole_cell_grain_coarsening(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    query = _observation(case, start="2026-08-01", end="2026-09-01", grain="day")
    month = case.session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("month"))
    coarsened = query.rollup(by=(month,))
    assert coarsened.execute().to_pandas()[("value", "value")].tolist() == [1000]
    assert coarsened.rollup().execute().to_pandas()[("value", "value")].tolist() == [1000]


def test_identity_observation_has_no_inferred_relationship(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    observed = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=session.members(ms.ref.entity("sales.order")),
    )
    assert observed.rollup().execute().to_pandas()[("value", "value")].tolist() == [1000]


@pytest.mark.slow
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
session = mv.session.get_or_create('temporal-grid-recovery', report_timezone='UTC')
calendar = session.time_scope(start='2026-08-01', end='2026-11-01').by(mv.grain('month'))
metric = session.catalog.metrics.get('sales.revenue').window(mv.all_history())
query = session.observe({'value':metric}, during=calendar, by=session.members(ms.ref.entity('sales.customer')))
result = query.rollup(by=(calendar,)).execute()
assert result.to_pandas()[('value','value')].tolist() == [1077,1176,1176]
print(json.dumps({'session':session.id,'artifact':result.id}))
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
result = mv.session.resume(sys.argv[1], by='id').artifact(sys.argv[2])
frame = result.to_pandas()
assert frame[('value','value')].tolist() == [1077,1176,1176]
assert frame.attrs['marivo']['axes'][0]['timezone'] == 'UTC'
assert len(frame.attrs['marivo']['axes'][0]['bounds']) == 3
print(json.dumps({'value':frame[('value','value')].tolist()}))
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
    assert json.loads(recovered.stdout.splitlines()[-1])["value"] == [1077, 1176, 1176]


def test_grid_saved_data_damage_rejects_complete_reads(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    result = _observation(case, start="2026-08-01", end="2026-10-01", grain="month").execute()
    data = result._manifest.data
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
                    case.session.artifact(result.id).to_pandas()
            finally:
                path.write_bytes(original)
    pd.testing.assert_frame_equal(case.session.artifact(result.id).to_pandas(), result.to_pandas())


def test_cumulative_anchors_ignore_display_start(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-15", end="2026-10-01").by(mv.grain("month"))
    members = session.members(ms.ref.entity("sales.customer"))
    rules = (
        (mv.all_history(), [1077, 1176]),
        (mv.to_date(grain=mv.grain("month")), [1000, 99]),
        (mv.trailing_duration(duration=timedelta(days=31)), [1000, 499]),
    )
    saved = []
    for rule, expected in rules:
        metric = session.catalog.metrics.get("sales.revenue").window(rule)
        query = session.observe({"value": metric}, during=calendar, by=members)
        result = query.rollup(by=(calendar,)).execute()
        assert result.to_pandas()[("value", "value")].tolist() == expected
        # Replacing/removing the window calendar would change the source window.
        with pytest.raises(AnalysisError, match="window"):
            query.rollup()
        scalar = session.observe(
            {"value": metric},
            during=session.time_scope(start="2026-09-30", end="2026-10-01"),
            by=members,
        ).rollup()
        assert scalar.execute().to_pandas()[("value", "value")].tolist() == [expected[-1]]
        saved.append((result, expected))
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    for result, expected in saved:
        assert session.artifact(result.id).to_pandas()[("value", "value")].tolist() == expected


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
    revenue = session.catalog.metrics.get("sales.revenue")
    members = session.members(ms.ref.entity("sales.customer"))
    expected = {"first": [10, 0], "last": [0, 10], "mean": [5, 5], "min": [0, 0], "max": [10, 10]}
    for kind, values in expected.items():
        samples = (
            session.catalog.fields.get("sales.order.ordered_at")
            if kind in ("first", "last")
            else mv.product(mv.coordinates(by=(), keys=((),)), calendar)
        )
        metric = getattr(revenue.per(samples), kind)()
        observed = session.observe({"value": metric}, during=calendar.scope, by=members)
        assert observed.execute().to_pandas()[("value", "value")].tolist() == values
        assert observed.rollup().execute().to_pandas()[("value", "value")].tolist() == [10]
    region = session.catalog.fields.get("sales.customer.region")
    regional = session.observe(
        {"value": revenue.per(session.catalog.fields.get("sales.order.ordered_at")).first()},
        during=calendar.scope,
        by=members.classify(by=(region,)),
    )
    frame = regional.execute().to_pandas()
    assert frame.iloc[:, 0].tolist() == ["east"]
    assert frame[("value", "value")].tolist() == [10]


def test_fold_rejects_float64_overflow_before_spatial_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _seed_two_sample_amounts(case, overflow=True)
    session = case.session
    calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    metric = (
        session.catalog.metrics.get("sales.revenue")
        .per(session.catalog.fields.get("sales.order.ordered_at"))
        .first()
    )
    query = session.observe(
        {"value": metric},
        during=calendar.scope,
        by=session.members(ms.ref.entity("sales.customer")),
    )
    assert query.execute().to_pandas()[("value", "value")].tolist() == [1e308, 1e308]
    with pytest.raises(AnalysisError) as failure:
        query.rollup().execute()
    from marivo.semantic.errors import SemanticExpressionError

    cause = failure.value.__cause__
    assert isinstance(cause, SemanticExpressionError)
    assert cause.expected == "finite values in the admitted numeric type"
    assert "internal sample reduction overflow" in (cause.received or "")
    assert failure.value.run_ref is not None
    assert session.get_run(failure.value.run_ref).lifecycle == "failed"


@pytest.mark.parametrize("wall", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_source_wall_clock_gap_and_fold_follow_admitted_conversion(
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
            "parse=ms.timestamp(timezone='UTC')", "parse=ms.timestamp(timezone='America/New_York')"
        )
    )
    ms.load(workspace_dir=case.root)
    query = _observation(
        case, start=wall[:10], end=(date.fromisoformat(wall[:10]) + timedelta(days=1)).isoformat()
    )
    frame = query.execute().to_pandas()
    assert frame.iloc[:, 0].tolist() == ["A", "B", "C", "D"]
    assert frame[("value", "value")].tolist() == [10, 0, 0, 0]


def test_cumulative_ratio_binds_every_component_to_the_endpoint(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    calendar = session.time_scope(start="2026-08-15", end="2026-10-01").by(mv.grain("month"))
    ratio = session.catalog.metrics.get("sales.revenue") / session.catalog.metrics.get(
        "sales.order_count"
    )
    query = session.observe(
        {"value": ratio.window(mv.all_history())},
        during=calendar,
        by=session.members(ms.ref.entity("sales.customer")),
    )
    result = query.rollup(by=(calendar,)).execute()
    assert result.to_pandas()[("value", "value")].tolist() == [269.25, 235.2]
    pd.testing.assert_frame_equal(session.artifact(result.id).to_pandas(), result.to_pandas())


def test_native_time_conversion_does_not_submit_rule_audit(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ibis
    import ibis.expr.types as ir

    from marivo.analysis.compiler import source_time

    case = analysis_dsl_case_factory("j1")
    original = source_time.render
    calls = []

    def disagree(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
        calls.append(zone)
        return original(zone, value) + ibis.interval(hours=1)

    monkeypatch.setattr(source_time, "render", disagree)
    result = _observation(case, start="2026-08-01", end="2026-09-01").execute().to_pandas()
    assert result[("value", "value")].tolist() == [527, 150, 0, 0]
    assert calls and set(calls) == {"UTC"}


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
    path.write_text(path.read_text().replace("parse=ms.timestamp(timezone='UTC')", "parse=None"))
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("date-window", report_timezone="Asia/Tokyo")
    grid = session.time_scope(start="2026-08-01", end="2026-08-03", timezone="America/New_York").by(
        mv.grain("day")
    )
    query = session.observe(
        {"value": session.catalog.metrics.get("sales.revenue")},
        during=grid,
        by=session.members(ms.ref.entity("sales.customer")),
    )
    frame = query.rollup(by=(grid,)).execute().to_pandas()
    assert frame[("value", "value")].tolist() == [10, 20]
    assert frame.attrs["marivo"]["axes"][0]["timezone"] == "America/New_York"


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
        by=session.members(ms.ref.entity("sales.customer")),
    )
    result = query.execute()
    assert result.to_pandas().empty
    total = query.rollup().execute().to_pandas()
    assert total[("value", "value")].tolist() == [0]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    pd.testing.assert_frame_equal(session.artifact(result.id).to_pandas(), result.to_pandas())


def test_selected_groups_keep_sample_selection_before_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    region = session.catalog.fields.get("sales.customer.region")
    category = session.read(members, {"region": region})
    selected = category.where(category["region"] == "east").population()
    calendar = session.time_scope(start="2026-08-01", end="2026-09-01").by(mv.grain("day"))
    query = session.observe(
        {
            "value": session.catalog.metrics.get("sales.revenue")
            .per(mv.product(mv.coordinates(by=(), keys=((),)), calendar))
            .max()
        },
        by=selected.classify(by=(region,)),
        members=selected,
    )
    result = query.execute()
    frame = result.to_pandas()
    assert frame.iloc[:, 0].tolist() == ["east"]
    assert frame[("value", "value")].tolist() == [450]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    pd.testing.assert_frame_equal(session.artifact(result.id).to_pandas(), frame)
