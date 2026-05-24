from __future__ import annotations

import asyncio
from typing import Any

import pytest

import marivo.semantic_py as ms
from marivo.semantic_py.errors import SemanticDecoratorError
from marivo.semantic_py.registry import SemanticProject, active_registry, use_registry
from marivo.semantic_py.testing import scoped_project


def test_symbol_ref_is_available_for_forward_references() -> None:
    ref = ms.ref("metric.total_users")

    assert ref.kind == "metric"
    assert ref.name == "total_users"


def test_use_registry_scopes_active_project_registry() -> None:
    previous = active_registry()
    project = SemanticProject(root="workspace")

    with use_registry(project.registry) as registry:
        assert registry is project.registry
        assert active_registry() is registry

    assert active_registry() is previous


def test_use_registry_restores_active_registry_after_exception() -> None:
    previous = active_registry()
    project = SemanticProject(root="workspace")

    try:
        with use_registry(project.registry):
            assert active_registry() is project.registry
            raise RuntimeError("boom")
    except RuntimeError as exc:
        assert str(exc) == "boom"

    assert active_registry() is previous


def test_use_registry_is_context_local_across_async_tasks() -> None:
    async def run_tasks() -> None:
        project_a = SemanticProject(root="workspace-a")
        project_b = SemanticProject(root="workspace-b")
        entered_a = asyncio.Event()
        entered_b = asyncio.Event()
        release_b = asyncio.Event()

        async def task_a() -> None:
            with use_registry(project_a.registry):
                entered_a.set()
                await entered_b.wait()
                assert active_registry() is project_a.registry
                release_b.set()

        async def task_b() -> None:
            await entered_a.wait()
            with use_registry(project_b.registry):
                entered_b.set()
                await release_b.wait()

        await asyncio.gather(task_a(), task_b())

    asyncio.run(run_tasks())


def test_decorators_register_complete_model() -> None:
    project = SemanticProject(root="/tmp/sales")

    with use_registry(project.registry):
        ms.model(name="sales", description="Sales model")

        @ms.datasource(name="warehouse_main", backend_type="duckdb")
        def warehouse_main() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse_main, primary_key=["order_id"])
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.time_field(dataset="orders", data_type="date", granularity="day")
        def order_date(orders: Any) -> Any:
            return orders.created_at.cast("date")

        @ms.field(dataset="orders", label="dimension")
        def region(orders: Any) -> Any:
            return orders.region.upper()

        @ms.metric(
            decomposition=ms.sum(),
            source_sql="sum(amount)",
            source_dialect="trino",
            source_document="kb://sales/revenue",
        )
        def revenue(orders: Any) -> Any:
            return orders.amount.sum()

        @ms.relationship(
            name="orders_to_users",
            from_="orders",
            to="users",
            from_columns=["user_id"],
            to_columns=["user_id"],
        )
        def orders_to_users() -> None: ...

    model = project.registry.models["sales"]
    assert model.datasources["warehouse_main"].backend_type == "duckdb"
    assert model.datasets["orders"].datasource_name == "warehouse_main"
    assert model.datasets["orders"].fn is orders
    assert model.datasets["orders"].fields["order_date"].is_time is True
    assert model.datasets["orders"].fields["region"].label == "dimension"
    assert model.metrics["revenue"].source is not None
    assert model.metrics["revenue"].source.sql == "sum(amount)"
    assert model.metrics["revenue"].fn is revenue
    assert model.relationships["orders_to_users"].from_dataset == "orders"


def test_public_surface_exports_core_builders() -> None:
    assert callable(ms.model)
    assert callable(ms.datasource)
    assert callable(ms.dataset)
    assert callable(ms.metric)
    assert callable(ms.sum)
    assert callable(ms.ratio)
    assert callable(ms.weighted_average)
    assert callable(ms.ref)


def test_project_registry_does_not_share_models_between_projects() -> None:
    first = SemanticProject(root="/tmp/first")
    second = SemanticProject(root="/tmp/second")

    with use_registry(first.registry):
        ms.model(name="sales")

    with use_registry(second.registry):
        ms.model(name="marketing")

    assert sorted(first.registry.models) == ["sales"]
    assert sorted(second.registry.models) == ["marketing"]


def test_scoped_project_restores_registry_after_exit() -> None:
    outer = SemanticProject(root="/tmp/outer")
    with use_registry(outer.registry):
        ms.model(name="outer")
        with scoped_project(root="/tmp/inner") as inner:
            ms.model(name="inner")
            assert sorted(inner.registry.models) == ["inner"]
        assert sorted(outer.registry.models) == ["outer"]


def test_scoped_project_restores_registry_after_exception() -> None:
    outer = SemanticProject(root="/tmp/outer-exception")
    with use_registry(outer.registry):
        ms.model(name="outer")

        with (
            pytest.raises(RuntimeError, match="boom"),
            scoped_project(root="/tmp/inner-exception") as inner,
        ):
            ms.model(name="inner")
            assert sorted(inner.registry.models) == ["inner"]
            raise RuntimeError("boom")

        assert active_registry() is outer.registry
        assert sorted(outer.registry.models) == ["outer"]


def test_decorators_reject_async_metric_functions() -> None:
    project = SemanticProject(root="/tmp/async")

    with (
        use_registry(project.registry),
        pytest.raises(SemanticDecoratorError, match="AstNodeForbidden"),
    ):
        ms.model(name="sales")

        @ms.metric(decomposition=ms.sum())
        async def revenue(orders: Any) -> Any:
            return orders.amount.sum()


def test_dataset_decorator_completes_forward_placeholder_dataset() -> None:
    project = SemanticProject(root="/tmp/forward")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.field(dataset="orders", label="dimension")
        def region(orders: Any) -> Any:
            return orders.region

        @ms.datasource(name="warehouse_main", backend_type="duckdb")
        def warehouse_main() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse_main, primary_key=["order_id"])
        def orders(backend: Any) -> Any:
            return backend.table("orders")

    dataset = project.registry.models["sales"].datasets["orders"]
    assert dataset.datasource_name == "warehouse_main"
    assert dataset.primary_key == ["order_id"]
    assert dataset.fn is orders
    assert dataset.fields["region"].fn is region


def test_duplicate_model_names_are_rejected() -> None:
    project = SemanticProject(root="/tmp/duplicate")

    with use_registry(project.registry):
        ms.model(name="sales")
        with pytest.raises(ValueError, match="already registered"):
            ms.model(name="sales")


def test_decorators_require_registered_model() -> None:
    project = SemanticProject(root="/tmp/no-model")

    with (
        use_registry(project.registry),
        pytest.raises(ValueError, match="no active semantic model"),
    ):

        @ms.datasource(name="warehouse_main", backend_type="duckdb")
        def warehouse_main() -> None: ...


def test_decorators_require_unambiguous_current_model() -> None:
    project = SemanticProject(root="/tmp/ambiguous")

    with use_registry(project.registry):
        ms.model(name="sales")
        ms.model(name="marketing")

        with pytest.raises(ValueError, match="multiple models"):

            @ms.datasource(name="warehouse_main", backend_type="duckdb")
            def warehouse_main() -> None: ...
