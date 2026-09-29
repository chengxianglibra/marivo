"""Independent temporal boundaries and graph-product checks for R5.5."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid, coarsening, instant
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCase, DslCaseFactory


@pytest.mark.parametrize(
    ("day", "hours", "start", "end"),
    [
        ("2026-03-08", 23, "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
        ("2026-11-01", 25, "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
    ],
)
def test_civil_day_retains_actual_dst_instants(day: str, hours: int, start: str, end: str) -> None:
    scope = mv.time_scope(
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
    scope = mv.time_scope(start="2026-08-01", end="2026-09-01")
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
        mv.time_scope(start="2026-03-08", end="2026-03-09"),
        mv.grain("day"),
        report_timezone="America/New_York",
    )
    adapter = TypeAdapter(BoundTimeGrid)
    assert adapter.validate_json(adapter.dump_json(grid), strict=True) == grid
    with pytest.raises(DatasetConstructionError, match="identity"):
        replace(grid, boundary_timezone="UTC")


def test_aware_endpoint_remains_instant() -> None:
    stamp = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert instant(stamp, "America/New_York") == stamp


@pytest.mark.runtime
def test_member_time_product_executes_on_unified_graph(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    grid = bind_grid(
        mv.time_scope(start="2026-08-01", end="2026-08-03"), mv.grain("day"), report_timezone="UTC"
    )
    product = members._node.each(grid)
    result = product.execute()
    frame = result.to_pandas()
    assert len(frame) == 8
    assert set(frame["coord_0"]) == {"2026-08-01T00:00:00+00:00", "2026-08-02T00:00:00+00:00"}
    assert frame.groupby("member").size().tolist() == [2, 2, 2, 2]
    assert result.artifact.descriptor.definition_fingerprint == product.root.fingerprint


@pytest.mark.runtime
@pytest.mark.parametrize("row_window", [True, False])
def test_time_product_observation_keeps_empty_cells_and_window_binding(
    analysis_dsl_case_factory: DslCaseFactory, row_window: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    scope = mv.time_scope(start="2026-08-01", end="2026-10-01")
    grid = bind_grid(scope, mv.grain("month"), report_timezone="UTC")
    product = members._node.each(grid)
    observed = product.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=grid if row_window else scope,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
    )
    result = observed.execute()
    frame = result.to_pandas()
    assert len(frame) == 8
    august = frame[frame["coord_0"] == "2026-08-01T00:00:00+00:00"]
    september = frame[frame["coord_0"] == "2026-09-01T00:00:00+00:00"]
    assert august.value.sum() == (1000 if row_window else 1099)
    assert september.value.sum() == (99 if row_window else 1099)
    assert (
        september.loc[september.member != "A", "cell_tag"].eq("null").all() if row_window else True
    )
    from marivo.analysis.materialization.graph_relation import Relation

    fixed = Relation.restore(result)
    if row_window:
        offline = case.database_path.with_suffix(".offline")
        case.database_path.rename(offline)
        try:
            assert fixed.rollup().execute().to_pandas().value.tolist() == [1099]
        finally:
            offline.rename(case.database_path)
        assert observed.rollup().execute().to_pandas().value.tolist() == [1099]
    else:
        with pytest.raises(AnalysisError, match="repeats"):
            fixed.rollup()
        with pytest.raises(AnalysisError, match="repeats"):
            observed.rollup()


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_public_grid_observation_and_retained_axis(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-11-01"), grain=mv.grain("month")
    )
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    product = members.each(grid)
    assert isinstance(product.execute(), mv.MaterializedTimeAnalysisDomain)
    observed = product.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=grid.window,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
    )
    current = observed.execute() if fixed else observed
    totals = current.group_by(grid).rollup().execute().to_pandas()
    assert totals.value.fillna(0).tolist() == [1000, 99, 0]
    assert totals.cell_tag.tolist() == ["defined", "defined", "null"]


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_public_whole_cell_grain_coarsening(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    observed = (
        case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
        .each(grid)
        .observe(
            ms.ref.metric(f"{n.domain}.{n.revenue}"),
            during=grid.window,
            via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        )
    )
    current = observed.execute() if fixed else observed
    result = current.group_by(mv.grain("month")).rollup().execute()
    assert result.to_pandas().value.tolist() == [1000]
    assert result.rollup().execute().to_pandas().value.tolist() == [1000]


@pytest.mark.runtime
def test_identity_observation_has_no_inferred_relationship(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    observed = case.session.members(ms.ref.entity(f"{n.domain}.{n.order}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
    )
    assert observed.rollup().execute().to_pandas().value.tolist() == [1000]


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
    scope = mv.time_scope(
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
            mv.time_scope(start="2026-07-30", end="2026-08-04"),
            grain,
            report_timezone="UTC",
            snapshot=snapshot,
        )


@pytest.mark.runtime
def test_grid_handle_rejects_other_product(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    other = mv.time_grid(
        during=mv.time_scope(start="2026-09-01", end="2026-10-01"), grain=mv.grain("day")
    )
    members = case.session.members(ms.ref.entity("sales.customer"))
    receiver = members.each(grid)
    members.each(other)
    with pytest.raises(AnalysisError, match="foreign"):
        receiver.observe(ms.ref.metric("sales.revenue"), during=other.window)


def _add_temporal_metrics(case: DslCase) -> None:
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + """
running = ms.cumulative(name='running', base=revenue)
status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold='max', unit='CNY')
folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')
"""
    )


@pytest.mark.runtime
@pytest.mark.parametrize("storage", ["table", "parquet"])
@pytest.mark.parametrize("metric", ["revenue", "running", "folded"])
def test_grid_produce_offline_continuation_and_cold_recovery_are_separate_processes(
    analysis_dsl_case_factory: DslCaseFactory, storage: str, metric: str
) -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tests.shared_fixtures import export_dsl_parquet_models

    case = analysis_dsl_case_factory("j1")
    _add_temporal_metrics(case)
    if storage == "parquet":
        export_dsl_parquet_models(case, case.root)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    producer = """
