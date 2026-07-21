"""Tests for qualify_provenance_sql and its integration with parity_check."""

from __future__ import annotations

import textwrap
from contextlib import contextmanager
from unittest.mock import patch

import ibis
import pytest

from marivo.datasource.ir import qualify_provenance_sql

# ---------------------------------------------------------------------------
# qualify_provenance_sql unit tests
# ---------------------------------------------------------------------------


def test_qualify_no_qualifiers_returns_unchanged() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    assert qualify_provenance_sql(sql, {}) == sql


def test_qualify_qualifies_bare_table() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_provenance_sql(sql, {"orders": "iceberg_inf.orders"})
    assert "iceberg_inf.orders" in result
    assert result == "SELECT SUM(amount) FROM iceberg_inf.orders"


def test_qualify_already_qualified_table_unchanged() -> None:
    sql = "SELECT SUM(amount) FROM iceberg_inf.orders"
    result = qualify_provenance_sql(sql, {"orders": "iceberg_inf.orders"})
    assert result == "SELECT SUM(amount) FROM iceberg_inf.orders"


def test_qualify_mixed_qualified_and_unqualified() -> None:
    sql = "SELECT SUM(amount) FROM orders JOIN iceberg_inf.regions r ON orders.region = r.id"
    result = qualify_provenance_sql(sql, {"orders": "iceberg_inf.orders"})
    assert "iceberg_inf.orders" in result


def test_qualify_cte_reference_not_qualified() -> None:
    sql = "WITH recent AS (SELECT * FROM orders WHERE year = 2025) SELECT SUM(amount) FROM recent"
    result = qualify_provenance_sql(sql, {"orders": "iceberg_inf.orders"})
    # 'recent' is a CTE reference, should NOT be qualified
    assert "iceberg_inf.recent" not in result
    # 'orders' inside CTE body should be qualified
    assert "iceberg_inf.orders" in result


def test_qualify_unknown_table_not_qualified() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_provenance_sql(sql, {"other_table": "iceberg_inf.other_table"})
    assert result == sql


def test_qualify_dialect_passthrough() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_provenance_sql(sql, {"orders": "iceberg_inf.orders"}, dialect="trino")
    assert "iceberg_inf.orders" in result


def test_qualify_multi_part_database() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_provenance_sql(sql, {"orders": "catalog.schema.orders"})
    assert "catalog" in result
    assert "schema" in result
    assert "orders" in result


def test_qualify_tuple_database_parity() -> None:
    """When database is a tuple like ('catalog', 'schema'), the joined
    qualifier produces a 3-part name that qualify_provenance_sql handles."""
    sql = "SELECT SUM(amount) FROM orders"
    # Simulates the f"{'.'.join(db)}.{table}" path from parity.py
    result = qualify_provenance_sql(sql, {"orders": "catalog.schema.orders"})
    assert "catalog" in result
    assert "schema" in result
    assert "orders" in result


# ---------------------------------------------------------------------------
# Parity integration helpers
# ---------------------------------------------------------------------------


class _FakeConnectionService:
    def __init__(self, factory):
        self._factory = factory

    @property
    def project_root(self):
        return None

    def session_backend(self, name: str):
        return self._factory(name)

    @contextmanager
    def use_backend(self, name: str):
        yield self._factory(name)

    def close_all(self):
        pass


@contextmanager
def _patch_project_backends(project, backend_factory):
    fake_service = _FakeConnectionService(backend_factory)
    project._connection_service_instance = fake_service
    with patch(
        "marivo.datasource.runtime.DatasourceConnectionService",
        return_value=fake_service,
    ):
        yield


# ---------------------------------------------------------------------------
# Parity integration: provenance SQL qualification
# ---------------------------------------------------------------------------


_ENTITY_WITH_DATABASE_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders", database="sales_mart"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
    )
    def total_amount(table):
        return table.amount.sum()
