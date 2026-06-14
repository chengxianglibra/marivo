"""Tests for marivo.semantic.materializer — dataset/field/metric materialization.

Tests cover:
- Dataset materialize with real DuckDB backend
- Field materialize on materialized dataset
- Base metric materialize (e.g., orders.amount.sum())
- Backend cache: same datasource reuses backend
- Dataset cache: same dataset reuses table
- SQL escape hatches are rejected by the semantic loader
- Cross-datasource fail: metric with datasets from different datasources -> error
- Metric not found -> error
- Dataset not found -> error
- Resolver creates fresh Materializer instances for expression access
- EntityRuntimeMetadata stored on project after materialize
"""

from __future__ import annotations

import textwrap
from contextlib import contextmanager
from unittest.mock import patch

import ibis
import pytest

from marivo.semantic.catalog import SemanticCatalog, SemanticKind, SemanticRef
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import EntityProvenance
from marivo.semantic.materializer import EntityRuntimeMetadata, Materializer

# ---------------------------------------------------------------------------
# DuckDB backend fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_backend():
    """In-memory DuckDB backend with a test orders table."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def backend_factory(duckdb_backend):
    """A backend_factory callable that always returns the shared DuckDB backend."""

    def _factory(datasource_semantic_id: str):
        return duckdb_backend

    return _factory


# ---------------------------------------------------------------------------
# Fake DatasourceConnectionService for patching
# ---------------------------------------------------------------------------


class _FakeConnectionService:
    """Stub DatasourceConnectionService that delegates to a test factory."""

    def __init__(self, factory):
        self._factory = factory

    @property
    def project_root(self):
        return None

    def session_backend(self, name):
        return self._factory(name)

    @contextmanager
    def use_backend(self, name):
        yield self._factory(name)

    def close_all(self):
        pass


def _patch_connection_service(project, factory):
    """Return a context manager that patches project._connection_service to use *factory*."""
    fake = _FakeConnectionService(factory)
    return patch.object(project, "_connection_service", return_value=fake)


def _materialize_dataset(project, ref: str):
    return SemanticCatalog(project)._resolver().table(SemanticRef(ref, kind=SemanticKind.ENTITY))


def _materialize_field(project, ref: str):
    return (
        SemanticCatalog(project)
        ._resolver()
        .dimension(SemanticRef(ref, kind=SemanticKind.DIMENSION))
    )


def _materialize_metric(project, ref: str):
    return SemanticCatalog(project)._resolver().metric(SemanticRef(ref, kind=SemanticKind.METRIC))


# ---------------------------------------------------------------------------
# Model file templates
# ---------------------------------------------------------------------------


_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")

_DATASET_AND_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
    def total_amount(table):
        return table.amount.sum()
""")

_SQL_VIEW_DATASET_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    @ms.entity(name="orders_view", datasource="warehouse", source=ms.table("orders"))
    def orders_view(backend):
        return backend.sql("SELECT * FROM orders")
""")


# ---------------------------------------------------------------------------
# Dataset materialize
# ---------------------------------------------------------------------------


def test_dataset_materialize(semantic_project_factory, backend_factory) -> None:
    """Materializing a dataset should return an ibis Table."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    assert project.is_ready()

    with _patch_connection_service(project, backend_factory):
        table = _materialize_dataset(project, "sales.orders")
    assert isinstance(table, ibis.Table)
    # The table should have the expected columns
    assert "amount" in table.columns
    assert "region" in table.columns


