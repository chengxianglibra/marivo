"""Metric authoring surface: field/measure values, discovery, and preview boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import ibis
import pytest
from ibis.common.deferred import Deferred

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef
from marivo._definition.types import SumAll
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_field, consume_metric
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.expressions import entity_exact
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value
from tests.shared_fixtures import resolve_test_symbol as resolve

ProjectFactory: TypeAlias = Callable[..., SemanticProject]

_DOMAIN_PY = """\
import marivo.datasource as md
import marivo.semantic as ms
ms.domain(name="sales", owner='Mina Zhang', default=True)
"""

_MODEL_PY = """import marivo.datasource as md
import marivo.semantic as ms

orders_ref = ms.entity(
    name="orders",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
)
region_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")
amount_ref = ms.measure_column(
    name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all(), unit="USD"
)
order_date_ref = ms.time_dimension_column(
    name="order_date",
    entity=orders_ref,
    column="order_date",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
)

orders = orders_ref
region = region_ref
amount = amount_ref
order_date = order_date_ref


@ms.measure()
def margin():
    return amount - 1


@ms.metric(
    ai_context=ms.ai_context(business_definition="Historical SQL: select sum(amount) from orders")
)
def revenue():
    return amount.sum(time=order_date)
"""


def _catalog(semantic_project_factory: ProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODEL_PY,
        }
    )
    return SemanticCatalog(project)


def _preview_backend(path: str):
    backend = ibis.duckdb.connect(path)
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, order_date TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    return backend


def test_catalog_get_measure_returns_the_resolved_measure_value(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    orders = resolve(catalog, ms.ref.entity("sales.orders"))

    assert type(amount) is Deferred
    amount = consume_field(amount)
    assert amount.reference.ref == ref_factory.measure("sales.orders.amount")
    assert amount.reference.version.startswith("sha256:")
    assert amount.root == entity_exact(orders)
    assert amount.unit == "USD"
    assert amount.permission == SumAll()
    assert "\n" not in repr(catalog.get("measure:sales.orders.amount"))


def test_catalog_fields_collection_holds_dimension_measure_and_time_field(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    assert [
        entry.id
        for entry in catalog.search().items
        if entry.kind in {"dimension", "measure", "time_dimension"}
    ] == [
        "dimension:sales.orders.region",
        "measure:sales.orders.amount",
        "measure:sales.orders.margin",
        "time_dimension:sales.orders.order_date",
    ]
    assert type(catalog_value(catalog, "sales.orders.region", kind="field")) is Deferred
    assert type(catalog_value(catalog, "sales.orders.amount", kind="field")) is Deferred
    assert type(catalog_value(catalog, "sales.orders.order_date", kind="field")) is Deferred
    assert type(catalog_value(catalog, "sales.orders.margin", kind="field")) is Deferred

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("measure:sales.orders.missing")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert "catalog.search" in str(exc_info.value)


def test_domain_and_catalog_collections_agree_on_metric_membership(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    domain = catalog.get("domain:sales")

    assert [entry.id for entry in catalog.search(kind="metric").items] == ["metric:sales.revenue"]
    assert [entry.id for entry in domain.related("members").items if entry.kind == "metric"] == [
        "metric:sales.revenue"
    ]
    scoped = catalog_value(domain, "sales.orders.amount", kind="field")
    global_amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    assert type(scoped) is Deferred and type(global_amount) is Deferred
    assert consume_field(scoped) == consume_field(global_amount)
    assert (
        catalog_value(catalog, "revenue", kind="metric")
        is catalog.get("metric:sales.revenue").value
    )


def test_metric_value_carries_roots_unit_and_business_context(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    revenue = catalog.get("metric:sales.revenue").value
    contract = catalog.get("metric:sales.revenue")

    assert type(revenue) is Deferred
    revenue = consume_metric(revenue)
    assert isinstance(revenue.node, DeclaredMetricRef)
    assert revenue.roots == (entity_exact(orders),)
    assert revenue.unit == "USD"
    assert contract.kind == "metric"
    assert (
        contract.definition.context.business_definition
        == "Historical SQL: select sum(amount) from orders"
    )
    assert contract.definition.roots == (entity_exact(orders).ref.key,)


def test_measure_preview_uses_measure_expression_without_context_columns(
    semantic_project_factory: ProjectFactory,
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "warehouse.duckdb"
    backend = _preview_backend(str(database_path))
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODEL_PY,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    snapshot = md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "order_date"),
    )

    preview = catalog.preview(
        catalog_value(catalog, "sales.orders.amount", kind="field"),
        scope=snapshot.scope,
        limit=3,
    )

    assert preview.ref == "sales.orders.amount"
    assert preview.kind == "semantic_measure"
    assert "amount" in preview.columns

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(
            catalog_value(catalog, "sales.orders.amount", kind="field"),
            scope=snapshot.scope,
            context_columns=("region",),
        )

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "context_columns" in str(exc_info.value)


def test_published_metric_and_declared_measure_have_no_physical_preview(
    semantic_project_factory: ProjectFactory,
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "warehouse.duckdb"
    backend = _preview_backend(str(database_path))
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODEL_PY,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    snapshot = md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "order_date"),
    )

    preview = catalog.preview(
        catalog.get("metric:sales.revenue").value, scope=snapshot.scope, limit=1
    )
    assert preview.kind == "semantic_metric"
    assert preview.returned_row_count == 1
    with pytest.raises(SemanticRuntimeError, match="requires physical inputs") as exc_info:
        catalog.preview(
            catalog_value(catalog, "sales.orders.margin", kind="field"),
            scope=snapshot.scope,
            limit=1,
        )
    assert exc_info.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
