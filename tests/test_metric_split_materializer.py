"""Tests for metric-split Plan 4: materializer dispatch + tier-1 path + derived
composition + metric_on.

Uses an inline semantic project backed by DuckDB with a seeded orders table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ibis
import pytest

from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.materializer import Materializer

# ---------------------------------------------------------------------------
# Inline model: sales domain with tier-1 + derived metrics
# ---------------------------------------------------------------------------

_INLINE_SALES = """\
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

@ms.measure(entity=orders, additivity="additive")
def amount(orders): return orders.amount

@ms.measure(entity=orders, additivity="additive")
def gross(orders): return orders.gross

@ms.measure(entity=orders, additivity="additive")
def refund(orders): return orders.refund

@ms.metric(entities=[orders], additivity="additive", name="revenue_via_measure")
def revenue_via_measure(orders): return amount(orders).sum()

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
gross_total = ms.aggregate(measure=gross, agg="sum", name="gross_total")
refund_total = ms.aggregate(measure=refund, agg="sum", name="refund_total")
aov = ms.ratio(name="aov", numerator=revenue, denominator=order_count)
net = ms.linear(name="net", add=[gross_total], subtract=[refund_total])
"""


def _seed_orders(con: ibis.duckdb.DuckDBBackend) -> None:
    """Seed the in-memory DuckDB with the orders table."""
    con.con.execute(
        "CREATE TABLE orders ("
        "order_id INTEGER, amount DOUBLE, gross DOUBLE, refund DOUBLE, "
        "region VARCHAR, created_at DATE)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES "
        "(1, 10.0, 12.0, 2.0, 'north', DATE '2026-07-01'),"
        "(2, 20.0, 24.0, 4.0, 'north', DATE '2026-07-02'),"
        "(3, 30.0, 36.0, 6.0, 'south', DATE '2026-08-01'),"
        "(4, 40.0, 48.0, 8.0, 'north', DATE '2026-09-15')"
    )


@dataclass
class MaterializedProject:
    """Wrapper around a loaded project + materializer for test assertions."""

    materializer: Materializer
    _con: ibis.duckdb.DuckDBBackend

    def execute_scalar(self, value: Any) -> Any:
        """Execute an ibis value expression and return the scalar result."""
        result = self._con.execute(value)
        if isinstance(result, (int, float)):
            return result
        return result.iloc[0]

    def expected_sum(self, col: str) -> float:
        """Return SUM(col) over the orders table."""
        return self._con.con.execute(f"SELECT SUM({col}) FROM orders").fetchone()[0]

    def row_count(self, table: str) -> int:
        """Return COUNT(*) for the named table."""
        return self._con.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def table(self, name: str) -> Any:
        """Return the ibis table expression for the named table."""
        return self._con.table(name)


@pytest.fixture
def materialized_project(semantic_project_factory):
    """Load the inline sales model with a seeded DuckDB backend."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', default=True)\n"
            ),
            "sales/models.py": _INLINE_SALES,
        }
    )
    assert project.is_ready(), f"Project failed to load: {project.errors()}"
    con = ibis.duckdb.connect(":memory:")
    _seed_orders(con)

    def backend_factory(datasource_name: str):
        return con

    mat = Materializer(project, backend_factory)
    yield MaterializedProject(materializer=mat, _con=con)


# ---------------------------------------------------------------------------
# Task 1: tier-1 metric materialization
# ---------------------------------------------------------------------------


def test_tier1_sum_materializes_as_aggregation(materialized_project):
    # revenue = ms.aggregate(measure=amount, agg="sum")
    value = materialized_project.materializer.metric("sales.revenue")
    assert materialized_project.execute_scalar(value) == materialized_project.expected_sum("amount")


def test_tier1_count_materializes_as_count(materialized_project):
    # order_count = ms.aggregate(measure=amount, agg="count")
    value = materialized_project.materializer.metric("sales.order_count")
    assert materialized_project.execute_scalar(value) == materialized_project.row_count("orders")


def test_tier2_body_can_reference_measure(materialized_project):
    # revenue_via_measure: a tier-2 @ms.metric body that calls the `amount`
    # measure -> amount(orders).sum(). The MeasureRef must resolve to its
    # sidecar callable inside a loaded project, like DimensionRef does.
    value = materialized_project.materializer.metric("sales.revenue_via_measure")
    assert materialized_project.execute_scalar(value) == materialized_project.expected_sum("amount")


# ---------------------------------------------------------------------------
# Task 2: derived metric materialization via Composition
# ---------------------------------------------------------------------------


def test_ratio_materializes_as_division(materialized_project):
    # aov = ms.ratio(numerator=revenue, denominator=order_count)
    value = materialized_project.materializer.metric("sales.aov")
    expected = materialized_project.expected_sum("amount") / materialized_project.row_count(
        "orders"
    )
    assert materialized_project.execute_scalar(value) == expected


def test_linear_materializes_with_signs(materialized_project):
    # net = ms.linear(add=[gross_total], subtract=[refund_total])
    value = materialized_project.materializer.metric("sales.net")
    expected = materialized_project.expected_sum("gross") - materialized_project.expected_sum(
        "refund"
    )
    assert materialized_project.execute_scalar(value) == expected


# ---------------------------------------------------------------------------
# Task 3: metric_on dispatch
# ---------------------------------------------------------------------------


def test_metric_on_rejects_derived(materialized_project):
    table = materialized_project.table("orders")
    with pytest.raises(SemanticRuntimeError):
        materialized_project.materializer.metric_on("sales.aov", table)


def test_metric_on_tier1_applies_to_caller_table(materialized_project):
    table = materialized_project.table("orders")
    value = materialized_project.materializer.metric_on("sales.revenue", table)
    assert materialized_project.execute_scalar(value) == materialized_project.expected_sum("amount")
