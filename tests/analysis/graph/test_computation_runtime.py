"""Real-execution contracts for computed measures and Linear coefficient typing.

Computed Measure bodies execute inside the single entity scan at aggregation
time, and decimal result types follow the semantic rule-table derivation rather
than the engine's ibis inference. Linear terms multiply by integral ``int``
literals so int64 stays int64 and decimal stays Decimal. The per-backend
sections execute the plan §4 matrix on the opt-in services; every expectation
is a hand-computed ``decimal.Decimal`` constant.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis import runtime_metric as rm

pytestmark = pytest.mark.runtime

_ROWS = "INSERT INTO orders VALUES (1,'2026-07-01',15.75,5.25,3), (2,'2026-07-01',4.25,1.75,1)"


@pytest.fixture
def computation_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "computation"\n')
    datasource = tmp_path / "models" / "datasources"
    semantic = tmp_path / "models" / "semantic" / "sales"
    datasource.mkdir(parents=True)
    semantic.mkdir(parents=True)
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (semantic / "orders.py").write_text(
        "import decimal\n"
        "import ibis\n"
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders', columns={'id': 'id', "
        "'day': 'day', "
        "'amount': 'amount', "
        "'cost': 'cost', "
        "'quantity': 'quantity'}), primary_key=['id'])\n"
        "day = ms.time_dimension_column(name='day', entity=orders, column='day', granularity='day', parse=None, is_default=True)\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
        "cost = ms.measure_column(name='cost', entity=orders, column='cost', additivity=ms.additive_all())\n"
        "quantity = ms.measure_column(name='quantity', entity=orders, column='quantity', additivity=ms.additive_all())\n"
        "@ms.measure(entity=orders, additivity=ms.additive_all())\n"
        "def net(orders_table):\n"
        "    return orders_table.amount * orders_table.quantity\n"
        "@ms.measure(entity=orders, additivity=ms.additive_all())\n"
        "def minus_one(orders_table):\n"
        "    return orders_table.amount - ibis.literal(\n"
        "        decimal.Decimal('1.00'), type='decimal(3,2)'\n"
        "    )\n"
        "@ms.measure(entity=orders, additivity=ms.additive_all())\n"
        "def double_quantity(orders_table):\n"
        "    return orders_table.quantity * 2\n"
        "net_total = ms.aggregate(name='net_total', measure=net, agg='sum')\n"
        "minus_sum = ms.aggregate(name='minus_sum', measure=minus_one, agg='sum')\n"
        "minus_min = ms.aggregate(name='minus_min', measure=minus_one, agg='min')\n"
        "minus_max = ms.aggregate(name='minus_max', measure=minus_one, agg='max')\n"
        "double_sum = ms.aggregate(name='double_sum', measure=double_quantity, agg='sum')\n"
        "double_count = ms.aggregate(name='double_count', measure=double_quantity, agg='count')\n"
        "double_min = ms.aggregate(name='double_min', measure=double_quantity, agg='min')\n"
        "double_max = ms.aggregate(name='double_max', measure=double_quantity, agg='max')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cost_total = ms.aggregate(name='cost_total', measure=cost, agg='sum')\n"
        "quantity_total = ms.aggregate(name='quantity_total', measure=quantity, agg='sum')\n"
        "gmv_quantity = ms.count(name='gmv_quantity', entity=orders)\n"
        "net_weighted = ms.weighted_mean(name='net_weighted', value=net, weight=quantity)\n"
        "net_revenue = ms.linear(name='net_revenue', add=[gmv], subtract=[cost_total])\n"
    )
    with duckdb.connect(str(tmp_path / "warehouse.duckdb")) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), "
            "cost DECIMAL(12,2), quantity BIGINT)"
        )
        connection.execute(_ROWS)
    return mv.session.get_or_create("computation", report_timezone="UTC")


def _aggregate(session: mv.Session, metric: str) -> mv.MaterializedNumericRelation:
    return (
        session.members(ms.ref.entity("sales.orders"))
        .observe(ms.ref.metric(f"sales.{metric}"), by=(mv.member(),))
        .rollup()
        .execute()
    )


def _semantic_value(session: mv.Session, metric: str) -> object:
    preview = session.catalog.preview(
        ms.ref.metric(f"sales.{metric}"), scope=md.unpruned(max_rows=10, timeout_seconds=30)
    )
    assert preview.returned_row_count == 1
    return preview.rows[0]["value"]


def test_computed_measure_multiplication_publishes_exact_decimal(
    computation_session: mv.Session,
) -> None:
    # Semantic preview retains the independent calculation oracle; this is not graph qualification.
    assert _semantic_value(computation_session, "net_total") == Decimal("51.50")


def test_computed_measure_decimal_subtraction_aggregates_exactly(
    computation_session: mv.Session,
) -> None:
    assert _semantic_value(computation_session, "minus_sum") == Decimal("18.00")
    assert _semantic_value(computation_session, "minus_min") == Decimal("3.25")
    assert _semantic_value(computation_session, "minus_max") == Decimal("14.75")


def test_computed_measure_integer_arithmetic_aggregates_exactly(
    computation_session: mv.Session,
) -> None:
    for metric, expected in (
        ("double_sum", 8),
        ("double_count", 2),
        ("double_min", 2),
        ("double_max", 6),
    ):
        assert _semantic_value(computation_session, metric) == expected


def test_runtime_linear_over_int64_measures_stays_int64(
    computation_session: mv.Session,
) -> None:
    expression = rm.linear(
        add=[ms.ref.metric("sales.quantity_total"), ms.ref.metric("sales.gmv_quantity")],
        subtract=[ms.ref.metric("sales.quantity_total")],
        label="net_quantity",
    )
    result = (
        computation_session.members(ms.ref.entity("sales.orders"))
        .observe(expression, by=(mv.member(),))
        .rollup()
        .execute()
    )
    rows = result.to_pandas()
    assert pd.api.types.is_integer_dtype(rows["value"])
    assert rows["value"].iloc[0] == 2


def test_catalog_linear_over_decimal_metrics_publishes_exact_decimal(
    computation_session: mv.Session,
) -> None:
    result = _aggregate(computation_session, "net_revenue")
    rows = result.to_pandas()
    assert isinstance(rows["value"].iloc[0], Decimal)
    assert rows["value"].iloc[0] == Decimal("13.00")


def test_weighted_mean_over_computed_measure_executes(
    computation_session: mv.Session,
) -> None:
    # (47.25 * 3 + 4.25 * 1) / 4 = 36.5 in Semantic preview.
    assert _semantic_value(computation_session, "net_weighted") == pytest.approx(36.5)


def test_direct_column_decimal_measure_regression(computation_session: mv.Session) -> None:
    rows = _aggregate(computation_session, "gmv").to_pandas()
    assert isinstance(rows["value"].iloc[0], Decimal)
    assert rows["value"].iloc[0] == Decimal("20.00")


# ---------------------------------------------------------------------------
# Remote backend matrix (plan section 4): computed measures, Linear, decimal
# units on the opt-in services. Rows: amount=15.75 with weight 3, amount=4.25
# with weight 1. net = amount * weight sums to 51.50; the amount sum is 20.00;
# the MySQL mean publishes scale s+4 = 6; the decimal linear cell keeps the
# exact decimal result. Every expectation is a hand-computed Decimal constant.
# ---------------------------------------------------------------------------


def _opt_in_backend(backend: str) -> None:
    if os.environ.get(f"MARIVO_{backend.upper()}_ANALYSIS_TEST") != "1":
        pytest.skip(f"opt-in {backend} service")


_REMOTE_PHYSICAL = {
    "postgres": (
        "CREATE TABLE {table}(id BIGINT, amount NUMERIC(12,2), weight BIGINT)",
        "INSERT INTO {table} VALUES (1, 15.75, 3), (2, 4.25, 1)",
    ),
    "mysql": (
        "CREATE TABLE {table}(id BIGINT, amount DECIMAL(12,2), weight BIGINT) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin",
        "INSERT INTO {table} VALUES (1, 15.75, 3), (2, 4.25, 1)",
    ),
    "clickhouse": (
        "CREATE TABLE {table}(id UInt64, amount Decimal(12,2), weight UInt64) "
        "ENGINE=MergeTree ORDER BY id",
        "INSERT INTO {table} VALUES (1, 15.75, 3), (2, 4.25, 1)",
    ),
}

_REMOTE_DATASOURCE = {
    "postgres": (
        "md.postgres(name='warehouse', host='127.0.0.1', port=15432, database='analysis', "
        "user_env='MARIVO_TEST_POSTGRES_USER', password_env='MARIVO_TEST_POSTGRES_PASSWORD')\n"
    ),
    "mysql": (
        "md.mysql(name='warehouse', host='127.0.0.1', port=23306, database='analysis', "
        "user_env='MARIVO_TEST_MYSQL_USER', password_env='MARIVO_TEST_MYSQL_PASSWORD')\n"
    ),
    "clickhouse": (
        "md.clickhouse(name='warehouse', host='127.0.0.1', port=18123, database='qualification', "
        "user_env='MARIVO_TEST_CLICKHOUSE_USER', password_env='MARIVO_TEST_CLICKHOUSE_PASSWORD')\n"
    ),
}


def _remote_credentials(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.datasource.environment.credentials import password

    monkeypatch.setenv(f"MARIVO_TEST_{backend.upper()}_PASSWORD", password())
    monkeypatch.setenv(f"MARIVO_TEST_{backend.upper()}_USER", "analysis_reader")


def _create_remote_table(backend: str, table: str) -> None:
    create, insert = _REMOTE_PHYSICAL[backend]
    if backend == "postgres":
        from tests.datasource.environment import postgres_analysis as pg

        with pg.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(create.format(table=table))
            cur.execute(insert.format(table=table))
    elif backend == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(create.format(table=table))
            cur.execute(insert.format(table=table))
    else:
        from tests.datasource.environment import clickhouse_analysis as ch

        with ch.connection(admin=True) as admin:
            admin.command(create.format(table=table))
            admin.command(insert.format(table=table))


def _drop_remote_table(backend: str, table: str) -> None:
    if backend == "postgres":
        from tests.datasource.environment import postgres_analysis as pg

        with pg.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
    elif backend == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
    else:
        from tests.datasource.environment import clickhouse_analysis as ch

        with ch.connection(admin=True) as admin:
            admin.command(f"DROP TABLE IF EXISTS {table}")


def _remote_project_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    table: str,
) -> mv.Session:
    """Author the project with the UUID table name, then load the Session."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text(f'[project]\nname = "c4-{backend}"\n')
    _remote_credentials(backend, monkeypatch)
    datasource = tmp_path / "models" / "datasources"
    semantic = tmp_path / "models" / "semantic" / "sales"
    datasource.mkdir(parents=True)
    semantic.mkdir(parents=True)
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\n" + _REMOTE_DATASOURCE[backend]
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (semantic / "orders.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('" + table + "', columns={'id': 'id', "
        "'amount': 'amount', "
        "'weight': 'weight'}), primary_key=['id'])\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
        "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
        "@ms.measure(entity=orders, additivity=ms.additive_all())\n"
        "def net(orders_table):\n"
        "    return orders_table.amount * orders_table.weight\n"
        "net_total = ms.aggregate(name='net_total', measure=net, agg='sum')\n"
        "amount_total = ms.aggregate(name='amount_total', measure=amount, agg='sum')\n"
        "weight_total = ms.aggregate(name='weight_total', measure=weight, agg='sum')\n"
        "amount_mean = ms.aggregate(name='amount_mean', measure=amount, agg='mean')\n"
        "net_linear = ms.linear(name='net_linear', add=[amount_total], subtract=[amount_total])\n"
    )
    return mv.session.get_or_create(f"c4-{backend}", report_timezone="UTC")