""")


@pytest.fixture
def _duckdb_with_schema():
    """In-memory DuckDB with a test orders table in the sales_mart schema."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE SCHEMA IF NOT EXISTS sales_mart")
    con.con.execute(
        "CREATE TABLE sales_mart.orders "
        "(order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO sales_mart.orders VALUES "
        "(1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def _schema_backend_factory(_duckdb_with_schema):
    def _factory(datasource_semantic_id: str):
        return _duckdb_with_schema

    return _factory


def test_parity_with_database_qualified_entity(
    semantic_project_factory,
    _duckdb_with_schema,
    _schema_backend_factory,
) -> None:
    """Provenance SQL uses bare table name; entity has database=; parity succeeds.

    The entity declares database="sales_mart", so the bare 'orders' in
    provenance SQL must be auto-qualified to 'sales_mart.orders' before
    execution.
    """
    project = semantic_project_factory(
        {"sales/_domain.py": _ENTITY_WITH_DATABASE_PY},
    )

    with _patch_project_backends(project, _schema_backend_factory):
        result = project.parity_check("sales.total_amount")

    assert result.ok, f"Parity failed: expected={result.expected}, actual={result.actual}"


_ENTITY_NO_DATABASE_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
    )
    def total_amount(table):
        return table.amount.sum()
""")


@pytest.fixture
def _duckdb_no_schema():
    """In-memory DuckDB with a test orders table in the default schema."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def _no_schema_backend_factory(_duckdb_no_schema):
    def _factory(datasource_semantic_id: str):
        return _duckdb_no_schema

    return _factory


def test_parity_without_database_on_entity(
    semantic_project_factory,
    _duckdb_no_schema,
    _no_schema_backend_factory,
) -> None:
    """Provenance SQL uses bare table name; entity has no database=; parity succeeds.

    No qualification is needed when the table lives in the default schema.
    """
    project = semantic_project_factory(
        {"sales/_domain.py": _ENTITY_NO_DATABASE_PY},
    )

    with _patch_project_backends(project, _no_schema_backend_factory):
        result = project.parity_check("sales.total_amount")

    assert result.ok, f"Parity failed: expected={result.expected}, actual={result.actual}"


# ---------------------------------------------------------------------------
# Parity integration: datasource database fallback
# ---------------------------------------------------------------------------


_ENTITY_DATASOURCE_DB_FALLBACK_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DATASOURCE_WITH_DATABASE_PY = (
    "import marivo.datasource as md\n"
    "md.duckdb("
    "name='warehouse', path=':memory:', extra={'database': 'sales_mart'})\n"
)


def test_parity_datasource_database_fallback(
    semantic_project_factory,
    _duckdb_with_schema,
    _schema_backend_factory,
) -> None:
    """Provenance SQL uses bare table name; entity has no database= but datasource
    declares database="sales_mart". Parity qualifies bare refs from the
    datasource database field and succeeds.

    The DuckDB backend used for testing connects without a default schema,
    so we create a view in the default schema that mirrors the qualified table
    so that the ibis entity materialization (backend.table("orders"))
    also works.
    """
    _duckdb_with_schema.con.execute("CREATE VIEW orders AS SELECT * FROM sales_mart.orders")
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": _DATASOURCE_WITH_DATABASE_PY,
            "sales/_domain.py": _ENTITY_DATASOURCE_DB_FALLBACK_PY,
        },
    )

    with _patch_project_backends(project, _schema_backend_factory):
        result = project.parity_check("sales.total_amount")

    assert result.ok, f"Parity failed: expected={result.expected}, actual={result.actual}"


_ENTITY_DATASOURCE_DB_FULLY_QUALIFIED_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity="additive",
        provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM sales_mart.orders", dialect="duckdb"),
    )
    def total_amount(table):
        return table.amount.sum()
""")


def test_parity_provenance_sql_already_qualified_no_double_qualify(
    semantic_project_factory,
    _duckdb_with_schema,
    _schema_backend_factory,
) -> None:
    """Provenance SQL uses a fully-qualified table name (sales_mart.orders); entity
    has no database= but datasource declares database="sales_mart".

    qualify_provenance_sql must leave already-qualified tables unchanged, so the
    SQL executes correctly without double-qualification like
    sales_mart.sales_mart.orders.
    """
    _duckdb_with_schema.con.execute("CREATE VIEW orders AS SELECT * FROM sales_mart.orders")
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": _DATASOURCE_WITH_DATABASE_PY,
            "sales/_domain.py": _ENTITY_DATASOURCE_DB_FULLY_QUALIFIED_PY,
        },
    )

    with _patch_project_backends(project, _schema_backend_factory):
        result = project.parity_check("sales.total_amount")

    assert result.ok, f"Parity failed: expected={result.expected}, actual={result.actual}"
