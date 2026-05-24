from __future__ import annotations

from typing import Any

import ibis
import pytest

import marivo.semantic_py as ms
from marivo.semantic_py import reader
from marivo.semantic_py.errors import PySemanticNotFound, SemanticRuntimeError
from marivo.semantic_py.registry import SemanticProject, use_registry


def _create_amount_table(con: Any, name: str, amounts: list[int]) -> None:
    con.raw_sql(f"CREATE TABLE {name} (amount INTEGER)")
    values = ", ".join(f"({amount})" for amount in amounts)
    con.raw_sql(f"INSERT INTO {name} VALUES {values}")


def _project() -> SemanticProject:
    project = SemanticProject(root="/tmp/materialize")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.field(dataset="orders")
        def broken_amount(orders: Any) -> Any:
            raise RuntimeError("field boom")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders: Any) -> Any:
            return orders.amount.sum()

    project.registry.state = "ready"
    return project


def _two_dataset_project() -> SemanticProject:
    project = SemanticProject(root="/tmp/materialize-two-datasets")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="refunds", datasource=warehouse)
        def refunds(backend: Any) -> Any:
            return backend.table("refunds")

        @ms.metric(decomposition=ms.sum())
        def net_revenue(orders: Any, refunds: Any) -> Any:
            return (
                orders.cross_join(refunds)
                .mutate(net_revenue=orders.amount - refunds.amount)
                .net_revenue.sum()
            )

    project.registry.state = "ready"
    return project


def test_materialize_metric_returns_ibis_expression() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    _create_amount_table(con, "orders", [10, 20, 30])

    expr = reader.materialize_metric(
        project=project,
        model="sales",
        metric="revenue",
        backend_factory=lambda datasource_name: con,
    )

    assert expr.execute() == 60


def test_backend_factory_is_called_once_per_datasource() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    _create_amount_table(con, "orders", [7])
    calls: list[str] = []

    def factory(datasource_name: str) -> Any:
        calls.append(datasource_name)
        return con

    reader.materialize_metric(
        project=project,
        model="sales",
        metric="revenue",
        backend_factory=factory,
    ).execute()

    assert calls == ["warehouse"]


def test_materialize_dataset_wraps_backend_factory_failure() -> None:
    project = _project()

    def factory(datasource_name: str) -> Any:
        raise RuntimeError(f"{datasource_name} unavailable")

    with pytest.raises(SemanticRuntimeError) as exc_info:
        reader.materialize_dataset(
            project=project,
            model="sales",
            dataset="orders",
            backend_factory=factory,
        )

    assert exc_info.value.kind == "DatasetMaterializationFailed"
    assert exc_info.value.refs == ["dataset:sales.orders"]


def test_materialize_field_wraps_field_function_failure() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    _create_amount_table(con, "orders", [10])

    with pytest.raises(SemanticRuntimeError) as exc_info:
        reader.materialize_field(
            project=project,
            model="sales",
            dataset="orders",
            field="broken_amount",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.kind == "FieldMaterializationFailed"
    assert exc_info.value.refs == ["field:sales.orders.broken_amount"]


def test_materialization_missing_entities_raise_not_found() -> None:
    project = _project()

    with pytest.raises(PySemanticNotFound):
        reader.materialize_dataset(
            project=project,
            model="missing",
            dataset="orders",
            backend_factory=lambda datasource_name: None,
        )

    with pytest.raises(PySemanticNotFound):
        reader.materialize_dataset(
            project=project,
            model="sales",
            dataset="missing",
            backend_factory=lambda datasource_name: None,
        )

    with pytest.raises(PySemanticNotFound):
        reader.materialize_metric(
            project=project,
            model="sales",
            metric="missing",
            backend_factory=lambda datasource_name: None,
        )

    with pytest.raises(PySemanticNotFound):
        reader.materialize_field(
            project=project,
            model="sales",
            dataset="orders",
            field="missing",
            backend_factory=lambda datasource_name: None,
        )


def test_metric_backend_cache_is_shared_across_same_datasource_datasets() -> None:
    project = _two_dataset_project()
    con = ibis.duckdb.connect()
    _create_amount_table(con, "orders", [30])
    _create_amount_table(con, "refunds", [5])
    calls: list[str] = []

    def factory(datasource_name: str) -> Any:
        calls.append(datasource_name)
        return con

    expr = reader.materialize_metric(
        project=project,
        model="sales",
        metric="net_revenue",
        backend_factory=factory,
    )

    assert expr.execute() == 25
    assert calls == ["warehouse"]
