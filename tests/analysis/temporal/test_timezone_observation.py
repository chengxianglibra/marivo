"""Independent oracles for source authority, report windows and mean continuations."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import connect
from typing import Literal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import TimeScope
from marivo.analysis.core.graph import MethodNode, SourceLeaf, topology
from marivo.analysis.core.rules import ObserveMetric
from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods.physical import TimeShape
from marivo.analysis.methods.registry import MethodRegistration, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.ir import TimestampParse
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.support.json import Json, checked, encode, read
from tests.support.paths import PROJECT_ROOT

pytestmark = pytest.mark.runtime

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
    backend: Literal["duckdb", "sqlite"] = "duckdb",
    form: Literal["table", "parquet"] = "table",
    representation: Literal["naive", "aware", "date"] = "naive",
    read_zone: str | None = "UTC",
    rows: tuple[tuple[str, str, int, str], ...] = _ROWS,
) -> Path:
    monkeypatch.chdir(root)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
    database = root / ("warehouse.duckdb" if backend == "duckdb" else "warehouse.sqlite")
    physical = {"naive": "TIMESTAMP", "aware": "TIMESTAMPTZ", "date": "DATE"}[representation]
    if backend == "sqlite":
        with connect(database) as connection:
            connection.execute("CREATE TABLE customers (id BIGINT)")
            connection.executemany("INSERT INTO customers VALUES (?)", [(1,), (2,)])
            connection.execute(
                f"CREATE TABLE orders (id VARCHAR, customer BIGINT, amount BIGINT, happened {physical})"
            )
            connection.executemany(
                "INSERT INTO orders VALUES (?, ?, ?, ?)",
                [
                    (identity, 1 if customer == "A" else 2, amount, point)
                    for identity, customer, amount, point in rows
                ],
            )
    else:
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
        if representation == "date"
        else "ms.timestamp()"
        if read_zone is None
        else f"ms.timestamp(timezone={read_zone!r})"
    )
    granularity = "day" if representation == "date" else "second"
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
            f"happened=ms.time_dimension_column(name='happened', entity=orders, column='happened', granularity={granularity!r}, parse={parser}, is_default=True)\n"
            "average=ms.aggregate(name='average', measure=amount, agg='mean')\n"
            "total=ms.aggregate(name='total', measure=amount, agg='sum')\n"
            "running=ms.cumulative(name='running', base=total, over=happened)\n",
        }
    )
    return database


def _observe(session: Session, scope: TimeScope) -> mv.LogicalNumericRelation:
    value = session.members(ms.ref.entity("sales.customers")).observe(
        ms.ref.metric("sales.average"),
        during=scope,
        via=ms.ref.relationship("sales.buyer"),
        by=(ms.ref.entity("sales.customers"),),
    )
    assert isinstance(value, mv.LogicalNumericRelation)
    return value


def _parameters(value: mv.LogicalNumericRelation) -> ObserveMetric:
    return next(
        node.parameters
        for node in topology(value._node.root)
        if isinstance(node, MethodNode) and isinstance(node.parameters, ObserveMetric)
    )


@pytest.mark.parametrize("form", ("table", "parquet"))
def test_report_mean_members_and_next_window_have_independent_oracles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    form: Literal["table", "parquet"],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, form=form)
    session = mv.session.get_or_create("mean-chain", report_timezone="Asia/Shanghai")
    values = _observe(session, mv.time_scope(start="2026-08-01", end="2026-08-02"))
    params = _parameters(values)
    assert params.temporal.report.timezone == "Asia/Shanghai"
    assert params.temporal.axes[0].read_timezone == "UTC"
    assert params.temporal.axes[0].source == "declared"
    assert all(
        node.definition.shape.time == TimeShape("instant", "us", "UTC")
        for node in topology(values._node.root)
        if isinstance(node, SourceLeaf)
    )
    assert values.execute().to_pandas().set_index("member").value.to_dict() == {"A": 20, "B": 60}
    selected = values.where(values.value.lt(50)).members()
    assert isinstance(selected, mv.LogicalAnalysisDomain)
    assert selected.execute().to_pandas().member.tolist() == ["A"]
    following = selected.observe(
        ms.ref.metric("sales.total"),
        during=mv.time_scope(start="2026-08-03", end="2026-08-10"),
        via=ms.ref.relationship("sales.buyer"),
        by=(ms.ref.entity("sales.customers"),),
    )
    assert following.execute().to_pandas().set_index("member").value.to_dict() == {"A": 100}


@pytest.mark.parametrize(
    "representation,read_zone,expected,origin",
    (
        ("naive", "Asia/Shanghai", 465, "declared"),
        ("aware", None, 20, "physical"),
        ("date", None, 465, "civil_date"),
    ),
)
def test_source_kind_and_authority_remain_independent_of_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    representation: Literal["naive", "aware", "date"],
    read_zone: str | None,
    expected: int,
    origin: str,
) -> None:
    _project(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        representation=representation,
        read_zone=read_zone,
    )
    session = mv.session.get_or_create("source-meaning", report_timezone="Asia/Shanghai")
    values = _observe(session, mv.time_scope(start="2026-08-01", end="2026-08-02"))
    authority = _parameters(values).temporal.axes[0]
    assert authority.source == origin
    if read_zone is None and representation != "date":
        parser = _parameters(values).event.parse
        assert isinstance(parser, TimestampParse)
        assert parser.timezone is None
    assert values.execute().to_pandas().set_index("member").value.to_dict() == {
        "A": expected,
        "B": 60,
    }


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
    session = mv.session.get_or_create("dst", report_timezone="America/New_York")
    scope = mv.time_scope(start=start, end=end)
    values = _observe(session, scope)
    params = _parameters(values)
    assert params.start is not None and params.end is not None
    assert (
        datetime.fromisoformat(params.end) - datetime.fromisoformat(params.start)
    ).total_seconds() == hours * 3600
    assert values.execute().to_pandas().set_index("member").value.to_dict() == {"A": 20, "B": 60}
    grid = mv.time_grid(during=scope, grain=mv.grain("day"))
    members = session.members(ms.ref.entity("sales.customers"))
    gridded = members.observe(
        ms.ref.metric("sales.average"),
        during=grid,
        via=ms.ref.relationship("sales.buyer"),
        by=(ms.ref.entity("sales.customers"),),
    )
    assert gridded.execute().to_pandas().set_index("member").value.to_dict() == {"A": 20, "B": 60}
    running = members.observe(
        ms.ref.metric("sales.running"),
        at=grid.end,
        via=ms.ref.relationship("sales.buyer"),
        by=(ms.ref.entity("sales.customers"),),
    )
    assert running.execute().to_pandas().set_index("member").value.to_dict() == {"A": 928, "B": 60}


@pytest.mark.parametrize(
    "zone,first,last,before,after",
    (
        (
            "UTC",
            "2026-07-31 16:00:00",
            "2026-08-01 15:59:59.999999",
            "2026-07-31 15:59:59.999999",
            "2026-08-01 16:00:00",
        ),
        (
            "Asia/Shanghai",
            "2026-08-01 00:00:00",
            "2026-08-01 23:59:59.999999",
            "2026-07-31 23:59:59.999999",
            "2026-08-02 00:00:00",
        ),
        (
            "America/New_York",
            "2026-07-31 12:00:00",
            "2026-08-01 11:59:59.999999",
            "2026-07-31 11:59:59.999999",
            "2026-08-01 12:00:00",
        ),
        (
            "UTC+05:45",
            "2026-07-31 21:45:00",
            "2026-08-01 21:44:59.999999",
            "2026-07-31 21:44:59.999999",
            "2026-08-01 21:45:00",
        ),
    ),
)
def test_sqlite_default_conversion_matches_independent_instant_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    zone: str,
    first: str,
    last: str,
    before: str,
    after: str,
) -> None:
    monkeypatch.setenv("TZ", zone)
    rows = (
        ("before", "A", 888, before),
        ("one", "A", 10, first),
        ("two", "A", 30, last),
        ("end", "A", 900, after),
        ("b", "B", 60, first),
    )
    _project(
        tmp_path, monkeypatch, semantic_project_factory, backend="sqlite", read_zone=None, rows=rows
    )
    session = mv.session.get_or_create("sqlite-boundaries", report_timezone="Asia/Shanghai")
    values = _observe(
        session,
        mv.time_scope(
            start=datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
            end=datetime(2026, 8, 1, 16, tzinfo=timezone.utc),
        ),
    )
    assert values.execute().to_pandas().set_index("member").value.to_dict() == {1: 20, 2: 60}


def test_sqlite_declared_new_york_source_converts_across_spring_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    rows = (
        ("before", "A", 888, "2026-03-07 23:59:59.999999"),
        ("one", "A", 10, "2026-03-08 01:30:00"),
        ("two", "A", 30, "2026-03-08 03:30:00"),
        ("end", "A", 900, "2026-03-09 00:00:00"),
        ("b", "B", 60, "2026-03-08 03:30:00"),
    )
    _project(
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        backend="sqlite",
        read_zone="America/New_York",
        rows=rows,
    )
    session = mv.session.get_or_create("sqlite-dst", report_timezone="America/New_York")
    values = _observe(session, mv.time_scope(start="2026-03-08", end="2026-03-09"))
    assert _parameters(values).temporal.axes[0].source == "declared"
    assert values.execute().to_pandas().set_index("member").value.to_dict() == {1: 20, 2: 60}


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
    session = mv.session.get_or_create("unsupported-conversion", report_timezone="Asia/Shanghai")
    before = session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("business reads must not precede exact conversion admission")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    with pytest.raises(
        AnalysisError,
        match=r"qualified exact key for metric\.mean"
        if read_zone == "UTC"
        else "submicrosecond conversion unsupported",
    ):
        _observe(session, mv.time_scope(start="2026-08-01", end="2026-08-02")).execute()
    assert session.runs().items == before


@pytest.mark.parametrize("zone", ("UTC", "Asia/Shanghai", "America/New_York", "UTC+05:45"))
def test_sqlite_system_default_is_frozen_and_declared_timezone_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    zone: str,
) -> None:
    monkeypatch.setenv("TZ", zone)
    rows = (
        ("one", "A", 2, "2026-08-01 08:00:00.000001"),
        ("two", "A", 5, "2026-08-01 09:00:00.000001"),
        ("b", "B", 60, "2026-08-01 08:00:00.000001"),
    )
    _project(
        tmp_path, monkeypatch, semantic_project_factory, backend="sqlite", read_zone=None, rows=rows
    )
    session = mv.session.get_or_create("default-reader", report_timezone="Asia/Shanghai")
    members = session.members(ms.ref.entity("sales.customers"))
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    scope = mv.time_scope(start="2026-08-01", end="2026-08-02")
    values = members.observe(
        ms.ref.metric("sales.average"),
        during=scope,
        via=ms.ref.relationship("sales.buyer"),
        by=(ms.ref.entity("sales.customers"),),
    )
    assert isinstance(values, mv.LogicalNumericRelation)
    params = _parameters(values)
    assert params.event.timezone is None
    assert isinstance(params.event.parse, TimestampParse)
    assert params.event.parse.timezone is None
    assert params.temporal.axes[0].source == "system_fallback"
    assert params.temporal.axes[0].read_timezone == zone
    monkeypatch.setenv("TZ", "UTC-03:30")
    assert values.execute().to_pandas().set_index("member").value.to_dict() == {1: 3.5, 2: 60}
    model = tmp_path / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("parse=ms.timestamp()", "parse=ms.timestamp(timezone='UTC')")
    )
    ms.load(workspace_dir=tmp_path)
    override_session = mv.session.get_or_create("declared-reader", report_timezone="UTC")
    override = _observe(override_session, scope)
    assert _parameters(override).temporal.axes[0].source == "declared"
    assert _parameters(override).temporal.axes[0].read_timezone == "UTC"
    assert override.execute().to_pandas().set_index("member").value.to_dict() == {1: 3.5, 2: 60}


def test_exact_mean_qualification_missing_still_rejects_before_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory)
    session = mv.session.get_or_create("no-fallback", report_timezone="Asia/Shanghai")
    value = _observe(session, mv.time_scope(start="2026-08-01", end="2026-08-02"))
    lookup = MethodRegistry.lookup

    def missing(registry: MethodRegistry, key: MethodKey) -> MethodRegistration:
        original = lookup(registry, key)
        return replace(original, implementations=()) if key.name == "metric.mean" else original

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("qualification failure submitted a business batch")

    monkeypatch.setattr(MethodRegistry, "lookup", missing)
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    with pytest.raises(AnalysisError, match=r"qualified exact key for metric\.mean"):
        value.execute()
    assert session.runs().items == ()


def test_explicit_instant_bounds_do_not_erase_report_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory)
    scope = mv.time_scope(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    parameters = []
    for zone in ("UTC", "Asia/Shanghai"):
        session = mv.session.get_or_create(zone, report_timezone=zone)
        value = _observe(session, scope)
        parameters.append(_parameters(value))
        assert value.execute().to_pandas().set_index("member").value.to_dict() == {
            "A": 465,
            "B": 60,
        }
    assert parameters[0].start == parameters[1].start and parameters[0].end == parameters[1].end
    assert parameters[0].temporal != parameters[1].temporal


def test_changed_engine_authority_rejects_before_business_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.datasource import timezone as datasource_timezone

    _project(tmp_path, monkeypatch, semantic_project_factory, read_zone=None)
    session = mv.session.get_or_create("engine-change", report_timezone="Asia/Shanghai")
    value = _observe(session, mv.time_scope(start="2026-08-01", end="2026-08-02"))
    assert _parameters(value).temporal.axes[0].source == "engine"
    original = datasource_timezone.probe_engine_timezone

    def changed(backend: object) -> datasource_timezone.DatasourceEngineTimezone:
        current = original(backend)
        name, zone = datasource_timezone.parse_timezone("Asia/Shanghai")
        return replace(current, engine_timezone_name=name, engine_timezone_tz=zone)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("authority drift submitted a business batch")

    monkeypatch.setattr(datasource_timezone, "probe_engine_timezone", changed)
    monkeypatch.setattr(SourceSession, "batches", forbidden)
    with pytest.raises(AnalysisError, match="source timezone changed after graph construction"):
        value.execute()


@pytest.mark.parametrize("backend", ("duckdb", "sqlite"))
def test_timezone_mean_fixed_and_cold_preserve_authority_without_ambient_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: Literal["duckdb", "sqlite"],
) -> None:
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
        form="parquet" if backend == "duckdb" else "table",
        read_zone="UTC" if backend == "duckdb" else None,
        rows=rows,
    )
    session = mv.session.get_or_create("recover-time", report_timezone="Asia/Shanghai")
    logical = _observe(session, mv.time_scope(start="2026-08-01", end="2026-08-02"))
    source = logical.execute()
    assert source.to_pandas().value.tolist() == [20, 60]
    state: dict[str, Json] = {
        "session": session.id,
        "source": source.state.artifact_ref.ref,
        "original": snapshot(source),
        "temporal": checked(_parameters(logical).temporal.model_dump(mode="json")),
        "source_pid": os.getpid(),
    }
    (tmp_path / "timezone-recovery.json").write_bytes(encode(state))
    shutil.rmtree(tmp_path / "models")
    database.rename(database.with_suffix(".offline"))
    for file in tmp_path.glob("*.parquet"):
        file.rename(file.with_suffix(".offline"))
    reports = []
    for phase in ("fixed", "cold"):
        output = tmp_path / f"{phase}.json"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.temporal.timezone_observation_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=PROJECT_ROOT,
            env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="America/Los_Angeles"),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(report["pid"] for report in reports)}) == 3
    assert all(report["source_and_timezone_forbidden"] is True for report in reports)
