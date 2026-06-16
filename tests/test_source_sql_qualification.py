"""Tests for qualify_source_sql and its integration with parity_check."""

from __future__ import annotations

import textwrap
from contextlib import contextmanager
from unittest.mock import patch

import ibis
import pytest

from marivo.datasource.ir import qualify_source_sql

# ---------------------------------------------------------------------------
# qualify_source_sql unit tests
# ---------------------------------------------------------------------------


def test_qualify_no_qualifiers_returns_unchanged() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    assert qualify_source_sql(sql, {}) == sql


def test_qualify_qualifies_bare_table() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_source_sql(sql, {"orders": "iceberg_inf.orders"})
    assert "iceberg_inf.orders" in result
    assert result == "SELECT SUM(amount) FROM iceberg_inf.orders"


def test_qualify_already_qualified_table_unchanged() -> None:
    sql = "SELECT SUM(amount) FROM iceberg_inf.orders"
    result = qualify_source_sql(sql, {"orders": "iceberg_inf.orders"})
    assert result == "SELECT SUM(amount) FROM iceberg_inf.orders"


def test_qualify_mixed_qualified_and_unqualified() -> None:
    sql = "SELECT SUM(amount) FROM orders JOIN iceberg_inf.regions r ON orders.region = r.id"
    result = qualify_source_sql(sql, {"orders": "iceberg_inf.orders"})
    assert "iceberg_inf.orders" in result


def test_qualify_cte_reference_not_qualified() -> None:
    sql = "WITH recent AS (SELECT * FROM orders WHERE year = 2025) SELECT SUM(amount) FROM recent"
    result = qualify_source_sql(sql, {"orders": "iceberg_inf.orders"})
    # 'recent' is a CTE reference, should NOT be qualified
    assert "iceberg_inf.recent" not in result
    # 'orders' inside CTE body should be qualified
    assert "iceberg_inf.orders" in result


def test_qualify_unknown_table_not_qualified() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_source_sql(sql, {"other_table": "iceberg_inf.other_table"})
    assert result == sql


def test_qualify_dialect_passthrough() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_source_sql(sql, {"orders": "iceberg_inf.orders"}, dialect="trino")
    assert "iceberg_inf.orders" in result


def test_qualify_multi_part_database() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = qualify_source_sql(sql, {"orders": "catalog.schema.orders"})
    assert "catalog" in result
    assert "schema" in result
    assert "orders" in result


def test_qualify_tuple_database_parity() -> None:
    """When database is a tuple like ('catalog', 'schema'), the joined
    qualifier produces a 3-part name that qualify_source_sql handles."""
    sql = "SELECT SUM(amount) FROM orders"
    # Simulates the f"{'.'.join(db)}.{table}" path from parity.py
    result = qualify_source_sql(sql, {"orders": "catalog.schema.orders"})
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
# Parity integration: source_sql qualification
# ---------------------------------------------------------------------------


_ENTITY_WITH_DATABASE_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
    orders = ms.entity(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders", database="sales_mart"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.simple_metric(
        entities=[orders],
        additivity="additive",
        source_sql="SELECT SUM(amount) AS total_amount FROM orders",
        source_dialect="duckdb",
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
    """source_sql uses bare table name; entity has database=; parity succeeds.

    The entity declares database="sales_mart", so the bare 'orders' in
    source_sql must be auto-qualified to 'sales_mart.orders' before
    execution.
    """
    project = semantic_project_factory(
        {"sales/_domain.py": _ENTITY_WITH_DATABASE_PY},
    )

    with _patch_project_backends(project, _schema_backend_factory):
        result = project.parity_check("sales.total_amount")

    assert result.ok, f"Parity failed: expected={result.expected}, actual={result.actual}"


_ENTITY_NO_DATABASE_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
    orders = ms.entity(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.simple_metric(
        entities=[orders],
        additivity="additive",
        source_sql="SELECT SUM(amount) AS total_amount FROM orders",
        source_dialect="duckdb",
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
    """source_sql uses bare table name; entity has no database=; parity succeeds.

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
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
    orders = ms.entity(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.simple_metric(
        entities=[orders],
        additivity="additive",
        source_sql="SELECT SUM(amount) AS total_amount FROM orders",
        source_dialect="duckdb",
    )
    def total_amount(table):
        return table.amount.sum()
""")

_DATASOURCE_WITH_DATABASE_PY = (
    "import marivo.datasource as md\n"
    "warehouse = md.DatasourceSpec("
    "name='warehouse', backend_type='duckdb', path=':memory:', database='sales_mart')\n"
    "md.datasource(warehouse)\n"
)


def test_parity_datasource_database_fallback(
    semantic_project_factory,
    _duckdb_with_schema,
    _schema_backend_factory,
) -> None:
    """source_sql uses bare table name; entity has no database= but datasource
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
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
    orders = ms.entity(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders"),
    )

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.simple_metric(
        entities=[orders],
        additivity="additive",
        source_sql="SELECT SUM(amount) AS total_amount FROM sales_mart.orders",
        source_dialect="duckdb",
    )
    def total_amount(table):
        return table.amount.sum()
""")


def test_parity_source_sql_already_qualified_no_double_qualify(
    semantic_project_factory,
    _duckdb_with_schema,
    _schema_backend_factory,
) -> None:
    """source_sql uses a fully-qualified table name (sales_mart.orders); entity
    has no database= but datasource declares database="sales_mart".

    qualify_source_sql must leave already-qualified tables unchanged, so the
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