import json, sys
import marivo.analysis as mv
import marivo.semantic as ms
ms.load(workspace_dir='.')
session = mv.session.resume(sys.argv[1], by='id')
grid = mv.time_grid(during=mv.time_scope(start='2026-08-01', end='2026-11-01'), grain=mv.grain('month'))
result = session.members(ms.ref.entity('sales.customer')).each(grid).observe(
    ms.ref.metric('sales.' + sys.argv[2]), via=ms.ref.relationship('sales.order_buyer'),
    **({'at': grid.end} if sys.argv[2] == 'running' else {'during': grid.window})
).execute()
assert len(result.to_pandas()) == 12
assert result.to_pandas().value.sum() == (3429 if sys.argv[2] == 'running' else 1099)
print(json.dumps({'artifact': result.state.artifact_ref.ref, 'grid': result._node.root.signature.domain.time_grid.identity}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", producer, case.session.id, metric],
        cwd=case.root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    produced = json.loads(completed.stdout.splitlines()[-1])
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    if storage == "parquet":
        (case.root / "source_files").rename(case.root / "source_files.offline")
    continuation = """
import json, sys
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('fixed time continuation touched source, models or DuckDB')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
DatasourceConnectionService.use_backend = forbidden
fixed = mv.session.resume(sys.argv[1], by='id').artifact(sys.argv[2])
assert fixed._node.root.signature.domain.time_grid.identity == sys.argv[3]
metric = sys.argv[4]
result = (fixed.group_by(mv.grain('month')).rollup() if metric == 'running' else
          fixed.group_by(ms.ref.entity('sales.customer')).rollup() if metric == 'folded' else
          fixed.rollup()).execute()
values = result._dataset.verified().primary['value'].to_pylist()
assert values == ([1077,1176,1176] if metric == 'running' else [450,150,400,None] if metric == 'folded' else [1099])
state = next(p.table.to_pylist() for p in result._dataset.verified().parts if p.role == 'original_state')
print(json.dumps({'value': values, 'state': state, 'K': result._dataset.artifact.descriptor.execution_key_digest}))
"""
    results = []
    for _ in range(2):
        recovered = subprocess.run(
            [
                sys.executable,
                "-c",
                continuation,
                case.session.id,
                produced["artifact"],
                produced["grid"],
                metric,
            ],
            cwd=case.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert recovered.returncode == 0, recovered.stderr
        results.append(json.loads(recovered.stdout.splitlines()[-1]))
    assert results[0] == results[1]
    if metric == "revenue":
        assert results[0]["state"][0]["original_state__sum"] == 1099


@pytest.mark.runtime
@pytest.mark.parametrize("damage", ["missing", "corrupt", "receipt", "version"])
@pytest.mark.parametrize("metric", ["revenue", "running", "folded"])
def test_grid_required_state_damage_revokes_continuation(
    analysis_dsl_case_factory: DslCaseFactory, damage: str, metric: str
) -> None:
    from marivo.analysis.materialization import graph_store

    case = analysis_dsl_case_factory("j1")
    _add_temporal_metrics(case)
    ms.load(workspace_dir=case.root)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .each(grid)
        .observe(
            ms.ref.metric("sales." + metric),
            at=grid.end if metric == "running" else None,
            during=None if metric == "running" else grid.window,
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .execute()
    )
    store = case.session._runtime.store
    with store._read() as connection:
        record = graph_store.artifact(store, connection, fixed.state.artifact_ref.ref)
    assert record is not None
    part = next(p for p in record.descriptor.parts if p.role == "original_state")
    path = case.root / part.local.project_relative_path / part.local.file_manifest[0].relative_path
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"corrupt grid state")
    else:
        import json

        from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode

        payload = json.loads(encode(record.descriptor, DESCRIPTOR))
        if damage == "receipt":
            payload["primary_receipt"]["input_binding"] = "foreign-grid"
        else:
            payload["method_state"]["contract_version"] += 1
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(payload), fixed.state.artifact_ref.ref),
            )
    with pytest.raises(AnalysisError):
        fixed.contract()
    with pytest.raises(AnalysisError):
        fixed.rollup().execute()
    with pytest.raises(AnalysisError):
        case.session.artifact(fixed.state.artifact_ref)


