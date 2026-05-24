from __future__ import annotations

from typing import Any

import pytest

import marivo.semantic_py as ms
from marivo.semantic_py.errors import SemanticDecoratorError, SemanticLoadError
from marivo.semantic_py.registry import SemanticProject, use_registry
from marivo.semantic_py.validator import validate_all


def test_validation_aggregates_missing_references() -> None:
    project = SemanticProject(root="/tmp/invalid")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.metric(
            decomposition=ms.ratio(numerator=ms.ref("metric.a"), denominator=ms.ref("metric.b"))
        )
        def conversion_rate(orders: Any) -> Any:
            return orders.converted.sum() / orders.users.sum()

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    kinds = {error.kind for error in exc_info.value.errors}
    assert "MetricDatasetMissing" in kinds
    assert "MetricReferenceMissing" in kinds


def test_time_hour_requires_prefix() -> None:
    project = SemanticProject(root="/tmp/invalid-time")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.time_field(dataset="orders", data_type="integer", granularity="hour", format="hh")
        def order_hour(orders: Any) -> Any:
            return orders.log_hour

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert exc_info.value.errors[0].kind == "TimeFieldPrefixMissing"


def test_time_hour_prefix_must_reference_existing_time_field() -> None:
    project = SemanticProject(root="/tmp/invalid-time-prefix")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.time_field(
            dataset="orders",
            data_type="integer",
            granularity="hour",
            required_prefix=ms.ref("time_field.nope"),
        )
        def order_hour(orders: Any) -> Any:
            return orders.log_hour

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert {error.kind for error in exc_info.value.errors} == {"TimeFieldPrefixMissing"}


def test_time_hour_prefix_decorated_ref_must_belong_to_same_dataset() -> None:
    project = SemanticProject(root="/tmp/invalid-time-prefix-dataset")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="sessions", datasource=warehouse)
        def sessions(backend: Any) -> Any:
            return backend.table("sessions")

        @ms.time_field(dataset="sessions", data_type="date", granularity="day", name="date")
        def session_date(sessions: Any) -> Any:
            return sessions.session_date

        with pytest.raises(ValueError, match="belongs to dataset"):

            @ms.time_field(
                dataset="orders",
                data_type="integer",
                granularity="hour",
                required_prefix=session_date,
            )
            def order_hour(orders: Any) -> Any:
                return orders.log_hour


def test_time_hour_prefix_decorated_ref_must_belong_to_same_model() -> None:
    project = SemanticProject(root="/tmp/invalid-time-prefix-model")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="sales_warehouse")
        def sales_warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=sales_warehouse)
        def sales_orders(backend: Any) -> Any:
            return backend.table("sales_orders")

        @ms.time_field(dataset="orders", data_type="date", granularity="day", name="date")
        def sales_order_date(orders: Any) -> Any:
            return orders.order_date

    marketing = SemanticProject(root="/tmp/invalid-time-prefix-model-marketing")
    with use_registry(marketing.registry):
        ms.model(name="marketing")

        @ms.datasource(name="marketing_warehouse")
        def marketing_warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=marketing_warehouse)
        def marketing_orders(backend: Any) -> Any:
            return backend.table("marketing_orders")

        with pytest.raises(ValueError, match="belongs to model"):

            @ms.time_field(
                dataset="orders",
                data_type="integer",
                granularity="hour",
                required_prefix=sales_order_date,
            )
            def marketing_order_hour(orders: Any) -> Any:
                return orders.log_hour


def test_decorated_dataset_ref_must_belong_to_same_model() -> None:
    sales = SemanticProject(root="/tmp/dataset-ref-sales")
    with use_registry(sales.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def sales_orders(backend: Any) -> Any:
            return backend.table("sales_orders")

    marketing = SemanticProject(root="/tmp/dataset-ref-marketing")
    with use_registry(marketing.registry):
        ms.model(name="marketing")

        @ms.datasource(name="warehouse")
        def marketing_warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=marketing_warehouse)
        def marketing_orders(backend: Any) -> Any:
            return backend.table("marketing_orders")

        with pytest.raises(ValueError, match="belongs to model"):

            @ms.field(dataset=sales_orders)
            def region(orders: Any) -> Any:
                return orders.region


