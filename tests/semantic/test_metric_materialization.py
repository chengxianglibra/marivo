"""Published Metric preview and physical Measure access over real DuckDB rows."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.materializer import Materializer
from marivo.semantic.reader import SemanticProject

_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms
orders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all())
gross_ref = ms.measure_column(name="gross", entity=orders_ref, column="gross", additivity=ms.additive_all())
refund_ref = ms.measure_column(name="refund", entity=orders_ref, column="refund", additivity=ms.additive_all())
finalized = ms.finalize()
amount = finalized.resolve(amount_ref)
gross = finalized.resolve(gross_ref)
refund = finalized.resolve(refund_ref)
orders = finalized.resolve(orders_ref)
@ms.metric()
def revenue():
    return amount.sum()
@ms.metric()
def order_count():
    return amount.count()
@ms.metric()
def order_rows():
    return orders.count()
@ms.metric()
def revenue_via_measure():
    return amount.sum()
@ms.metric()
def aov():
    return revenue / order_rows
@ms.metric()
def net():
    return gross.sum() - refund.sum()
"""


@pytest.fixture
def materialized_project(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
) -> Iterator[tuple[SemanticCatalog, ibis.duckdb.DuckDBBackend]]:
    path = tmp_path / "warehouse.duckdb"
    connection = ibis.duckdb.connect(path)
    connection.con.execute(
        "CREATE TABLE orders(order_id INTEGER, amount DOUBLE, gross DOUBLE, refund DOUBLE)"
    )
    connection.con.execute(
        "INSERT INTO orders VALUES (1,10,12,2),(2,20,24,4),(3,30,36,6),(4,40,48,8)"
    )
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse', path={str(path)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            "ms.domain(name='sales', owner='Data', default=True)\n",
            "sales/models.py": _MODEL,
        }
    )
    try:
        yield SemanticCatalog(project), connection
    finally:
        connection.disconnect()


@pytest.mark.parametrize(
    ("metric", "oracle"),
    (
        ("revenue", "SUM(amount)"),
        ("order_count", "COUNT(amount)"),
        ("order_rows", "COUNT(*)"),
        ("revenue_via_measure", "SUM(amount)"),
        ("aov", "SUM(amount)/COUNT(*)"),
        ("net", "SUM(gross)-SUM(refund)"),
    ),
)
def test_published_metric_preview_matches_source_equation(
    materialized_project: tuple[SemanticCatalog, ibis.duckdb.DuckDBBackend],
    metric: str,
    oracle: str,
) -> None:
    catalog, connection = materialized_project
    expected = connection.con.execute(f"SELECT {oracle} FROM orders").fetchone()[0]
    connection.disconnect()
    result = catalog.preview(
        ms.ref.metric(f"sales.{metric}"),
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
    )
    assert result.rows == ({"value": expected},)


def test_physical_measure_on_uses_caller_table(
    materialized_project: tuple[SemanticCatalog, ibis.duckdb.DuckDBBackend],
) -> None:
    catalog, connection = materialized_project
    materializer = Materializer(catalog._project, lambda _name: connection)
    table = connection.table("orders").filter(lambda rows: rows.amount > 10)
    value = materializer.measure_on("sales.orders.amount", table)
    assert connection.execute(value.sum()) == connection.execute(table.amount.sum()) == 90


@pytest.mark.parametrize("metric", ("revenue", "aov"))
def test_published_metric_cannot_be_rebound_to_an_arbitrary_ibis_table(
    materialized_project: tuple[SemanticCatalog, ibis.duckdb.DuckDBBackend],
    metric: str,
) -> None:
    catalog, connection = materialized_project
    materializer = Materializer(catalog._project, lambda _name: connection)
    with pytest.raises(SemanticRuntimeError) as caught:
        materializer.metric_on(f"sales.{metric}", connection.table("orders"))
    assert caught.value.kind == "metric_not_found"
