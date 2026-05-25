from __future__ import annotations

from typing import Any

import pytest

import marivo.semantic_py as ms
from marivo.semantic_py import reader
from marivo.semantic_py.errors import PySemanticNotFoundError
from marivo.semantic_py.registry import SemanticProject, use_registry


def _project() -> SemanticProject:
    project = SemanticProject(root=":phase2_introspection_test:")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(
            name="tiny_orders",
            backend_type="duckdb",
            description="Tiny order source",
        )
        def tiny_orders() -> None: ...

        @ms.datasource(name="archive_orders", backend_type="duckdb")
        def archive_orders() -> None: ...

        @ms.dataset(
            name="orders",
            datasource=tiny_orders,
            primary_key=["order_id"],
            description="Order facts",
        )
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="archive", datasource=archive_orders)
        def archive(backend: Any) -> Any:
            return backend.table("archive")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders: Any) -> Any:
            return orders.amount.sum()

        @ms.metric(decomposition=ms.sum())
        def orders_count(orders: Any) -> Any:
            return orders.count()

        @ms.metric(decomposition=ms.ratio(numerator=revenue, denominator=orders_count))
        def aov() -> int:
            return 0

    project.registry.state = "ready"
    return project


def test_reader_lists_datasources_datasets_and_metrics() -> None:
    project = _project()
    expected_metrics = ["sales.aov", "sales.orders_count", "sales.revenue"]

    assert reader.list_datasources(project) == [
        "sales.archive_orders",
        "sales.tiny_orders",
    ]
    assert reader.list_datasets(project=project) == ["sales.archive", "sales.orders"]
    assert reader.list_datasets(model="sales", project=project) == [
        "sales.archive",
        "sales.orders",
    ]
    assert reader.list_datasets(model="unknown", project=project) == []
    assert reader.list_metrics(project) == expected_metrics
    assert reader.list_metrics(project=project) == expected_metrics
    assert ms.list_metrics(project=project) == expected_metrics
    assert ms.list_metrics(dataset="sales.orders", project=project) == expected_metrics
    assert "sales.aov" in ms.list_metrics(dataset="sales.orders", project=project)
    assert "sales.aov" not in ms.list_metrics(dataset="sales.archive", project=project)
    assert ms.list_metrics(dataset="sales.archive", project=project) == []
    assert ms.list_metrics(dataset="sales.unknown", project=project) == []
    assert ms.list_metrics(dataset="malformed", project=project) == []


def test_reader_describe_resolves_datasource_dataset_and_metric() -> None:
    project = _project()

    assert reader.describe("sales.revenue", project=project) == {
        "kind": "metric",
        "model": "sales",
        "name": "revenue",
        "dataset": "orders",
        "description": None,
    }
    assert reader.describe("sales.orders", project=project) == {
        "kind": "dataset",
        "model": "sales",
        "name": "orders",
        "datasource": "tiny_orders",
        "primary_key": ["order_id"],
        "description": "Order facts",
    }
    assert reader.describe("sales.tiny_orders", project=project) == {
        "kind": "datasource",
        "model": "sales",
        "name": "tiny_orders",
        "backend_type": "duckdb",
        "description": "Tiny order source",
    }


def test_reader_describe_rejects_ambiguous_leaf_names() -> None:
    project = SemanticProject(root=":phase2_introspection_ambiguous_test:")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="shared", backend_type="duckdb")
        def shared_source() -> None: ...

        @ms.dataset(name="shared", datasource=shared_source)
        def shared_dataset(backend: Any) -> Any:
            return backend.table("shared")

    project.registry.state = "ready"

    with pytest.raises(PySemanticNotFoundError) as exc_info:
        reader.describe("sales.shared", project=project)

    assert exc_info.value.entity == "ambiguous semantic object"


def test_reader_describe_unknown_name_raises_not_found() -> None:
    with pytest.raises(PySemanticNotFoundError):
        reader.describe("sales.unknown", project=_project())