def test_decorated_datasource_ref_must_belong_to_same_model() -> None:
    sales = SemanticProject(root="/tmp/datasource-ref-sales")
    with use_registry(sales.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def sales_warehouse() -> None: ...

    marketing = SemanticProject(root="/tmp/datasource-ref-marketing")
    with use_registry(marketing.registry):
        ms.model(name="marketing")

        with pytest.raises(ValueError, match="belongs to model"):

            @ms.dataset(name="orders", datasource=sales_warehouse)
            def marketing_orders(backend: Any) -> Any:
                return backend.table("marketing_orders")


def test_dataset_datasource_must_exist() -> None:
    project = SemanticProject(root="/tmp/invalid-datasource")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.dataset(name="orders", datasource=ms.ref("datasource.missing"))
        def orders(backend: Any) -> Any:
            return backend.table("orders")

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert {error.kind for error in exc_info.value.errors} == {"DatasetDatasourceMissing"}


def test_placeholder_dataset_without_datasource_fails_validation() -> None:
    project = SemanticProject(root="/tmp/placeholder-dataset")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.field(dataset="orders")
        def region(orders: Any) -> Any:
            return orders.region

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert {error.kind for error in exc_info.value.errors} == {"DatasetDatasourceMissing"}


def test_decomposition_components_must_be_metric_refs() -> None:
    project = SemanticProject(root="/tmp/invalid-decomposition-ref")

    with use_registry(project.registry):
        ms.model(name="sales")

        with pytest.raises(ValueError, match="metric ref"):

            @ms.metric(
                decomposition=ms.ratio(
                    numerator=ms.ref("field.clicks"), denominator=ms.ref("field.views")
                )
            )
            def conversion_rate(orders: Any) -> Any:
                return orders.clicks.sum() / orders.views.sum()


def test_decorated_metric_ref_must_belong_to_same_model() -> None:
    sales = SemanticProject(root="/tmp/metric-ref-sales")
    with use_registry(sales.registry):
        ms.model(name="sales")

        @ms.metric(decomposition=ms.sum())
        def sales_revenue(orders: Any) -> Any:
            return orders.amount.sum()

    marketing = SemanticProject(root="/tmp/metric-ref-marketing")
    with use_registry(marketing.registry):
        ms.model(name="marketing")

        @ms.metric(decomposition=ms.sum())
        def marketing_revenue(orders: Any) -> Any:
            return orders.amount.sum()

        with pytest.raises(ValueError, match="belongs to model"):

            @ms.metric(
                decomposition=ms.ratio(
                    numerator=sales_revenue,
                    denominator=marketing_revenue,
                )
            )
            def revenue_share(orders: Any) -> Any:
                return orders.amount.sum() / orders.total_amount.sum()


def test_relationship_columns_must_exist_on_endpoint_datasets() -> None:
    project = SemanticProject(root="/tmp/invalid-relationship")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="users", datasource=warehouse)
        def users(backend: Any) -> Any:
            return backend.table("users")

        @ms.field(dataset="orders", name="user_id")
        def orders_user_id(orders: Any) -> Any:
            return orders.user_id

        @ms.field(dataset="users", name="user_id")
        def users_user_id(users: Any) -> Any:
            return users.user_id

        @ms.relationship(
            name="orders_to_users",
            from_="orders",
            to="users",
            from_columns=["user_id", "missing_order_column"],
            to_columns=["user_id", "missing_user_column"],
        )
        def orders_to_users() -> None: ...

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    errors = exc_info.value.errors
    missing_column_errors = [error for error in errors if error.kind == "RelationshipColumnMissing"]
    assert len(missing_column_errors) == 2
    refs = {ref for error in missing_column_errors for ref in error.refs}
    assert "relationship:orders_to_users" in refs
    assert "column:orders.missing_order_column" in refs
    assert "column:users.missing_user_column" in refs


def test_relationship_endpoint_datasets_must_exist() -> None:
    project = SemanticProject(root="/tmp/invalid-relationship-endpoint")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.field(dataset="orders", name="user_id")
        def orders_user_id(orders: Any) -> Any:
            return orders.user_id

        @ms.relationship(
            name="orders_to_missing_users",
            from_="orders",
            to="users",
            from_columns=["user_id"],
            to_columns=["user_id"],
        )
        def orders_to_missing_users() -> None: ...

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    missing_endpoint_errors = [
        error for error in exc_info.value.errors if error.kind == "RelationshipDatasetMissing"
    ]
    assert len(missing_endpoint_errors) == 1
    assert missing_endpoint_errors[0].refs == [
        "relationship:orders_to_missing_users",
        "dataset:users",
    ]