def test_time_product_codec_does_not_capture_complete_group_parameters() -> None:
    from marivo.analysis.core.model import Binding, DomainSignature
    from marivo.analysis.core.rules import CompleteGroups, RuleParameters, TimeProduct

    domain = DomainSignature(
        Binding("session", "owner", "input", "scope"), "singleton", (), (), "all"
    )
    adapter = TypeAdapter(RuleParameters)
    for value in (CompleteGroups(domain), TimeProduct(domain, "time_product")):
        recovered = adapter.validate_json(adapter.dump_json(value), strict=True)
        assert type(recovered) is type(value)
        assert recovered == value


@pytest.mark.runtime
@pytest.mark.parametrize(
    ("anchor", "expected", "overlap"),
    [
        ("None", [1077, 1176], True),
        ("ms.grain_to_date(grain=mv.grain('month'))", [1000, 99], False),
        ("ms.trailing(count=31, unit='day')", [1000, 499], True),
    ],
)
def test_cumulative_anchors_ignore_display_start(
    analysis_dsl_case_factory: DslCaseFactory, anchor: str, expected: list[int], overlap: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + "\nimport marivo.analysis as mv\n"
        + f"\nrunning = ms.cumulative(name='running', base=revenue, anchor={anchor})\n"
    )
    ms.load(workspace_dir=case.root)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-15", end="2026-10-01"), grain=mv.grain("month")
    )
    logical = (
        case.session.members(ms.ref.entity("sales.customer"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.running"),
            at=grid.end,
            via=ms.ref.relationship("sales.order_buyer"),
        )
    )
    fixed = logical.execute()
    for current in (logical, fixed):
        result = current.group_by(grid).rollup().execute()
        assert result.to_pandas().value.tolist() == expected
        if overlap:
            with pytest.raises(AnalysisError, match="overlap"):
                result.rollup()
        else:
            assert result.rollup().execute().to_pandas().value.tolist() == [1099]
    scalar = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.running"),
            at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .rollup()
        .execute()
    )
    assert scalar.to_pandas().value.tolist() == [expected[-1]]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert fixed.group_by(grid).rollup().execute().to_pandas().value.tolist() == expected


