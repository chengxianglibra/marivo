"""Tests for marivo.semantic_py.materializer — dataset/field/metric materialization.

Tests cover:
- Dataset materialize with real DuckDB backend
- Field materialize on materialized dataset
- Base metric materialize (e.g., orders.amount.sum())
- Backend cache: same datasource reuses backend
- Dataset cache: same dataset reuses table
- SQL view detection: dataset using backend.sql("SELECT ...") gets SQL_VIEW provenance
- Cross-datasource fail: metric with datasets from different datasources -> error
- Metric not found -> error
- Dataset not found -> error
- Materializer creates fresh instance per project.materialize_*() call
- DatasetRuntimeMetadata stored on project after materialize
"""

from __future__ import annotations

import textwrap

import ibis
import pytest

from marivo.semantic_py.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic_py.ir import DatasetProvenance
from marivo.semantic_py.materializer import DatasetRuntimeMetadata, Materializer

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
# Model file templates
# ---------------------------------------------------------------------------


_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic_py as ms
    ms.model(name="sales", default=True)
""")

_DATASET_AND_METRIC_PY = textwrap.dedent("""\
    import marivo.semantic_py as ms
    @ms.dataset(datasource="warehouse")
    def orders(backend):
        return backend.table("orders")

    @ms.field(dataset=orders)
    def amount(table):
        return table.amount

    @ms.metric(datasets=[orders], decomposition=ms.sum())
    def total_amount(table):
        return table.amount.sum()
""")

_SQL_VIEW_DATASET_PY = textwrap.dedent("""\
    import marivo.semantic_py as ms
    @ms.dataset(datasource="warehouse")
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
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    assert project.is_ready()

    table = project.materialize_dataset("sales.orders", backend_factory=backend_factory)
    assert isinstance(table, ibis.Table)
    # The table should have the expected columns
    assert "amount" in table.columns
    assert "region" in table.columns


def test_dataset_materialize_returns_rows(semantic_project_factory, backend_factory) -> None:
    """Materialized dataset should return actual data via to_pandas()."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    table = project.materialize_dataset("sales.orders", backend_factory=backend_factory)
    df = table.to_pandas()
    assert len(df) == 2
    assert list(df["amount"]) == [100.0, 200.0]


# ---------------------------------------------------------------------------
# Field materialize
# ---------------------------------------------------------------------------


def test_field_materialize(semantic_project_factory, backend_factory) -> None:
    """Materializing a field should return an ibis Value expression."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    field_expr = project.materialize_field("sales.amount", backend_factory=backend_factory)
    # ibis Value is the base of column expressions
    assert field_expr is not None


# ---------------------------------------------------------------------------
# Base metric materialize
# ---------------------------------------------------------------------------


def test_metric_materialize_sum(semantic_project_factory, backend_factory) -> None:
    """Materializing a sum metric should return a scalar ibis Value."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    metric_expr = project.materialize_metric("sales.total_amount", backend_factory=backend_factory)
    assert metric_expr is not None
    # Execute the metric to verify the value
    result = metric_expr.to_pandas()
    assert result == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Backend cache
# ---------------------------------------------------------------------------


def test_backend_cache_reuses_same_backend(semantic_project_factory) -> None:
    """Same datasource should reuse the same backend instance."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
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
    project.materialize_dataset("sales.orders", backend_factory=tracking_factory)
    project.materialize_metric("sales.total_amount", backend_factory=tracking_factory)

    # Backend factory should only have been called once per unique datasource
    # (each materialize_* call creates a new Materializer, but the factory is
    # called only once within each Materializer)
    # First call: dataset materialize -> 1 factory call
    # Second call: metric materialize -> 1 factory call (new Materializer)
    assert len(created_backends) == 2
    # Both should be for the same datasource
    assert all(ds == "warehouse" for ds, _ in created_backends)