def _remote_matrix_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    metric: str,
    expected: Decimal,
) -> None:
    table = "c4_" + uuid4().hex
    _create_remote_table(backend, table)
    try:
        session = _remote_project_session(tmp_path, monkeypatch, backend, table)
        value = _semantic_value(session, metric)
        assert isinstance(value, Decimal), f"{backend}: {type(value).__name__}"
        assert value == expected, f"{backend}: {value}"
    finally:
        _drop_remote_table(backend, table)


@pytest.mark.parametrize("backend", ["postgres", "mysql", "clickhouse"])
def test_remote_computed_measure_decimal_sum_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    _opt_in_backend(backend)
    # 15.75 * 3 + 4.25 * 1 = 51.50.
    _remote_matrix_case(tmp_path, monkeypatch, backend, "net_total", Decimal("51.50"))


@pytest.mark.parametrize("backend", ["postgres", "mysql", "clickhouse"])
def test_remote_decimal_linear_publishes_exact_decimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    _opt_in_backend(backend)
    # amount_total - amount_total = 20.00 - 20.00 as dec(38,2) leaves.
    _remote_matrix_case(tmp_path, monkeypatch, backend, "net_linear", Decimal("0.00"))


@pytest.mark.parametrize("backend", ["mysql"])
def test_mysql_decimal_mean_stays_rejected_until_the_mean_equation_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """MySQL mean over decimal keeps its admission rejection this stage.

    The server arithmetic is exact (AVG publishes scale s+4 = 6, verified
    live), but the mean pipeline still publishes ``sum/count`` through a
    float-labeled division that the value-exact transport rule refuses, so the
    backend keeps ``mean`` out of its resolved-decimal units and admission
    rejects the metric with the engine-fact diagnostic.
    """
    from marivo.analysis.errors import AnalysisError

    _opt_in_backend(backend)
    table = "c4_" + uuid4().hex
    _create_remote_table(backend, table)
    try:
        session = _remote_project_session(tmp_path, monkeypatch, backend, table)
        logical = (
            session.members(ms.ref.entity("sales.orders"))
            .observe(ms.ref.metric("sales.amount_mean"), by=(mv.member(),))
            .rollup()
        )
        with pytest.raises(AnalysisError):
            logical.execute()
    finally:
        _drop_remote_table(backend, table)