@pytest.mark.runtime
@pytest.mark.parametrize(
    ("kind", "overflow"),
    [(kind, False) for kind in ("first", "last", "mean", "min", "max")] + [("first", True)],
)
def test_fold_reaggregates_aligned_samples_before_time(
    analysis_dsl_case_factory: DslCaseFactory, kind: str, overflow: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
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
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + f"""
status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold={kind!r}, unit='CNY')
folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')
"""
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    observed = members.observe(
        ms.ref.metric("sales.folded"),
        during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    fixed = observed.execute()
    if overflow:
        assert fixed.to_pandas().value.tolist() == [1e308, 1e308]
        with pytest.raises(AnalysisError, match="pre-fold spatial sum exceeds float64"):
            fixed.rollup().execute()
        return
    expected = {"first": [10, 0], "last": [0, 10], "mean": [5, 5], "min": [0, 0], "max": [10, 10]}[
        kind
    ]
    assert fixed.to_pandas().value.tolist() == expected
    assert observed.rollup().execute().to_pandas().value.tolist() == [10]
    assert fixed.rollup().execute().to_pandas().value.tolist() == [10]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert fixed.rollup().execute().to_pandas().value.tolist() == [10]


@pytest.mark.runtime
@pytest.mark.parametrize("wall", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_source_wall_clock_gap_and_fold_reject_before_publication(
    analysis_dsl_case_factory: DslCaseFactory, wall: str
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute('ALTER TABLE "order" ALTER ordered_at TYPE TIMESTAMP')
        connection.execute(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)', ["x", "A", "web", "paid", wall, 10]
        )
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text().replace(
            "parse=ms.timestamp(timezone='UTC')", "parse=ms.timestamp(timezone='America/New_York')"
        )
    )
    ms.load(workspace_dir=case.root)
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"), via=ms.ref.relationship("sales.order_buyer")
    )
    with pytest.raises(AnalysisError, match="instants"):
        observed.execute()


@pytest.mark.runtime
@pytest.mark.parametrize("civil_date", [False, True])
def test_source_report_and_grid_timezones_are_independent(
    analysis_dsl_case_factory: DslCaseFactory, civil_date: bool
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute(
            'ALTER TABLE "order" ALTER ordered_at TYPE ' + ("DATE" if civil_date else "TIMESTAMP")
        )
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                (
                    "a",
                    "A",
                    "web",
                    "paid",
                    "2026-08-01" if civil_date else "2026-08-01 08:00:00",
                    10,
                ),
                (
                    "b",
                    "A",
                    "web",
                    "paid",
                    "2026-08-02" if civil_date else "2026-08-01 20:00:00",
                    20,
                ),
            ],
        )
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text().replace(
            "parse=ms.timestamp(timezone='UTC')",
            "parse=None" if civil_date else "parse=ms.timestamp(timezone='America/New_York')",
        )
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create(
        name="three-zones",
        question="Independent temporal authority",
        report_timezone="America/New_York",
    )
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-03" if civil_date else "2026-08-02"),
        grain=mv.grain("day"),
        timezone="America/New_York" if civil_date else "Asia/Tokyo",
    )
    result = (
        session.members(ms.ref.entity("sales.customer"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.revenue"),
            during=grid.window,
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .group_by(grid)
        .rollup()
        .execute()
    )
    assert result.to_pandas().value.tolist() == [10, 20]


def test_trailing_day_uses_seconds_across_civil_dst_day() -> None:
    from marivo.analysis.core.time_grid import GridPoint, bind_cumulative

    grid = bind_grid(
        mv.time_scope(start="2026-03-08", end="2026-03-09"),
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


@pytest.mark.runtime
def test_cumulative_ratio_binds_every_component_to_the_endpoint(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + """
running_revenue = ms.cumulative(name='running_revenue', base=revenue)
running_count = ms.cumulative(name='running_count', base=order_count)
running_ratio = ms.ratio(name='running_ratio', numerator=running_revenue, denominator=running_count,
                        zero_denominator=ms.zero_denominator.undefined())
"""
    )
    ms.load(workspace_dir=case.root)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-15", end="2026-10-01"), grain=mv.grain("month")
    )
    observed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .each(grid)
        .observe(
            ms.ref.metric("sales.running_ratio"),
            at=grid.end,
            via=ms.ref.relationship("sales.order_buyer"),
        )
    )
    assert observed.group_by(grid).rollup().execute().to_pandas().value.tolist() == [269.25, 235.2]
    fixed = observed.execute()
    assert fixed.group_by(grid).rollup().execute().to_pandas().value.tolist() == [269.25, 235.2]


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


@pytest.mark.runtime
def test_engine_timezone_disagreement_rejects_without_route_retry(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis
    import ibis.expr.types as ir

    from marivo.analysis.compiler import source_time

    case = analysis_dsl_case_factory("j1")
    original = source_time.render
    calls: list[str] = []

    def disagree(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
        calls.append(zone)
        return original(zone, value) + ibis.interval(hours=1)

    monkeypatch.setattr(source_time, "render", disagree)
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"), via=ms.ref.relationship("sales.order_buyer")
    )
    with pytest.raises(AnalysisError, match="timezone rules differ"):
        observed.execute()
    assert calls == ["UTC"]


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
from marivo.analysis.core.time_grid import bind_grid
start = datetime(2026, 8, 1, tzinfo=timezone.utc)
grid = bind_grid(mv.time_scope(start=start, end=start + timedelta(seconds=1)),
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
        mv.time_scope(
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


@pytest.mark.runtime
@pytest.mark.parametrize("metric", ["revenue", "running"])
def test_fixed_date_windows_keep_authority_on_a_foreign_zone_grid(
    analysis_dsl_case_factory: DslCaseFactory, metric: str
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
    _add_temporal_metrics(case)
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(path.read_text().replace("parse=ms.timestamp(timezone='UTC')", "parse=None"))
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create(
        name="fixed-date-window",
        question="Independent window authority",
        report_timezone="Asia/Tokyo",
    )
    members = session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
        grain=mv.grain("day"),
        timezone="America/New_York",
    )
    window = mv.time_scope(start="2026-08-01", end="2026-08-02") if metric == "revenue" else None
    endpoint = datetime(2026, 8, 1, 15, tzinfo=timezone.utc) if metric == "running" else None
    direct = (
        members.observe(
            ms.ref.metric(f"sales.{metric}"),
            during=window,
            at=endpoint,
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .execute()
        .to_pandas()
    )
    product = (
        members.each(grid)
        .observe(
            ms.ref.metric(f"sales.{metric}"),
            during=window,
            at=endpoint,
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .execute()
        .to_pandas()
    )
    assert direct.loc[direct.member == "A", "value"].tolist() == [10]
    assert product.loc[product.member == "A", "value"].tolist() == [10, 10, 10]


@pytest.mark.runtime
def test_empty_fold_keeps_typed_state_for_fixed_continuation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer")
    _add_temporal_metrics(case)
    ms.load(workspace_dir=case.root)
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.folded"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .execute()
    )
    assert fixed.to_pandas().empty
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    reduced = fixed.group_by(ms.ref.entity("sales.customer")).rollup().execute()
    assert reduced.to_pandas().empty
    assert reduced.group_by(ms.ref.entity("sales.customer")).rollup().execute().to_pandas().empty
    total = reduced.rollup().execute().to_pandas()
    assert total.cell_tag.tolist() == ["null"]
    assert total.cell_reason.tolist() == ["empty_contribution"]