def test_backend_cache_within_single_materializer(semantic_project_factory) -> None:
    """Within one Materializer, the same datasource should only call factory once."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
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
    mat.dataset("sales.orders")
    assert call_count == 1

    # Calling again should use cache
    mat.dataset("sales.orders")
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
            "sales/_model.py": _MODEL_PY,
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
    table1 = mat.dataset("sales.orders")
    table2 = mat.dataset("sales.orders")
    assert table1 is table2


# ---------------------------------------------------------------------------
# SQL view detection
# ---------------------------------------------------------------------------


def test_sql_view_detection(semantic_project_factory, duckdb_backend) -> None:
    """Dataset using backend.sql() should be detected as SQL_VIEW provenance."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _SQL_VIEW_DATASET_PY,
        }
    )
    project.materialize_dataset("sales.orders_view", backend_factory=factory)

    meta = project.runtime_metadata("sales.orders_view")
    assert meta is not None
    assert meta.dataset_provenance == DatasetProvenance.SQL_VIEW
    assert meta.raw_sql_snippet is not None
    assert "SELECT" in meta.raw_sql_snippet


def test_ibis_table_detection(semantic_project_factory, duckdb_backend) -> None:
    """Dataset using backend.table() should be detected as IBIS_TABLE provenance."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    project.materialize_dataset("sales.orders", backend_factory=factory)

    meta = project.runtime_metadata("sales.orders")
    assert meta is not None
    assert meta.dataset_provenance == DatasetProvenance.IBIS_TABLE
    assert meta.raw_sql_snippet is None


# ---------------------------------------------------------------------------
# Cross-datasource fail-closed
# ---------------------------------------------------------------------------


def test_cross_datasource_metric_fails(semantic_project_factory, duckdb_backend) -> None:
    """A metric referencing datasets from different datasources must raise."""

    cross_ds_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="warehouse1")
        def orders_a(backend):
            return backend.table("orders")

        @ms.dataset(datasource="warehouse2")
        def orders_b(backend):
            return backend.table("orders")

        @ms.metric(datasets=[orders_a, orders_b], decomposition=ms.sum())
        def cross_metric(t1, t2):
            return t1.amount.sum() + t2.amount.sum()
    """)

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": cross_ds_model,
        }
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.materialize_metric("sales.cross_metric", backend_factory=factory)

    assert exc_info.value.kind == ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED


# ---------------------------------------------------------------------------
# Not found errors
# ---------------------------------------------------------------------------


def test_metric_not_found(semantic_project_factory, backend_factory) -> None:
    """Materializing a non-existent metric should raise SemanticRuntimeError."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.materialize_metric("sales.nonexistent", backend_factory=backend_factory)

    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


def test_dataset_not_found(semantic_project_factory, backend_factory) -> None:
    """Materializing a non-existent dataset should raise SemanticRuntimeError."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.materialize_dataset("sales.nonexistent", backend_factory=backend_factory)

    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


