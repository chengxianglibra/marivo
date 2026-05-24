from __future__ import annotations

from typing import Any

import ibis
import pytest

import marivo.semantic_py as ms
from marivo.semantic_py.errors import SemanticParityError
from marivo.semantic_py.parity import _comparison_ok, compare_metric_to_source_sql
from marivo.semantic_py.registry import SemanticProject, use_registry


def _project(
    *,
    source_sql: str
    | None = "select sum(case when pay_status = 1 then amount else 0 end) as value from orders",
    source_dialect: str | None = "duckdb",
    backend_type: str | None = "duckdb",
) -> SemanticProject:
    project = SemanticProject(root="/tmp/parity")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse", backend_type=backend_type)
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.metric(
            decomposition=ms.sum(),
            source_sql=source_sql,
            source_dialect=source_dialect if source_sql is not None else None,
            source_document="kb://finance/revenue" if source_sql is not None else None,
        )
        def paid_revenue(orders: Any) -> Any:
            return orders.filter(orders.pay_status == 1).amount.sum()

    project.registry.state = "ready"
    return project


def _missing_dataset_project() -> SemanticProject:
    project = SemanticProject(root="/tmp/parity-missing-dataset")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse", backend_type="duckdb")
        def warehouse() -> None: ...

        @ms.metric(
            decomposition=ms.sum(),
            source_sql="select 17 as value",
            source_dialect="duckdb",
        )
        def paid_revenue(orders: Any) -> Any:
            return orders.amount.sum()

    project.registry.state = "ready"
    return project


def _non_scalar_metric_project() -> SemanticProject:
    project = SemanticProject(root="/tmp/parity-non-scalar-metric")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse", backend_type="duckdb")
        def warehouse() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.metric(
            decomposition=ms.sum(),
            source_sql="select 17 as value",
            source_dialect="duckdb",
        )
        def paid_revenue(orders: Any) -> Any:
            return orders

    project.registry.state = "ready"
    return project


def _multi_datasource_project() -> SemanticProject:
    project = SemanticProject(root="/tmp/parity-multi-datasource")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse() -> None: ...

        @ms.datasource(name="finance")
        def finance() -> None: ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.dataset(name="refunds", datasource=finance)
        def refunds(backend: Any) -> Any:
            return backend.table("refunds")

        @ms.metric(
            decomposition=ms.sum(),
            source_sql="select 1 as value",
            source_dialect="duckdb",
        )
        def net_revenue(orders: Any, refunds: Any) -> Any:
            return orders.amount.sum() - refunds.amount.sum()

    project.registry.state = "ready"
    return project


def test_compare_metric_to_source_sql_passes_for_equal_results() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1, 0, 1], "amount": [10, 99, 7]})
    calls: list[str] = []

    def backend_factory(datasource_name: str) -> Any:
        calls.append(datasource_name)
        return con

    result = compare_metric_to_source_sql(
        project=project,
        model="sales",
        metric="paid_revenue",
        backend_factory=backend_factory,
    )

    assert result.ok is True
    assert result.metric_value == 17
    assert result.sql_value == 17
    assert result.source_sql == (
        "select sum(case when pay_status = 1 then amount else 0 end) as value from orders"
    )
    assert result.source_dialect == "duckdb"
    assert result.source_document == "kb://finance/revenue"
    assert calls == ["warehouse"]


def test_compare_metric_to_source_sql_reports_mismatch() -> None:
    project = _project(source_sql="select sum(amount) as value from orders")
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1, 0, 1], "amount": [10, 99, 7]})

    result = compare_metric_to_source_sql(
        project=project,
        model="sales",
        metric="paid_revenue",
        backend_factory=lambda datasource_name: con,
    )

    assert result.ok is False
    assert result.metric_value == 17
    assert result.sql_value == 116


def test_comparison_is_exact_by_default_for_numeric_values() -> None:
    assert _comparison_ok(0.1 + 0.2, 0.3, metric_ref="metric:sales.ratio") is False
    assert _comparison_ok(10**12, 10**12 + 1, metric_ref="metric:sales.revenue") is False


def test_comparison_allows_explicit_small_numeric_drift() -> None:
    assert (
        _comparison_ok(
            0.1 + 0.2,
            0.3,
            metric_ref="metric:sales.ratio",
            abs_tol=1e-9,
        )
        is True
    )
    assert _comparison_ok(1.0, 1.01, metric_ref="metric:sales.ratio") is False