# ---------------------------------------------------------------------------
# Trino (plan section 4, probe-decided): the live probe measured exact
# DECIMAL arithmetic for mul/add/sub and SUM, but AVG(DECIMAL) stays at the
# input scale and rounds (10.005 -> 10.01 decimal(12,2)), so row expressions
# open and every composed decimal unit keeps its rejection.
# ---------------------------------------------------------------------------

_TRINO_PHYSICAL = (
    "CREATE TABLE {table}(id BIGINT, amount DECIMAL(12,2), weight BIGINT)",
    "INSERT INTO {table} VALUES (1, 15.75, 3), (2, 4.25, 1)",
)


def _trino_admin_cursor():
    from tests.datasource.environment import trino_analysis as trino

    connection = trino.connection(admin=True).__enter__()
    cursor = connection.cursor()
    return connection, cursor


@pytest.fixture
def trino_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[mv.Session]:
    _opt_in_backend("trino")
    monkeypatch.setenv("MARIVO_TEST_TRINO_USER", "analysis_reader")
    monkeypatch.setenv("TZ", "UTC")
    table = "c4_" + uuid4().hex
    from tests.datasource.environment import trino_analysis as trino

    with trino.connection(admin=True) as admin:
        cursor = admin.cursor()
        cursor.execute(_TRINO_PHYSICAL[0].format(table=table)).fetchall()
        cursor.execute(_TRINO_PHYSICAL[1].format(table=table)).fetchall()
    try:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "marivo.toml").write_text('[project]\nname = "c4-trino"\n')
        datasource = tmp_path / "models" / "datasources"
        semantic = tmp_path / "models" / "semantic" / "sales"
        datasource.mkdir(parents=True)
        semantic.mkdir(parents=True)
        (datasource / "warehouse.py").write_text(
            "import marivo.datasource as md\n"
            "md.trino(name='warehouse', host='127.0.0.1', port=18080, catalog='iceberg', "
            "schema='analysis', timezone='UTC', user_env='MARIVO_TEST_TRINO_USER')\n"
        )
        (semantic / "_domain.py").write_text(
            "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
        )
        (semantic / "orders.py").write_text(
            "import marivo.datasource as md\n"
            "import marivo.semantic as ms\n"
            "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
            "source=md.table('" + table + "', columns={'id': 'id', "
            "'amount': 'amount', "
            "'weight': 'weight'}), primary_key=['id'])\n"
            "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
            "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
            "@ms.measure(entity=orders, additivity=ms.additive_all())\n"
            "def net(orders_table):\n"
            "    return orders_table.amount * orders_table.weight\n"
            "net_total = ms.aggregate(name='net_total', measure=net, agg='sum')\n"
            "amount_total = ms.aggregate(name='amount_total', measure=amount, agg='sum')\n"
            "weight_total = ms.aggregate(name='weight_total', measure=weight, agg='sum')\n"
            "amount_mean = ms.aggregate(name='amount_mean', measure=amount, agg='mean')\n"
        )
        yield mv.session.get_or_create("c4-trino", report_timezone="UTC")
    finally:
        with trino.connection(admin=True) as admin:
            admin.cursor().execute(f"DROP TABLE IF EXISTS {table}").fetchall()


