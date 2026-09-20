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
        "source=md.table('orders', columns={'id': md.source_column('id', data_type='int64'), "
        "'day': md.source_column('day', data_type='date'), "
        "'amount': md.source_column('amount', data_type='decimal(12,2)'), "
        "'cost': md.source_column('cost', data_type='decimal(12,2)'), "
        "'quantity': md.source_column('quantity', data_type='int64')}), primary_key=['id'])\n"
        "day = ms.time_dimension_column(name='day', entity=orders, column='day', granularity='day')\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')\n"
        "cost = ms.measure_column(name='cost', entity=orders, column='cost', additivity='additive')\n"
        "quantity = ms.measure_column(name='quantity', entity=orders, column='quantity', additivity='additive')\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def net(orders_table):\n"
        "    return orders_table.amount * orders_table.quantity\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
        "def minus_one(orders_table):\n"
        "    return orders_table.amount - ibis.literal(\n"
        "        decimal.Decimal('1.00'), type='decimal(3,2)'\n"
        "    )\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
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
        "gmv_quantity = ms.aggregate(name='gmv_quantity', measure=quantity, agg='count')\n"
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


def _aggregate(session: mv.Session, metric: str) -> mv.MaterializedMetricDataset:
    return session.observe(ms.ref.metric(f"sales.{metric}")).aggregate().execute()


def test_computed_measure_multiplication_publishes_exact_decimal(
    computation_session: mv.Session,
) -> None:
    result = _aggregate(computation_session, "net_total")
    rows = result.to_pandas()
    assert isinstance(rows["net_total"].iloc[0], Decimal)
    assert rows["net_total"].iloc[0] == Decimal("51.50")


def test_computed_measure_decimal_subtraction_aggregates_exactly(
    computation_session: mv.Session,
) -> None:
    # amount - decimal literal 1.00 over rows 15.75 and 4.25.
    assert _aggregate(computation_session, "minus_sum").to_pandas()["minus_sum"].iloc[0] == Decimal(
        "18.00"
    )
    assert _aggregate(computation_session, "minus_min").to_pandas()["minus_min"].iloc[0] == Decimal(
        "3.25"
    )
    assert _aggregate(computation_session, "minus_max").to_pandas()["minus_max"].iloc[0] == Decimal(
        "14.75"
    )


def test_computed_measure_integer_arithmetic_aggregates_exactly(
    computation_session: mv.Session,
) -> None:
    # quantity * 2 over rows 3 and 1.
    assert _aggregate(computation_session, "double_sum").to_pandas()["double_sum"].iloc[0] == 8
    assert _aggregate(computation_session, "double_count").to_pandas()["double_count"].iloc[0] == 2
    assert _aggregate(computation_session, "double_min").to_pandas()["double_min"].iloc[0] == 2
    assert _aggregate(computation_session, "double_max").to_pandas()["double_max"].iloc[0] == 6


def test_runtime_linear_over_int64_measures_stays_int64(
    computation_session: mv.Session,
) -> None:
    expression = rm.linear(
        add=[ms.ref.metric("sales.quantity_total"), ms.ref.metric("sales.gmv_quantity")],
        subtract=[ms.ref.metric("sales.quantity_total")],
        label="net_quantity",
    )
    result = computation_session.observe(expression).aggregate().execute()
    rows = result.to_pandas()
    assert pd.api.types.is_integer_dtype(rows["net_quantity"])
    assert rows["net_quantity"].iloc[0] == 2


def test_catalog_linear_over_decimal_metrics_publishes_exact_decimal(
    computation_session: mv.Session,
) -> None:
    result = _aggregate(computation_session, "net_revenue")
    rows = result.to_pandas()
    assert isinstance(rows["net_revenue"].iloc[0], Decimal)
    assert rows["net_revenue"].iloc[0] == Decimal("13.00")


def test_weighted_mean_over_computed_measure_executes(
    computation_session: mv.Session,
) -> None:
    # net rows are 15.75 * 3 = 47.25 and 4.25 * 1 = 4.25, weights 3 and 1:
    # (47.25 * 3 + 4.25 * 1) / (3 + 1) = 146.0 / 4 = 36.5.
    rows = _aggregate(computation_session, "net_weighted").to_pandas()
    assert rows["net_weighted"].iloc[0] == pytest.approx(36.5)


def test_direct_column_decimal_measure_regression(computation_session: mv.Session) -> None:
    rows = _aggregate(computation_session, "gmv").to_pandas()
    assert isinstance(rows["gmv"].iloc[0], Decimal)
    assert rows["gmv"].iloc[0] == Decimal("20.00")


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
    from tests.multisource_environment.credentials import password

    monkeypatch.setenv(f"MARIVO_TEST_{backend.upper()}_PASSWORD", password())
    monkeypatch.setenv(f"MARIVO_TEST_{backend.upper()}_USER", "analysis_reader")