def test_dataset_materialize_returns_rows(semantic_project_factory, backend_factory) -> None:
    """Materialized dataset should return actual data via to_pandas()."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with _patch_connection_service(project, backend_factory):
        table = _materialize_dataset(project, "sales.orders")
    df = table.to_pandas()
    assert len(df) == 2
    assert list(df["amount"]) == [100.0, 200.0]


def test_dataset_table_source_passes_database(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource='warehouse',\n"
                "    source=ms.table('orders', database='sales_mart'),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def table(self, name, /, *, database=None):
            assert name == "orders"
            assert database == "sales_mart"
            return ibis.table({"amount": "float64"}, name=f"{database}.{name}")

    with _patch_connection_service(project, lambda _: _Backend()):
        table = _materialize_dataset(project, "sales.orders")
    assert table.get_name() == "sales_mart.orders"


def test_dataset_file_source_reads_parquet(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource='warehouse',\n"
                "    source=ms.file('/data/orders/*.parquet', format='parquet', hive_partitioning=True),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def read_parquet(self, path, **options):
            assert path == "/data/orders/*.parquet"
            assert options == {"hive_partitioning": True}
            return ibis.table({"amount": "float64"}, name="orders_file")

    with _patch_connection_service(project, lambda _: _Backend()):
        table = _materialize_dataset(project, "sales.orders")
    assert table.get_name() == "orders_file"


def test_dataset_file_source_requires_backend_reader(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource='warehouse',\n"
                "    source=ms.file('/data/orders.csv', format='csv'),\n"
                ")\n"
            ),
        }
    )

    with (
        _patch_connection_service(project, lambda _: object()),
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_dataset(project, "sales.orders")

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "does not support csv file sources" in exc_info.value.message


# ---------------------------------------------------------------------------
# Field materialize
# ---------------------------------------------------------------------------


def test_field_materialize(semantic_project_factory, backend_factory) -> None:
    """Materializing a field should return an ibis Value expression."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with _patch_connection_service(project, backend_factory):
        field_expr = _materialize_field(project, "sales.orders.amount")
    # ibis Value is the base of column expressions
    assert field_expr is not None


# ---------------------------------------------------------------------------
# Base metric materialize
# ---------------------------------------------------------------------------


def test_metric_materialize_sum(semantic_project_factory, backend_factory) -> None:
    """Materializing a sum metric should return a scalar ibis Value."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with _patch_connection_service(project, backend_factory):
        metric_expr = _materialize_metric(project, "sales.total_amount")
    assert metric_expr is not None
    # Execute the metric to verify the value
    result = metric_expr.to_pandas()
    assert result == pytest.approx(300.0)


def test_materializer_dimension_on_uses_supplied_table(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    mat = Materializer(project, backend_factory)
    supplied = ibis.table({"amount": "float64", "region": "string"}, name="supplied_orders")

    value = mat.dimension_on("sales.orders.amount", supplied)

    assert isinstance(value, ibis.expr.types.Value)
    assert "supplied_orders" in repr(value)


def test_materializer_dimension_on_is_not_ref_cached(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    mat = Materializer(project, backend_factory)
    first = ibis.table({"amount": "float64", "region": "string"}, name="first_orders")
    second = ibis.table({"amount": "float64", "region": "string"}, name="second_orders")

    first_value = mat.dimension_on("sales.orders.amount", first)
    second_value = mat.dimension_on("sales.orders.amount", second)

    assert "first_orders" in repr(first_value)
    assert "second_orders" in repr(second_value)


def test_materializer_metric_on_uses_supplied_table(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    mat = Materializer(project, backend_factory)
    supplied = ibis.table({"amount": "float64", "region": "string"}, name="supplied_orders")

    value = mat.metric_on("sales.total_amount", supplied)

    assert isinstance(value, ibis.expr.types.Value)
    assert "supplied_orders" in repr(value)


def test_materializer_metric_on_rejects_derived_metric(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )\n"
                "def revenue(table):\n"
                "    return table.amount.sum()\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )\n"
                "def orders_count(table):\n"
                "    return table.order_id.nunique()\n"
                "ratio = ms.derived_metric(\n"
                "    name='ratio',\n"
                "    decomposition=ms.ratio(numerator=revenue, denominator=orders_count),\n"
                ")\n"
            ),
        }
    )
    mat = Materializer(project, backend_factory)
    supplied = ibis.table({"amount": "float64", "order_id": "int64"}, name="supplied_orders")

    with pytest.raises(SemanticRuntimeError) as exc_info:
        mat.metric_on("sales.ratio", supplied)

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "derived metric" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Backend cache
# ---------------------------------------------------------------------------


def test_backend_by_datasource_reuses_same_backend(semantic_project_factory) -> None:
    """Same datasource should reuse the same backend instance."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    created_backends = []

    def tracking_factory(datasource_id: str):
        con = ibis.duckdb.connect(":memory:")
        con.con.execute(
            "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
        )
        con.con.execute(
            "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), "
            "(2, 200.0, 'EU', '2025-02-01')"
        )
        created_backends.append((datasource_id, id(con)))
        return con

    # Materialize dataset then metric — both use warehouse
    with _patch_connection_service(project, tracking_factory):
        _materialize_dataset(project, "sales.orders")
        _materialize_metric(project, "sales.total_amount")

    # Backend factory should only have been called once per unique datasource
    # (each materialize_* call creates a new Materializer, but the factory is
    # called only once within each Materializer)
    # First call: dataset materialize -> 1 factory call
    # Second call: metric materialize -> 1 factory call (new Materializer)
    assert len(created_backends) == 2
    # Both should be for the same datasource
    assert all(ds == "warehouse" for ds, _ in created_backends)