@pytest.mark.parametrize("source_sql", [None, "   "])
def test_compare_metric_to_source_sql_requires_source_sql(source_sql: str | None) -> None:
    project = _project(source_sql=source_sql)
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1], "amount": [10]})

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.phase == "parity"
    assert exc_info.value.kind == "SourceSqlMissing"
    assert exc_info.value.refs == ["metric:sales.paid_revenue"]


def test_compare_metric_to_source_sql_requires_source_dialect() -> None:
    project = _project(source_dialect=None)

    def backend_factory(datasource_name: str) -> Any:
        raise AssertionError("backend should not be created when source_dialect is missing")

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=backend_factory,
        )

    assert exc_info.value.phase == "parity"
    assert exc_info.value.kind == "SourceDialectMissing"
    assert exc_info.value.refs == ["metric:sales.paid_revenue", "datasource:sales.warehouse"]


def test_compare_metric_to_source_sql_requires_source_backend_type() -> None:
    project = _project(source_dialect="duckdb", backend_type=None)

    def backend_factory(datasource_name: str) -> Any:
        raise AssertionError("backend should not be created when backend_type is missing")

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=backend_factory,
        )

    assert exc_info.value.phase == "parity"
    assert exc_info.value.kind == "SourceBackendTypeMissing"
    assert exc_info.value.refs == ["metric:sales.paid_revenue", "datasource:sales.warehouse"]


def test_compare_metric_to_source_sql_wraps_missing_metric() -> None:
    project = _project()
    con = ibis.duckdb.connect()

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="missing_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.phase == "parity"
    assert exc_info.value.kind == "MetricMissing"
    assert exc_info.value.refs == ["metric:sales.missing_revenue"]


def test_compare_metric_to_source_sql_wraps_missing_metric_dataset() -> None:
    project = _missing_dataset_project()
    con = ibis.duckdb.connect()

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.phase == "parity"
    assert exc_info.value.kind == "MetricDatasetMissing"
    assert exc_info.value.refs == ["metric:sales.paid_revenue", "dataset:sales.orders"]


def test_compare_metric_to_source_sql_rejects_source_dialect_backend_mismatch() -> None:
    project = _project(source_dialect="trino", backend_type="duckdb")
    con = ibis.duckdb.connect()

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.phase == "parity"
    assert exc_info.value.kind == "SourceDialectMismatch"
    assert exc_info.value.refs == ["metric:sales.paid_revenue", "datasource:sales.warehouse"]


@pytest.mark.parametrize(
    ("source_sql", "expected_kind"),
    [
        ("select amount from orders where pay_status = 9", "SourceSqlResultShapeInvalid"),
        ("select amount from orders", "SourceSqlResultShapeInvalid"),
        ("select amount, pay_status from orders limit 1", "SourceSqlResultShapeInvalid"),
    ],
)
def test_compare_metric_to_source_sql_rejects_non_scalar_source_sql_result(
    source_sql: str,
    expected_kind: str,
) -> None:
    project = _project(source_sql=source_sql)
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1, 0], "amount": [10, 99]})

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.refs == ["metric:sales.paid_revenue"]


def test_compare_metric_to_source_sql_wraps_invalid_source_sql() -> None:
    project = _project(source_sql="select missing_column from orders")
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1], "amount": [10]})

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.kind == "SourceSqlExecutionFailed"
    assert exc_info.value.refs == ["metric:sales.paid_revenue"]


def test_compare_metric_to_source_sql_wraps_metric_execution_failure() -> None:
    project = _non_scalar_metric_project()
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1, 0], "amount": [10, 99]})

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="paid_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.kind == "MetricResultShapeInvalid"
    assert exc_info.value.refs == ["metric:sales.paid_revenue"]


def test_compare_metric_to_source_sql_rejects_multi_datasource_metric() -> None:
    project = _multi_datasource_project()
    con = ibis.duckdb.connect()

    with pytest.raises(SemanticParityError) as exc_info:
        compare_metric_to_source_sql(
            project=project,
            model="sales",
            metric="net_revenue",
            backend_factory=lambda datasource_name: con,
        )

    assert exc_info.value.kind == "SourceSqlDatasourceAmbiguous"
    assert exc_info.value.refs == [
        "metric:sales.net_revenue",
        "dataset:sales.orders",
        "dataset:sales.refunds",
    ]
