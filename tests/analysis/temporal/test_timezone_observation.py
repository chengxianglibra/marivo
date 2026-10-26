"""Independent oracles for source authority, report windows and mean continuations."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo._temporal import CalendarAxis, TimeScope
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs
from tests.support.paths import PROJECT_ROOT

_ROWS = (
    ("before", "A", 888, "2026-07-31 15:59:59.999999"),
    ("one", "A", 10, "2026-07-31 16:00:00"),
    ("two", "A", 30, "2026-08-01 15:59:59.999999"),
    ("end", "A", 900, "2026-08-01 16:00:00"),
    ("b", "B", 60, "2026-08-01 08:00:00"),
    ("next_a1", "A", 40, "2026-08-05 12:00:00"),
    ("next_a2", "A", 60, "2026-08-06 12:00:00"),
    ("next_b", "B", 120, "2026-08-05 12:00:00"),
)


def _project(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    backend: Literal["duckdb",] = "duckdb",
    form: Literal["table", "parquet"] = "table",
    representation: Literal["naive", "aware", "date"] = "naive",
    read_zone: str | None = "UTC",
    rows: tuple[tuple[str, str, int, str], ...] = _ROWS,
) -> Path:
    monkeypatch.chdir(root)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
    database = root / "warehouse.duckdb"
    physical = {"naive": "TIMESTAMP", "aware": "TIMESTAMPTZ", "date": "DATE"}[representation]
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET TimeZone='UTC'")
        connection.execute("CREATE TABLE customers (id VARCHAR)")
        connection.execute("INSERT INTO customers VALUES ('A'), ('B')")
        connection.execute(
            f"CREATE TABLE orders (id VARCHAR, customer VARCHAR, amount BIGINT, happened {physical})"
        )
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", rows)
        if form == "parquet":
            for table in ("customers", "orders"):
                pq.write_table(
                    connection.execute(f"SELECT * FROM {table}").to_arrow_table(),
                    root / f"{table}.parquet",
                )
    customer_source = (
        f"md.parquet({str(root / 'customers.parquet')!r})"
        if form == "parquet"
        else "md.table('customers')"
    )
    order_source = (
        f"md.parquet({str(root / 'orders.parquet')!r})"
        if form == "parquet"
        else "md.table('orders')"
    )
    parser = (
        "None"
        if representation in ("date", "aware")
        else "ms.timestamp()"
        if read_zone is None
        else f"ms.timestamp(timezone={read_zone!r})"
    )
    factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.{backend}(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            f"customers=ms.entity(name='customers', datasource=ms.ref.datasource('warehouse'), source={customer_source}, primary_key=['id'])\n"
            f"orders=ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source={order_source}, primary_key=['id'])\n"
            "customer_id=ms.dimension_column(name='id', entity=customers, column='id')\n"
            "order_customer=ms.dimension_column(name='customer', entity=orders, column='customer')\n"
            "buyer=ms.relationship(name='buyer', from_entity=orders, to_entity=customers, keys=[ms.join_on(order_customer, customer_id)])\n"
            "amount=ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
            f"happened=ms.time_dimension_column(name='happened', entity=orders, column='happened', parse={parser}, is_default=True)\n"
            ""
            "amount_value=amount\n"
            "time_value=happened\n"
            "@ms.metric()\n"
            "def average():\n    return amount_value.mean(time=time_value)\n"
            "@ms.metric()\n"
            "def total():\n    return amount_value.sum(time=time_value)\n",
        }
    )
    return database


def _observe(session: Session, scope: TimeScope | CalendarAxis) -> mv.AnalysisFrame:
    return session.observe(
        {"value": session.catalog.metrics.get("sales.average")},
        by=mv.product(scope, session.members(session.catalog.entities.get("sales.customers"))),
    )


def _values(query: mv.AnalysisFrame) -> dict[str, object]:
    frame = query.compute().to_pandas()
    member = next(axis for axis in frame.attrs["axes"] if axis["role"] == "identity")
    identities = frame[("coordinate", member["token"])].tolist()
    assert identities == sorted(identities)
    assert len(identities) == len(set(identities))
    return dict(zip(identities, frame[("value", "value")], strict=True))


def test_report_mean_members_and_next_window_have_independent_oracles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, form="parquet")
    session = mv.session.get_or_create("mean-chain", timezone="Asia/Shanghai")
    values = _observe(session, session.time_scope(start="2026-08-01", end="2026-08-02"))
    assert _values(values) == {"A": 20, "B": 60}
    chosen = values.where(values["value"] < 50)
    selected = session.population_from(
        chosen, on=((chosen.axes[1], session.catalog.entities.get("sales.customers")),)
    )
    assert case_members(session, selected) == ["A"]
    following = session.observe(
        {"value": session.catalog.metrics.get("sales.total")},
        by=mv.product(session.time_scope(start="2026-08-03", end="2026-08-10"), selected),
        where=mv.in_population(selected),
    )
    assert _values(following) == {"A": 100}


def case_members(session: Session, population: mv.Population) -> list[object]:
    return session.frame(population).compute().to_pandas().iloc[:, 0].tolist()


@pytest.mark.parametrize(
    "representation,read_zone,expected",
    (
        ("naive", "Asia/Shanghai", 465),
        ("aware", None, 20),
        ("date", None, 465),
    ),
)
def test_source_kind_and_authority_remain_independent_of_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    representation: Literal["naive", "aware", "date"],
    read_zone: str | None,
    expected: int,
) -> None:
    _project(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        representation=representation,
        read_zone=read_zone,
    )
    session = mv.session.get_or_create("source-meaning", timezone="Asia/Shanghai")
    values = _observe(session, session.time_scope(start="2026-08-01", end="2026-08-02"))
    assert _values(values) == {"A": expected, "B": 60}


@pytest.mark.parametrize(
    "start,end,first,second,before,after,hours",
    (
        (
            "2026-03-08",
            "2026-03-09",
            "2026-03-08 06:30:00",
            "2026-03-08 07:30:00",
            "2026-03-08 04:59:59.999999",
            "2026-03-09 04:00:00",
            23,
        ),
        (
            "2026-11-01",
            "2026-11-02",
            "2026-11-01 05:30:00",
            "2026-11-01 06:30:00",
            "2026-11-01 03:59:59.999999",
            "2026-11-02 05:00:00",
            25,
        ),
    ),
)
def test_dst_mean_grid_and_cumulative_preserve_exact_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    start: str,
    end: str,
    first: str,
    second: str,
    before: str,
    after: str,
    hours: int,
) -> None:
    rows = (
        ("before", "A", 888, before),
        ("one", "A", 10, first),
        ("two", "A", 30, second),
        ("end", "A", 900, after),
        ("b", "B", 60, first),
    )
    _project(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        rows=rows,
        representation="aware",
        read_zone=None,
    )
    session = mv.session.get_or_create("dst", timezone="America/New_York")
    scope = session.time_scope(start=start, end=end)
    assert (scope.end - scope.start).total_seconds() == hours * 3600
    assert _values(_observe(session, scope)) == {"A": 20, "B": 60}
    grid = scope.by(mv.grain("day"))
    members = session.members(session.catalog.entities.get("sales.customers"))
    gridded = _observe(session, grid)
    assert _values(gridded) == {"A": 20, "B": 60}
    running = session.observe(
        {"value": session.catalog.metrics.get("sales.total").window(mv.all_history())},
        by=mv.product(grid, members),
    )
    assert _values(running) == {"A": 928, "B": 60}
    output = gridded.compute().to_pandas()
    calendar = output.attrs["axes"][0]["descriptor"]
    bounds = output.attrs["frozen_coordinates"][0]["rows"]
    assert calendar["timezone"] == "America/New_York"
    assert (
        bounds["actual_end"].iloc[0] - bounds["actual_start"].iloc[0]
    ).total_seconds() == hours * 3600


@pytest.mark.parametrize("read_zone", ("UTC", "Asia/Shanghai"))
def test_nanosecond_mean_or_conversion_rejects_before_business_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    read_zone: str,
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, form="parquet", read_zone=read_zone)
    path = tmp_path / "orders.parquet"
    table = pq.read_table(path)
    table = table.set_column(
        table.schema.get_field_index("happened"),
        "happened",
        table["happened"].cast(pa.timestamp("ns")),
    )
    pq.write_table(table, path)
    session = mv.session.get_or_create("unsupported-conversion", timezone="Asia/Shanghai")
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("business reads must not precede exact conversion admission")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(
        AnalysisError,
        match="submicrosecond conversion unsupported",
    ):
        _observe(session, session.time_scope(start="2026-08-01", end="2026-08-02")).compute()
    assert terminal_runs(session) == before


def test_exact_mean_qualification_missing_still_rejects_before_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.datasource.adapters import QualifiedSource

    _project(tmp_path, monkeypatch, semantic_project_factory)
    session = mv.session.get_or_create("no-fallback", timezone="Asia/Shanghai")
    value = _observe(session, session.time_scope(start="2026-08-01", end="2026-08-02"))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("qualification failure submitted a business batch")

    def missing(*args: object, **kwargs: object) -> QualifiedSource:
        raise AnalysisError(message="qualification unavailable")

    monkeypatch.setattr(SourceSession, "qualify", missing)
    monkeypatch.setattr(SourceSession, "execute", forbidden)
    with pytest.raises(AnalysisError, match="qualification unavailable"):
        value.compute()
    assert terminal_runs(session) == ()


def test_explicit_instant_bounds_do_not_erase_report_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory)
    boundaries = []
    for zone in ("UTC", "Asia/Shanghai"):
        session = mv.session.get_or_create(zone, timezone=zone)
        scope = session.time_scope(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )
        value = _observe(session, scope)
        boundaries.append((scope.start, scope.end, scope.boundary_timezone))
        assert _values(value) == {"A": 465, "B": 60}
    assert boundaries[0][:2] == boundaries[1][:2]
    assert boundaries[0][2] != boundaries[1][2]


def test_failed_reader_timezone_probe_does_not_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.datasource import timezone as datasource_timezone

    _project(tmp_path, monkeypatch, semantic_project_factory, read_zone=None)
    session = mv.session.get_or_create("engine-change", timezone="Asia/Shanghai")
    value = _observe(session, session.time_scope(start="2026-08-01", end="2026-08-02"))
    original = datasource_timezone.probe_engine_timezone

    def changed(backend: object) -> datasource_timezone.DatasourceEngineTimezone:
        current = original(backend)
        name, zone = datasource_timezone.parse_timezone("Asia/Shanghai")
        return replace(current, engine_timezone_name=name, engine_timezone_tz=zone)

    # Physical authority is prepared at compute, so constructing a Frame does
    # not freeze an unprobed reader assumption. A failed probe never falls back.
    def failed(backend: object) -> datasource_timezone.DatasourceEngineTimezone:
        raise AnalysisError(message="timezone probe unavailable")

    monkeypatch.setattr(datasource_timezone, "probe_engine_timezone", failed)
    with pytest.raises(AnalysisError, match="timezone probe unavailable"):
        value.compute()
    assert terminal_runs(session) == ()
    monkeypatch.setattr(datasource_timezone, "probe_engine_timezone", changed)
    assert _values(value) == {"A": 465, "B": 60}


@pytest.mark.release
@pytest.mark.slow
def test_timezone_mean_fixed_and_cold_preserve_authority_without_ambient_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    backend: Literal["duckdb"] = "duckdb"
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    rows = (
        ("one", "A", 10, "2026-08-01 08:00:00"),
        ("two", "A", 30, "2026-08-01 09:00:00"),
        ("b", "B", 60, "2026-08-01 08:00:00"),
    )
    database = _project(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        backend=backend,
        form="parquet",
        read_zone="UTC",
        rows=rows,
    )
    session = mv.session.get_or_create("recover-time", timezone="Asia/Shanghai")
    logical = _observe(
        session, session.time_scope(start="2026-08-01", end="2026-08-02").by(mv.grain("day"))
    )
    source = logical.persist()
    expected = source.to_pandas()
    assert expected[("value", "value")].tolist() == [20, 60]
    shutil.rmtree(tmp_path / "models")
    database.rename(database.with_suffix(".offline"))
    for file in tmp_path.glob("*.parquet"):
        file.rename(file.with_suffix(".offline"))
    pd.testing.assert_frame_equal(session.persisted_frame(source.artifact.id).to_pandas(), expected)
    worker = """