def test_backend_by_datasource_within_single_materializer(semantic_project_factory) -> None:
    """Within one Materializer, the same datasource should only call factory once."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    call_count = 0

    def counting_factory(datasource_id: str):
        nonlocal call_count
        call_count += 1
        con = ibis.duckdb.connect(":memory:")
        con.con.execute(
            "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
        )
        con.con.execute(
            "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), "
            "(2, 200.0, 'EU', '2025-02-01')"
        )
        return con

    # Use the Materializer directly to test internal caching
    mat = Materializer(project, counting_factory)
    mat.entity("sales.orders")
    assert call_count == 1

    # Calling again should use cache
    mat.entity("sales.orders")
    assert call_count == 1

    # Materialize a metric on the same datasource — should still be cached
    mat.metric("sales.total_amount")
    assert call_count == 1


# ---------------------------------------------------------------------------
# Dataset cache
# ---------------------------------------------------------------------------


def test_dataset_cache_reuses_table(semantic_project_factory) -> None:
    """Same dataset should return the same table object within a Materializer."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )

    def factory(ds_id: str):
        return con

    mat = Materializer(project, factory)
    table1 = mat.entity("sales.orders")
    table2 = mat.entity("sales.orders")
    assert table1 is table2


# ---------------------------------------------------------------------------
# SQL escape hatch rejection
# ---------------------------------------------------------------------------


def test_dataset_decorator_body_rejected(semantic_project_factory) -> None:
    """Dataset bodies are no longer the physical-source entrypoint."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _SQL_VIEW_DATASET_PY,
        },
        load=False,
    )

    result = project.load()

    assert result.status == "errored"
    assert result.errors
    assert result.errors[0].kind == "organization_error"
    assert "not callable" in result.errors[0].message


def test_ibis_table_detection(semantic_project_factory, duckdb_backend) -> None:
    """Dataset using backend.table() should be detected as IBIS_TABLE provenance."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with _patch_connection_service(project, factory):
        _materialize_dataset(project, "sales.orders")

    meta = project._runtime_metadata.get("sales.orders")
    assert meta is not None
    assert meta.entity_provenance == EntityProvenance.IBIS_TABLE
    assert meta.raw_sql_snippet is None


# ---------------------------------------------------------------------------
# Cross-datasource fail-closed
# ---------------------------------------------------------------------------


def test_cross_datasource_metric_fails(semantic_project_factory, duckdb_backend) -> None:
    """A metric referencing datasets from different datasources must raise."""

    cross_ds_model = textwrap.dedent("""\
        import marivo.semantic as ms
        orders_a = ms.entity(name="orders_a", datasource="warehouse1", source=ms.table("orders"))

        orders_b = ms.entity(name="orders_b", datasource="warehouse2", source=ms.table("orders"))

        @ms.metric(entities=[orders_a, orders_b], root_entity=orders_a, additivity="additive", decomposition=ms.sum(), )
        def cross_metric(t1, t2):
            return t1.amount.sum()
    """)

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": cross_ds_model,
        }
    )

    with (
        _patch_connection_service(project, factory),
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_metric(project, "sales.cross_metric")

    assert exc_info.value.kind == ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED


# ---------------------------------------------------------------------------
# Not found errors
# ---------------------------------------------------------------------------


def test_metric_not_found(semantic_project_factory, backend_factory) -> None:
    """Materializing a non-existent metric should raise SemanticRuntimeError."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with (
        _patch_connection_service(project, backend_factory),
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_metric(project, "sales.nonexistent")

    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


def test_entity_not_found(semantic_project_factory, backend_factory) -> None:
    """Materializing a non-existent entity should raise SemanticRuntimeError."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with (
        _patch_connection_service(project, backend_factory),
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_dataset(project, "sales.nonexistent")

    assert exc_info.value.kind == ErrorKind.ENTITY_NOT_FOUND


def test_dimension_not_found(semantic_project_factory, backend_factory) -> None:
    """Materializing a non-existent dimension should raise SemanticRuntimeError."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with (
        _patch_connection_service(project, backend_factory),
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_field(project, "sales.nonexistent")

    assert exc_info.value.kind == ErrorKind.DIMENSION_NOT_FOUND


# ---------------------------------------------------------------------------
# Derived metric materialization
# ---------------------------------------------------------------------------


def test_derived_metric_ratio_materialize(semantic_project_factory, backend_factory) -> None:
    """Derived ratio metric: synthesized numerator / denominator."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
        def revenue(table):
            return table.amount.sum()

        revenue_ratio = ms.derived_metric(
            name="revenue_ratio",
            decomposition=ms.ratio(
                numerator="sales.revenue",
                denominator="sales.revenue",
            ),
        )
    """)

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # revenue_ratio = revenue / revenue = 1.0
    with _patch_connection_service(project, backend_factory):
        result = _materialize_metric(project, "sales.revenue_ratio")
    value = result.to_pandas()
    assert value == pytest.approx(1.0)


def test_derived_metric_has_no_materializer_sidecar_entry(
    semantic_project_factory,
    backend_factory,
) -> None:
    derived_model = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
        def revenue(table):
            return table.amount.sum()

        revenue_ratio = ms.derived_metric(
            name="revenue_ratio",
            decomposition=ms.ratio(
                numerator="sales.revenue",
                denominator="sales.revenue",
            ),
        )
    """)

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": derived_model,
        }
    )

    sidecar = project._sidecar
    assert sidecar is not None
    assert "sales.revenue" in sidecar
    assert "sales.revenue_ratio" not in sidecar
    with _patch_connection_service(project, backend_factory):
        result = _materialize_metric(project, "sales.revenue_ratio")
    assert result.to_pandas() == pytest.approx(1.0)