def test_trino_computed_measure_decimal_sum_is_exact(
    trino_session: mv.Session,
) -> None:
    # 15.75 * 3 + 4.25 * 1 = 51.50, exact through Trino DECIMAL arithmetic.
    rows = (
        trino_session.members(ms.ref.entity("sales.orders"))
        .observe(ms.ref.metric("sales.net_total"), by=(mv.member(),))
        .rollup()
        .execute()
        .to_pandas()
    )
    value = rows["value"].iloc[0]
    assert isinstance(value, Decimal)
    assert value == Decimal("51.50")


def test_trino_decimal_mean_keeps_rejection(trino_session: mv.Session) -> None:
    """AVG stays at the input scale and rounds, so the mean cell stays closed."""
    from marivo.analysis.errors import AnalysisError

    logical = (
        trino_session.members(ms.ref.entity("sales.orders"))
        .observe(ms.ref.metric("sales.amount_mean"), by=(mv.member(),))
        .rollup()
    )
    with pytest.raises(AnalysisError):
        logical.execute()


@pytest.mark.parametrize("metric", ("net_total", "minus_sum", "double_sum", "net_weighted"))
def test_computed_measure_graph_rejects_before_business_reads_and_run(
    computation_session: mv.Session, metric: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import SourceSession

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unqualified computed graph read business rows")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    members = computation_session.members(ms.ref.entity("sales.orders"))
    with pytest.raises(AnalysisError, match="Measure is not a frozen direct column"):
        members.observe(ms.ref.metric(f"sales.{metric}"), by=(mv.member(),))
    assert computation_session.runs().items == ()