def _create_remote_table(backend: str, table: str) -> None:
    create, insert = _REMOTE_PHYSICAL[backend]
    if backend == "postgres":
        from tests.multisource_environment import postgres_analysis as pg

        with pg.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(create.format(table=table))
            cur.execute(insert.format(table=table))
    elif backend == "mysql":
        from tests.multisource_environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(create.format(table=table))
            cur.execute(insert.format(table=table))
    else:
        from tests.multisource_environment import clickhouse_analysis as ch

        with ch.connection(admin=True) as admin:
            admin.command(create.format(table=table))
            admin.command(insert.format(table=table))


def _drop_remote_table(backend: str, table: str) -> None:
    if backend == "postgres":
        from tests.multisource_environment import postgres_analysis as pg

        with pg.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
    elif backend == "mysql":
        from tests.multisource_environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as admin, admin.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
    else:
        from tests.multisource_environment import clickhouse_analysis as ch

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
        "source=md.table('"
        + table
        + "', columns={'id': md.source_column('id', data_type='"
        + ("uint64" if backend == "clickhouse" else "int64")
        + "'), "
        "'amount': md.source_column('amount', data_type='decimal(12,2)'), "
        "'weight': md.source_column('weight', data_type='"
        + ("uint64" if backend == "clickhouse" else "int64")
        + "')}), primary_key=['id'])\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')\n"
        "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity='additive')\n"
        "@ms.measure(entity=orders, additivity='additive')\n"
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
        rows = session.observe(ms.ref.metric(f"sales.{metric}")).aggregate().execute().to_pandas()
        value = rows[metric].iloc[0]
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
    from marivo.analysis.compiler.errors import DatasetCompilationError

    _opt_in_backend(backend)
    table = "c4_" + uuid4().hex
    _create_remote_table(backend, table)
    try:
        session = _remote_project_session(tmp_path, monkeypatch, backend, table)
        logical = session.observe(ms.ref.metric("sales.amount_mean")).aggregate()
        with pytest.raises(DatasetCompilationError, match="AVG scale is a public contract"):
            logical.execute()
    finally:
        _drop_remote_table(backend, table)


def test_mysql_div_precision_guard_refuses_non_default_server_value() -> None:
    """The execution guard names the server fact and the repair, without I/O."""
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.mysql_execution import (
        MySQLExecutionAdapter as _Adapter,
    )

    class _GuardProbe:
        """Stub carrying only the guard's own collaborators, no connection."""

        def __init__(self, value: object) -> None:
            self._probe_value = value
            self._div_precision_increment = None
            self._run_ref = None
            self._closed = False

        require_div_precision_increment = _Adapter.require_div_precision_increment
        _div_precision = _Adapter._div_precision

        def error(self, *args: object, **kwargs: object) -> MaterializationError:
            return MaterializationError(
                expected=str(args[0]),
                received=str(args[1]),
                repair=str(args[2]),
                stage=kwargs.get("stage", "output_validation"),
                run_ref=None,
            )

        def read_scalar(self, value: object, **kwargs: object) -> object:
            return self._probe_value

        def statement(self, *args: object, **kwargs: object) -> object:
            return None

    probe = _GuardProbe(6)
    with pytest.raises(MaterializationError, match="div_precision_increment=6"):
        probe.require_div_precision_increment()
    ok = _GuardProbe(4)
    ok.require_div_precision_increment()


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
    from tests.multisource_environment import trino_analysis as trino

    connection = trino.connection(admin=True).__enter__()
    cursor = connection.cursor()
    return connection, cursor


@pytest.fixture
def trino_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[mv.Session]:
    _opt_in_backend("trino")
    monkeypatch.setenv("MARIVO_TEST_TRINO_USER", "analysis_reader")
    monkeypatch.setenv("TZ", "UTC")
    table = "c4_" + uuid4().hex
    from tests.multisource_environment import trino_analysis as trino

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
            "source=md.table('"
            + table
            + "', columns={'id': md.source_column('id', data_type='int64'), "
            "'amount': md.source_column('amount', data_type='decimal(12,2)'), "
            "'weight': md.source_column('weight', data_type='int64')}), primary_key=['id'])\n"
            "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')\n"
            "weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity='additive')\n"
            "@ms.measure(entity=orders, additivity='additive')\n"
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
    rows = trino_session.observe(ms.ref.metric("sales.net_total")).aggregate().execute().to_pandas()
    value = rows["net_total"].iloc[0]
    assert isinstance(value, Decimal)
    assert value == Decimal("51.50")


def test_trino_decimal_mean_keeps_rejection(trino_session: mv.Session) -> None:
    """AVG stays at the input scale and rounds, so the mean cell stays closed."""
    from marivo.analysis.compiler.errors import DatasetCompilationError

    logical = trino_session.observe(ms.ref.metric("sales.amount_mean")).aggregate()
    with pytest.raises(
        DatasetCompilationError, match="composed Decimal results require resolved precision"
    ):
        logical.execute()
