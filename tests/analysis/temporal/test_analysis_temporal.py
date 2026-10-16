"""Independent temporal boundaries and graph-product checks for ."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid, coarsening, instant
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import (
    DSL_NAMES,
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
    recovered = case.session.artifact(result.state.artifact_ref)
    assert isinstance(recovered, mv.MaterializedAnalysisDomain)
    assert recovered.to_pandas().equals(frame)


def test_time_product_observation_keeps_empty_cells_and_window_binding(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    scope = mv.time_scope(start="2026-08-01", end="2026-10-01")
    grid = bind_grid(scope, mv.grain("month"), report_timezone="UTC")
    product = members._node.each(grid)
    metric = ms.ref.metric(f"{n.domain}.{n.revenue}")
    from marivo.analysis.materialization.graph_relation import Relation

    for row_window in (True, False):
        observed = product.observe(
            metric,
            metric_contract=product.resolve_metric(metric),
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
        if row_window:
            assert september.loc[september.member != "A", "cell_tag"].eq("null").all()
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


def test_public_grid_observation_and_retained_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-11-01"), grain=mv.grain("month")
    )
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    observed = members.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=grid,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    fixed = observed.execute()
    for current in (observed, fixed):
        totals = current.group_by(grid).rollup().execute().to_pandas()
        assert totals.value.fillna(0).tolist() == [1000, 99, 0]
        assert totals.cell_tag.tolist() == ["defined", "defined", "null"]


def test_public_whole_cell_grain_coarsening(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
    )
    observed = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=grid,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    )
    fixed = observed.execute()
    for current in (observed, fixed):
        result = current.group_by(mv.grain("month")).rollup().execute()
        assert result.to_pandas().value.tolist() == [1000]
        assert result.rollup().execute().to_pandas().value.tolist() == [1000]


def test_identity_observation_has_no_inferred_relationship(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    n = case.names
    observed = case.session.members(ms.ref.entity(f"{n.domain}.{n.order}")).observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        by=(mv.member(),),
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


def _add_temporal_metrics(root: Path) -> None:
    path = root / "models" / "semantic" / "sales" / "models.py"
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


@pytest.mark.slow
def test_grid_produce_and_offline_continuation_are_separate_processes(
    tmp_path: Path,
) -> None:
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "temporal-grid"
    database_path = _prepare_temporal_grid_project(root)
    metric = "running"
    _add_temporal_metrics(root)
    environment = {
        **os.environ,
        "MARIVO_PROJECT_ROOT": str(root),
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    producer = """
import json, sys
import marivo.analysis as mv
import marivo.semantic as ms
ms.load(workspace_dir='.')
session = mv.session.get_or_create('temporal-grid-recovery', report_timezone='UTC')
grid = mv.time_grid(during=mv.time_scope(start='2026-08-01', end='2026-11-01'), grain=mv.grain('month'))
result = session.members(ms.ref.entity('sales.customer')).observe(
    ms.ref.metric('sales.' + sys.argv[1]), via=ms.ref.relationship('sales.order_buyer'),
    by=(mv.member(),),
    **({'at': grid.end} if sys.argv[1] == 'running' else {'during': grid})
).execute()
frame = result.to_pandas()
assert len(frame) == 12
assert frame.value.sum() == (3429 if sys.argv[1] == 'running' else 1099)
print(json.dumps({'session': session.id, 'artifact': result.state.artifact_ref.ref, 'grid': result._node.root.signature.domain.time_grid.identity}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", producer, metric],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    produced = json.loads(completed.stdout.splitlines()[-1])
    database_path.rename(database_path.with_suffix(".offline"))
    (root / "models").rename(root / "models.offline")
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
verified = result._dataset.verified()
primary = verified.primary
keys = [name for name in primary.columns if name.startswith('key_')]
values = (primary.sort_values(keys) if keys else primary)['value'].tolist()
assert values == ([1077,1176,1176] if metric == 'running' else [450,150,400,None] if metric == 'folded' else [1099]), values
state = next(p.table.to_dict(orient='records') for p in verified.parts if p.role == 'original_state')
print(json.dumps({'value': values, 'state': state, 'K': result._dataset.artifact.descriptor.execution_key_digest}))
"""
    recovered = subprocess.run(
        [
            sys.executable,
            "-c",
            continuation,
            produced["session"],
            produced["artifact"],
            produced["grid"],
            metric,
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout.splitlines()[-1])
    assert result["value"] == [1077, 1176, 1176]


def test_grid_required_state_damage_revokes_continuation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import json

    from marivo.analysis.materialization import graph_store
    from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode

    case = analysis_dsl_case_factory("j1")
    _add_temporal_metrics(case.root)
    ms.load(workspace_dir=case.root)
    metric = "revenue"
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales." + metric),
            at=grid.end if metric == "running" else None,
            during=None if metric == "running" else grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .execute()
    )
    artifact_ref = fixed.state.artifact_ref.ref
    store = case.session._runtime.store
    with store._read() as connection:
        record = graph_store.artifact(store, connection, artifact_ref)
    assert record is not None
    part = next(p for p in record.descriptor.parts if p.role == "original_state")
    path = case.root / part.local.project_relative_path / part.local.file_manifest[0].relative_path
    original_bytes = path.read_bytes()
    original_descriptor = encode(record.descriptor, DESCRIPTOR)
    for damage in ("missing", "corrupt", "receipt", "version"):
        path.write_bytes(original_bytes)
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (original_descriptor, artifact_ref),
            )
        if damage == "missing":
            path.unlink()
        elif damage == "corrupt":
            path.write_bytes(b"corrupt grid state")
        else:
            payload = json.loads(original_descriptor)
            if damage == "receipt":
                payload["primary_receipt"]["input_binding"] = "foreign-grid"
            else:
                payload["method_state"]["contract_version"] += 1
            with store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (json.dumps(payload), artifact_ref),
                )
        try:
            with pytest.raises(AnalysisError):
                if damage in ("missing", "receipt"):
                    fixed.contract()
                elif damage == "corrupt":
                    fixed.rollup().execute()
                else:
                    case.session.artifact(fixed.state.artifact_ref)
        finally:
            path.write_bytes(original_bytes)
            with store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (original_descriptor, artifact_ref),
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


def test_cumulative_anchors_ignore_display_start(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    anchors = (
        ("all_time", "None", [1077, 1176], True),
        ("month_to_date", "ms.grain_to_date(grain=mv.grain('month'))", [1000, 99], False),
        ("trailing", "ms.trailing(count=31, unit='day')", [1000, 499], True),
    )
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + "\nimport marivo.analysis as mv\n"
        + "\n".join(
            f"running_{name} = ms.cumulative(name='running_{name}', base=revenue, anchor={anchor})"
            for name, anchor, _, _ in anchors
        )
    )
    ms.load(workspace_dir=case.root)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-15", end="2026-10-01"), grain=mv.grain("month")
    )
    members = case.session.members(ms.ref.entity("sales.customer"))
    fixed_results: list[
        tuple[mv.MaterializedNumericRelation | mv.MaterializedRatioRelation, list[int], bool]
    ] = []
    for name, _, expected, overlap in anchors:
        metric = ms.ref.metric(f"sales.running_{name}")
        logical = members.observe(
            metric,
            at=grid.end,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        fixed = logical.execute()
        overall = members.observe(
            metric,
            at=grid.end,
            via=ms.ref.relationship("sales.order_buyer"),
        )
        assert overall.execute().to_pandas().value.tolist() == expected
        if overlap:
            with pytest.raises(AnalysisError, match="overlap"):
                overall.rollup()
        result = logical.group_by(grid).rollup().execute()
        assert result.to_pandas().value.tolist() == expected
        if overlap:
            with pytest.raises(AnalysisError, match="overlap"):
                result.rollup()
        else:
            assert result.rollup().execute().to_pandas().value.tolist() == [1099]
        scalar = (
            members.observe(
                metric,
                at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .rollup()
            .execute()
        )
        assert scalar.to_pandas().value.tolist() == [expected[-1]]
        if name != "trailing":
            fixed_results.append((fixed, expected, overlap))
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    try:
        for fixed, expected, overlap in fixed_results:
            result = fixed.group_by(grid).rollup().execute()
            assert result.to_pandas().value.tolist() == expected
            if overlap:
                with pytest.raises(AnalysisError, match="overlap"):
                    result.rollup()
            else:
                assert result.rollup().execute().to_pandas().value.tolist() == [1099]
    finally:
        offline.rename(case.database_path)


def test_fold_reaggregates_aligned_samples_before_time(
    analysis_dsl_case_factory: DslCaseFactory,
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
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    definitions = [
        f"""
status_amount_{kind} = ms.measure_column(name='status_amount_{kind}', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold={kind!r}, unit='CNY')
folded_{kind} = ms.aggregate(name='folded_{kind}', measure=status_amount_{kind}, agg='sum')
"""
        for kind in ("first", "last", "mean", "min", "max")
    ]
    path.write_text(path.read_text() + "\n".join(definitions))
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    during = mv.time_scope(start="2026-08-01", end="2026-08-03")
    expected_by_kind = {
        "first": [10, 0],
        "last": [0, 10],
        "mean": [5, 5],
        "min": [0, 0],
        "max": [10, 10],
    }
    fixed_by_kind: dict[str, mv.MaterializedNumericRelation] = {}
    for kind, expected in expected_by_kind.items():
        observed = members.observe(
            ms.ref.metric(f"sales.folded_{kind}"),
            during=during,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        fixed = observed.execute()
        assert fixed.to_pandas().value.tolist() == expected
        fixed_by_kind[kind] = fixed

    observed = members.observe(
        ms.ref.metric("sales.folded_first"),
        during=during,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    overall = members.observe(
        ms.ref.metric("sales.folded_first"),
        during=during,
        via=ms.ref.relationship("sales.order_buyer"),
    )
    assert overall.execute().to_pandas().value.tolist() == [10]
    regional = members.observe(
        ms.ref.metric("sales.folded_first"),
        during=during,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(ms.ref.dimension("sales.customer.region"),),
    )
    assert regional.execute().to_pandas().set_index("group").value.to_dict() == {"east": 10}
    assert observed.rollup().execute().to_pandas().value.tolist() == [10]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert fixed_by_kind["first"].rollup().execute().to_pandas().value.tolist() == [10]


def test_fold_rejects_float64_overflow_before_spatial_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
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
        connection.execute('ALTER TABLE "order" ALTER COLUMN amount TYPE DOUBLE')
        connection.execute('UPDATE "order" SET amount = 1e308')
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(
        path.read_text()
        + """
status_amount = ms.measure_column(name='status_amount', entity=orders, column='amount',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold='first', unit='CNY')
folded = ms.aggregate(name='folded', measure=status_amount, agg='sum')
"""
    )
    ms.load(workspace_dir=case.root)
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.folded"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .execute()
    )
    assert fixed.to_pandas().value.tolist() == [1e308, 1e308]
    with pytest.raises(AnalysisError, match="pre-fold spatial sum exceeds float64"):
        fixed.rollup().execute()


@pytest.mark.parametrize("wall", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_source_wall_clock_gap_and_fold_follow_admitted_conversion(
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
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(
            start=wall[:10], end=(date.fromisoformat(wall[:10]) + timedelta(days=1)).isoformat()
        ),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    result = observed.execute()
    rows = result.to_pandas().set_index("member")
    assert set(rows.index) == {"A", "B", "C", "D"}
    assert rows.loc["A", "value"] == 10
    assert rows.loc["A", "cell_tag"] == "defined"
    assert rows.loc[["B", "C", "D"], "cell_tag"].tolist() == ["null"] * 3


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
    observed = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.running_ratio"),
        at=grid.end,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
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


def test_native_time_conversion_does_not_submit_rule_audit(
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
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    result = observed.execute().to_pandas().set_index("member")
    assert result.loc[["A", "B"], "value"].to_dict() == {"A": 527, "B": 150}
    assert result.loc[["A", "B"], "cell_tag"].tolist() == ["defined"] * 2
    assert result.loc[["C", "D"], "cell_tag"].tolist() == ["null"] * 2
    assert calls and set(calls) == {"UTC"}


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
    _add_temporal_metrics(case.root)
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
    direct = (
        members.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .execute()
        .to_pandas()
    )
    assert direct.loc[direct.member == "A", "value"].tolist() == [10]
    assert grid._bound is None


def test_empty_fold_keeps_typed_state_for_fixed_continuation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.execute("DELETE FROM customer")
    _add_temporal_metrics(case.root)
    ms.load(workspace_dir=case.root)
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.folded"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
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


def test_selected_groups_keep_fold_state_in_source_and_fixed_execution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _add_temporal_metrics(case.root)
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = ms.ref.dimension("sales.customer.region")
    category = members.read(region)
    assert isinstance(category, mv.LogicalCategoryRelation)
    selected = category.where(category.value.eq("east")).members()
    window = mv.time_scope(start="2026-08-01", end="2026-09-01")
    observed = selected.observe(
        ms.ref.metric("sales.folded"),
        during=window,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(region,),
    ).execute()
    expected = observed.to_pandas()
    assert expected.set_index("group").value.dropna().to_dict() == {"east": 450}
    assert expected.set_index("group").cell_reason.to_dict() == {
        "east": None,
    }
    sparse = selected.observe(
        ms.ref.metric("sales.folded"),
        during=window,
        via=ms.ref.relationship("sales.order_buyer"),
        by=(region,),
    ).execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    completed = sparse.group_by(region).rollup().execute()
    assert completed.to_pandas().equals(expected)
    restored = case.session.artifact(observed.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedNumericRelation)
    assert restored.group_by(region).rollup().execute().to_pandas().equals(expected)