def test_derived_metric_weighted_average(semantic_project_factory, backend_factory) -> None:
    """Derived weighted_average metric: synthesized numerator / weight."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
        def revenue(table):
            return table.amount.sum()

        @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
        def count_metric(table):
            return table.count()

        aov = ms.derived_metric(
            name="aov",
            decomposition=ms.weighted_average(
                value="sales.revenue",
                weight="sales.count_metric",
            ),
        )
    """)

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # aov = revenue / count = 300 / 2 = 150.0
    with _patch_connection_service(project, backend_factory):
        result = _materialize_metric(project, "sales.aov")
    value = result.to_pandas()
    assert value == pytest.approx(150.0)


def test_derived_metric_recursive(semantic_project_factory, backend_factory) -> None:
    """Derived metric referencing another derived metric's component."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), )
        def revenue(table):
            return table.amount.sum()

        revenue_ratio = ms.derived_metric(
            name="revenue_ratio",
            decomposition=ms.ratio(
                numerator="sales.revenue",
                denominator="sales.revenue",
            ),
        )

        double_ratio = ms.derived_metric(
            name="double_ratio",
            decomposition=ms.ratio(
                numerator="sales.revenue_ratio",
                denominator="sales.revenue_ratio",
            ),
        )
    """)

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # double_ratio = revenue_ratio / revenue_ratio = 1.0
    with _patch_connection_service(project, backend_factory):
        result = _materialize_metric(project, "sales.double_ratio")
    value = result.to_pandas()
    assert value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Fresh Materializer per call
# ---------------------------------------------------------------------------


def test_fresh_materializer_per_resolver_call(semantic_project_factory) -> None:
    """Each resolver helper call should create a new Materializer."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    factory_calls = []

    def tracking_factory(ds_id: str):
        factory_calls.append(ds_id)
        con = ibis.duckdb.connect(":memory:")
        con.con.execute(
            "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
        )
        con.con.execute(
            "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), "
            "(2, 200.0, 'EU', '2025-02-01')"
        )
        return con

    # First call creates a Materializer and calls factory
    with _patch_connection_service(project, tracking_factory):
        _materialize_dataset(project, "sales.orders")
    assert len(factory_calls) == 1

    # Second call creates a new Materializer, calls factory again (no cross-call cache)
    with _patch_connection_service(project, tracking_factory):
        _materialize_dataset(project, "sales.orders")
    assert len(factory_calls) == 2


# ---------------------------------------------------------------------------
# EntityRuntimeMetadata on project
# ---------------------------------------------------------------------------


def test_runtime_metadata_stored_on_project(semantic_project_factory, duckdb_backend) -> None:
    """After materialization, EntityRuntimeMetadata should be on the project."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    # Before materialization, no metadata
    assert project._runtime_metadata.get("sales.orders") is None

    # After materialization
    with _patch_connection_service(project, factory):
        _materialize_dataset(project, "sales.orders")
    meta = project._runtime_metadata.get("sales.orders")
    assert meta is not None
    assert isinstance(meta, EntityRuntimeMetadata)
    assert meta.entity_provenance == EntityProvenance.IBIS_TABLE
    assert meta.detected_at is not None


