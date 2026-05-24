"""apply_window_to_dataset / apply_slice_to_dataset / execute against ibis."""

import ibis
import pytest

import marivo.semantic_py as ms
from marivo.analysis_py.errors import BackendError, SliceInvalidError, WindowInvalidError
from marivo.analysis_py.executor.backend import BackendCache
from marivo.analysis_py.executor.runner import (
    ExecutionResult,
    apply_slice_to_dataset,
    apply_window_to_dataset,
    execute,
)
from marivo.semantic_py import SemanticProject
from marivo.semantic_py.registry import use_registry


def _project_with_dataset(tmp_path):
    project = SemanticProject(root=str(tmp_path / "semantic"))
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend):
            return backend.table("orders")

        @ms.time_field(dataset="orders", data_type="date", granularity="day")
        def order_date(orders):
            return orders.created_at.cast("date")

        @ms.field(dataset="orders")
        def region(orders):
            return orders.region.upper()

    project.registry.state = "ready"
    return project


def _seed_backend():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE, region VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north'),"
        "(2, DATE '2026-08-01', 20.0, 'south')"
    )
    return con


def test_apply_window_filters_rows(tmp_path):
    project = _project_with_dataset(tmp_path)
    con = _seed_backend()
    dataset_ir = project.registry.models["sales"].datasets["orders"]
    filtered = apply_window_to_dataset(
        dataset_ir.fn(con),
        {"start": "2026-07-01", "end": "2026-07-31"},
        dataset_ir=dataset_ir,
    )
    df = filtered.execute()
    assert len(df) == 1
    assert df.iloc[0]["order_id"] == 1


def test_apply_slice_filters_by_declared_field(tmp_path):
    project = _project_with_dataset(tmp_path)
    con = _seed_backend()
    dataset_ir = project.registry.models["sales"].datasets["orders"]
    filtered = apply_slice_to_dataset(
        dataset_ir.fn(con), {"region": "NORTH"}, dataset_ir=dataset_ir
    )
    df = filtered.execute()
    assert len(df) == 1
    assert df.iloc[0]["region"] == "north"


def test_apply_slice_unknown_field_raises(tmp_path):
    project = _project_with_dataset(tmp_path)
    con = _seed_backend()
    dataset_ir = project.registry.models["sales"].datasets["orders"]
    with pytest.raises(SliceInvalidError):
        apply_slice_to_dataset(dataset_ir.fn(con), {"bogus_field": 1}, dataset_ir=dataset_ir)


def test_apply_window_dataset_without_time_field_raises(tmp_path):
    project = SemanticProject(root=str(tmp_path / "semantic2"))
    with use_registry(project.registry):
        ms.model(name="x")

        @ms.datasource(name="w")
        def w() -> None: ...

        @ms.dataset(name="t", datasource=w)
        def t(backend):
            return backend.table("t")

    project.registry.state = "ready"
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE t (x INTEGER)")
    dataset_ir = project.registry.models["x"].datasets["t"]
    with pytest.raises(WindowInvalidError):
        apply_window_to_dataset(
            dataset_ir.fn(con),
            {"start": "2026-01-01", "end": "2026-12-31"},
            dataset_ir=dataset_ir,
        )


def test_execute_returns_dataframe_with_timing():
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1),(2),(3);")
    cache = BackendCache(lambda name: con)
    result = execute(con.table("t").x.sum(), datasource_name="warehouse", cache=cache)
    assert isinstance(result, ExecutionResult)
    assert result.row_count >= 1
    assert result.duration_ms >= 0


def test_execute_wraps_backend_errors():
    class FakeBackend:
        def execute(self, expr):
            raise RuntimeError("backend exploded")

    cache = BackendCache(lambda name: FakeBackend())
    with pytest.raises(BackendError):
        execute(object(), datasource_name="warehouse", cache=cache)