def test_relationship_join_columns_cannot_be_empty() -> None:
    project = SemanticProject(root="/tmp/invalid-empty-relationship")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="users", datasource=warehouse)
        def users(backend: Any) -> Any:
            return backend.table("users")

        @ms.relationship(
            name="orders_to_users",
            from_="orders",
            to="users",
            from_columns=[],
            to_columns=[],
        )
        def orders_to_users() -> None: ...

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert {error.kind for error in exc_info.value.errors} == {"RelationshipColumnsEmpty"}


def test_relationship_join_columns_must_have_equal_arity() -> None:
    project = SemanticProject(root="/tmp/invalid-arity-relationship")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="users", datasource=warehouse)
        def users(backend: Any) -> Any:
            return backend.table("users")

        @ms.field(dataset="orders", name="user_id")
        def orders_user_id(orders: Any) -> Any:
            return orders.user_id

        @ms.field(dataset="orders", name="tenant_id")
        def orders_tenant_id(orders: Any) -> Any:
            return orders.tenant_id

        @ms.field(dataset="users", name="user_id")
        def users_user_id(users: Any) -> Any:
            return users.user_id

        @ms.relationship(
            name="orders_to_users",
            from_="orders",
            to="users",
            from_columns=["user_id", "tenant_id"],
            to_columns=["user_id"],
        )
        def orders_to_users() -> None: ...

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert {error.kind for error in exc_info.value.errors} == {"RelationshipColumnArityMismatch"}


def test_metric_body_accepts_single_return_expression() -> None:
    project = SemanticProject(root="/tmp/valid-ast")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders: Any) -> Any:
            return orders.amount.sum()

    assert project.registry.models["sales"].metrics["revenue"].fn is revenue


def test_metric_body_accepts_docstring_then_single_return_expression() -> None:
    project = SemanticProject(root="/tmp/valid-docstring-ast")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders: Any) -> Any:
            """Total paid revenue."""
            return orders.amount.sum()

    assert project.registry.models["sales"].metrics["revenue"].fn is revenue


def test_metric_body_rejects_assignment_at_decorator_time() -> None:
    project = SemanticProject(root="/tmp/invalid-ast")

    with use_registry(project.registry), pytest.raises(SemanticDecoratorError) as exc_info:
        ms.model(name="sales")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders: Any) -> Any:
            total = orders.amount.sum()
            return total

    assert "AstNodeForbidden" in str(exc_info.value)


def test_metric_body_rejects_no_return_shapes_at_decorator_time() -> None:
    project = SemanticProject(root="/tmp/invalid-return-shape")

    with use_registry(project.registry):
        ms.model(name="sales")

        with pytest.raises(SemanticDecoratorError, match="FunctionBodyInvalid"):

            @ms.metric(decomposition=ms.sum())
            def no_return(orders: Any) -> Any:
                orders.amount.sum()

        with pytest.raises(SemanticDecoratorError, match="FunctionBodyInvalid"):

            @ms.metric(decomposition=ms.sum())
            def pass_only(orders: Any) -> Any:
                pass

        with pytest.raises(SemanticDecoratorError, match="FunctionBodyInvalid"):

            @ms.metric(decomposition=ms.sum())
            def extra_expression(orders: Any) -> Any:
                orders.amount  # noqa: B018
                return orders.amount.sum()


def test_metric_body_rejects_expression_level_control_flow_at_decorator_time() -> None:
    project = SemanticProject(root="/tmp/invalid-expression-ast")

    with use_registry(project.registry):
        ms.model(name="sales")

        with pytest.raises(SemanticDecoratorError, match="AstNodeForbidden"):

            @ms.metric(decomposition=ms.sum())
            def conditional_revenue(orders: Any) -> Any:
                return orders.amount.sum() if orders.is_paid else orders.discount.sum()

        with pytest.raises(SemanticDecoratorError, match="AstNodeForbidden"):

            @ms.metric(decomposition=ms.sum())
            def comprehension_revenue(orders: Any) -> Any:
                return [amount for amount in orders.amount]  # noqa: C416

        with pytest.raises(SemanticDecoratorError, match="AstNodeForbidden"):

            @ms.metric(decomposition=ms.sum())
            def walrus_revenue(orders: Any) -> Any:
                return (total := orders.amount.sum())