def test_runtime_metadata_cleared_on_reload(semantic_project_factory, duckdb_backend) -> None:
    """Runtime metadata should be cleared when project is reloaded."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with _patch_connection_service(project, factory):
        _materialize_dataset(project, "sales.orders")
    assert project._runtime_metadata.get("sales.orders") is not None

    project.load()
    assert project._runtime_metadata.get("sales.orders") is None


# ---------------------------------------------------------------------------
# Metric with multiple datasets from same datasource
# ---------------------------------------------------------------------------


def test_same_datasource_multiple_datasets_ok(semantic_project_factory, duckdb_backend) -> None:
    """A metric using multiple datasets from the same datasource should work."""

    multi_ds_model = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        orders_alias = ms.entity(name="orders_alias", datasource="warehouse", source=ms.table("orders"))

        @ms.metric(entities=[orders, orders_alias], root_entity=orders, additivity="additive", decomposition=ms.sum(), )
        def combined(t1, t2):
            return t1.amount.sum()
    """)

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": multi_ds_model,
        }
    )

    with _patch_connection_service(project, factory):
        metric_expr = _materialize_metric(project, "sales.combined")
    result = metric_expr.to_pandas()
    # root-only aggregation on orders = 300.0
    assert result == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Project must be loaded before materialize
# ---------------------------------------------------------------------------


def test_materialize_on_unloaded_project_raises(semantic_project_factory) -> None:
    """Calling materialize on an unloaded project should raise."""

    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )

    def factory(ds_id: str):
        return con

    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/datasets.py": _DATASET_AND_METRIC_PY},
        load=False,
    )
    # Not loaded yet
    with _patch_connection_service(project, factory), pytest.raises(SemanticRuntimeError):
        _materialize_dataset(project, "sales.orders")


def test_dataset_materialize_with_sample_size(semantic_project_factory) -> None:
    """Materializer with sample_size should limit dataset rows before caching."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    # Insert enough rows to exceed sample_size
    for i in range(1, 51):
        con.con.execute(f"INSERT INTO orders VALUES ({i}, {i * 10.0}, 'US', '2025-01-01')")

    def factory(ds_id: str):
        return con

    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/datasets.py": _DATASET_AND_METRIC_PY}
    )

    # Without sample_size — full table
    mat_full = Materializer(project, factory)
    table_full = mat_full.entity("sales.orders")
    assert len(table_full.to_pandas()) == 50

    # With sample_size=5 — bounded table
    mat_sampled = Materializer(project, factory, sample_size=5)
    table_sampled = mat_sampled.entity("sales.orders")
    assert len(table_sampled.to_pandas()) == 5

    # Sampled table is cached — subsequent calls return same bounded result
    table_cached = mat_sampled.entity("sales.orders")
    assert table_cached.to_pandas().equals(table_sampled.to_pandas())


def test_metric_materialize_with_sample_size(semantic_project_factory) -> None:
    """Metric callable should aggregate on sampled rows when sample_size is set."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    # Insert 100 rows with amounts 1..100 (total = 5050)
    for i in range(1, 101):
        con.con.execute(f"INSERT INTO orders VALUES ({i}, {float(i)}, 'US', '2025-01-01')")

    def factory(ds_id: str):
        return con

    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/datasets.py": _DATASET_AND_METRIC_PY}
    )

    # Full metric — exact result
    mat_full = Materializer(project, factory)
    value_full = mat_full.metric("sales.total_amount")
    assert value_full.to_pandas() == 5050.0

    # Sampled metric — approximate result (only first 10 rows, sum = 1+2+...+10 = 55)
    mat_sampled = Materializer(project, factory, sample_size=10)
    value_sampled = mat_sampled.metric("sales.total_amount")
    assert value_sampled.to_pandas() == 55.0


def test_metric_callable_name_error_adds_import_hint(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', )\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum() + ibis.literal(0)\n"
            ),
        }
    )
    with (
        _patch_connection_service(project, backend_factory),
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_metric(project, "sales.revenue")
    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "NameError" in exc_info.value.message
    assert "import ibis" in exc_info.value.message