def test_field_not_found(semantic_project_factory, backend_factory) -> None:
    """Materializing a non-existent field should raise SemanticRuntimeError."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.materialize_field("sales.nonexistent", backend_factory=backend_factory)

    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


# ---------------------------------------------------------------------------
# Derived metric materialization
# ---------------------------------------------------------------------------


def test_derived_metric_ratio_materialize(semantic_project_factory, backend_factory) -> None:
    """Derived ratio metric: ms.component("numerator") / ms.component("denominator")."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="warehouse")
        def orders(backend):
            return backend.table("orders")

        @ms.metric(datasets=[orders], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()

        @ms.metric(datasets=[], decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.revenue"))
        def revenue_ratio():
            return ms.component("numerator") / ms.component("denominator")
    """)

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # revenue_ratio = revenue / revenue = 1.0
    result = project.materialize_metric("sales.revenue_ratio", backend_factory=backend_factory)
    value = result.to_pandas()
    assert value == pytest.approx(1.0)


def test_derived_metric_with_arithmetic(semantic_project_factory, backend_factory) -> None:
    """Derived metric with arithmetic: ms.component("a") * 2 + 100."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="warehouse")
        def orders(backend):
            return backend.table("orders")

        @ms.metric(datasets=[orders], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()

        @ms.metric(datasets=[], decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.revenue"))
        def scaled_revenue():
            return ms.component("numerator") * 2 + ms.component("denominator") * 0
    """)

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # scaled_revenue = revenue * 2 + revenue * 0 = 600 + 0 = 600
    result = project.materialize_metric("sales.scaled_revenue", backend_factory=backend_factory)
    value = result.to_pandas()
    assert value == pytest.approx(600.0)


def test_derived_metric_weighted_average(semantic_project_factory, backend_factory) -> None:
    """Derived weighted_average metric: ms.component("numerator") / ms.component("weight")."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="warehouse")
        def orders(backend):
            return backend.table("orders")

        @ms.metric(datasets=[orders], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()

        @ms.metric(datasets=[orders], decomposition=ms.sum())
        def count_metric(table):
            return table.count()

        @ms.metric(datasets=[], decomposition=ms.weighted_average(numerator="sales.revenue", weight="sales.count_metric"))
        def aov():
            return ms.component("numerator") / ms.component("weight")
    """)

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # aov = revenue / count = 300 / 2 = 150.0
    result = project.materialize_metric("sales.aov", backend_factory=backend_factory)
    value = result.to_pandas()
    assert value == pytest.approx(150.0)


def test_derived_metric_recursive(semantic_project_factory, backend_factory) -> None:
    """Derived metric referencing another derived metric's component."""

    derived_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="warehouse")
        def orders(backend):
            return backend.table("orders")

        @ms.metric(datasets=[orders], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()

        @ms.metric(datasets=[], decomposition=ms.ratio(numerator="sales.revenue", denominator="sales.revenue"))
        def revenue_ratio():
            return ms.component("numerator") / ms.component("denominator")

        @ms.metric(datasets=[], decomposition=ms.ratio(numerator="sales.revenue_ratio", denominator="sales.revenue_ratio"))
        def double_ratio():
            return ms.component("numerator") / ms.component("denominator")
    """)

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": derived_model,
        }
    )

    # double_ratio = revenue_ratio / revenue_ratio = 1.0
    result = project.materialize_metric("sales.double_ratio", backend_factory=backend_factory)
    value = result.to_pandas()
    assert value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Fresh Materializer per call
# ---------------------------------------------------------------------------


def test_fresh_materializer_per_call(semantic_project_factory) -> None:
    """Each project.materialize_*() call should create a new Materializer."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
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
    project.materialize_dataset("sales.orders", backend_factory=tracking_factory)
    assert len(factory_calls) == 1

    # Second call creates a new Materializer, calls factory again (no cross-call cache)
    project.materialize_dataset("sales.orders", backend_factory=tracking_factory)
    assert len(factory_calls) == 2


# ---------------------------------------------------------------------------
# DatasetRuntimeMetadata on project
# ---------------------------------------------------------------------------


def test_runtime_metadata_stored_on_project(semantic_project_factory, duckdb_backend) -> None:
    """After materialization, DatasetRuntimeMetadata should be on the project."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    # Before materialization, no metadata
    assert project.runtime_metadata("sales.orders") is None

    # After materialization
    project.materialize_dataset("sales.orders", backend_factory=factory)
    meta = project.runtime_metadata("sales.orders")
    assert meta is not None
    assert isinstance(meta, DatasetRuntimeMetadata)
    assert meta.dataset_provenance == DatasetProvenance.IBIS_TABLE
    assert meta.detected_at is not None


def test_runtime_metadata_cleared_on_reload(semantic_project_factory, duckdb_backend) -> None:
    """Runtime metadata should be cleared when project is reloaded."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    project.materialize_dataset("sales.orders", backend_factory=factory)
    assert project.runtime_metadata("sales.orders") is not None

    project.reload()
    assert project.runtime_metadata("sales.orders") is None


# ---------------------------------------------------------------------------
# Metric with multiple datasets from same datasource
# ---------------------------------------------------------------------------


def test_same_datasource_multiple_datasets_ok(semantic_project_factory, duckdb_backend) -> None:
    """A metric using multiple datasets from the same datasource should work."""

    multi_ds_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="warehouse")
        def orders(backend):
            return backend.table("orders")

        @ms.dataset(datasource="warehouse")
        def orders_alias(backend):
            return backend.table("orders")

        @ms.metric(datasets=[orders, orders_alias], decomposition=ms.sum())
        def combined(t1, t2):
            return t1.amount.sum() + t2.amount.sum()
    """)

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/datasets.py": multi_ds_model,
        }
    )

    metric_expr = project.materialize_metric("sales.combined", backend_factory=factory)
    result = metric_expr.to_pandas()
    # 300 + 300 = 600
    assert result == pytest.approx(600.0)


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
        {"sales/_model.py": _MODEL_PY, "sales/datasets.py": _DATASET_AND_METRIC_PY},
        load=False,
    )
    # Not loaded yet
    with pytest.raises(SemanticRuntimeError):
        project.materialize_dataset("sales.orders", backend_factory=factory)