import json, sys
import duckdb, ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('terminal read touched source or semantic models')
ms.load = forbidden
duckdb.connect = forbidden
ibis.duckdb.connect = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1])
frame = session.persisted_frame(sys.argv[2]).to_pandas()
assert frame[('value','value')].tolist() == [20,60]
calendar = frame.attrs['axes'][0]['descriptor']
bounds = frame.attrs['frozen_coordinates'][0]['rows']
assert frame.attrs['axes'][0]['role'] == 'calendar' and calendar['timezone'] == 'Asia/Shanghai'
assert bounds['actual_start'].iloc[0].isoformat() == '2026-07-31T16:00:00+00:00'
assert bounds['actual_end'].iloc[0].isoformat() == '2026-08-01T16:00:00+00:00'
assert frame.attrs['axes'][1]['role'] == 'identity'
print(json.dumps({'pid': __import__('os').getpid(), 'values': frame[('value','value')].tolist()}))
"""
    process = subprocess.run(
        [sys.executable, "-c", worker, session.id, source.artifact.id],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="America/Los_Angeles"),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = __import__("json").loads(process.stdout.splitlines()[-1])
    assert report["pid"] != os.getpid()


@pytest.mark.parametrize(
    "read_zone,expected_ids,expected_total",
    (
        ("UTC", ["b", "one", "two"], 100),
        ("Asia/Shanghai", ["b", "end", "two"], 990),
    ),
)
def test_population_time_predicate_uses_source_authority_and_owns_its_period(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    read_zone: str,
    expected_ids: list[str],
    expected_total: int,
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, read_zone=read_zone)
    session = mv.session.get_or_create("within-members", timezone="Asia/Shanghai")
    scope = session.time_scope(start="2026-08-01", end="2026-08-02")
    time = session.catalog.fields.get("sales.orders.happened")
    metric = session.catalog.metrics.get("sales.total")
    members = session.members(
        session.catalog.entities.get("sales.orders"), where=time.within(scope)
    )
    assert case_members(session, members) == expected_ids
    for period in (scope, session.time_scope(start="2026-07-01", end="2026-09-01")):
        for condition in ("members", "predicate"):
            query = session.observe(
                {"value": metric},
                by=(period,),
                where=mv.in_population(members),
            )
            assert query.compute().to_pandas()[("value", "value")].tolist() == [expected_total]
    # Image and set selection consume the same already governed identity selection.
    buyer = session.catalog.relationships.get("sales.buyer")
    assert case_members(session, members.image(via=(buyer,))) == ["A", "B"]
    assert case_members(session, members.intersection(members)) == expected_ids
    assert case_members(session, members.difference(members)) == []
    assert session.observe({"value": metric}, by=(scope,)).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [expected_total]
