"""Tests for typed catalog navigation — global collections, scoped collections,
and the navigation matrix."""

from __future__ import annotations

import textwrap

import pytest

from marivo.semantic.catalog import (
    CatalogCollection,
    CatalogEntry,
    DatasourceEntry,
    DimensionEntry,
    DomainEntry,
    EntityEntry,
    MeasureEntry,
    MetricEntry,
    RelationshipEntry,
    SemanticCatalog,
    TimeDimensionEntry,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Analytics", default=True)
"""

_OBJECTS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.Ref.datasource("warehouse"), source=md.table("orders"))
users = ms.entity(name="users", datasource=ms.Ref.datasource("warehouse"), source=md.table("users"))

@ms.dimension(entity=orders)
def region(table):
    return table.region

@ms.dimension(entity=orders)
def user_id(table):
    return table.user_id

@ms.dimension(entity=users)
def id(table):
    return table.id

@ms.time_dimension(entity=orders, granularity="day", parse=ms.timestamp(timezone="UTC"))
def ordered_at(table):
    return table.ordered_at

@ms.measure(entity=orders, additivity="additive", unit="USD")
def amount(table):
    return table.amount

revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")

ms.relationship(
    name="orders_to_users",
    from_entity=orders,
    to_entity=users,
    keys=[ms.join_on(user_id, id)],
)
"""

_OPS_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="ops", owner="Operations")
"""

_OPS_OBJECTS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

events = ms.entity(name="events", datasource=ms.Ref.datasource("warehouse"), source=md.table("events"))

@ms.dimension(entity=events)
def region(table):
    return table.region
"""


def _catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent(_DOMAIN_PY),
            "sales/objects.py": textwrap.dedent(_OBJECTS_PY),
            "ops/_domain.py": textwrap.dedent(_OPS_DOMAIN_PY),
            "ops/objects.py": textwrap.dedent(_OPS_OBJECTS_PY),
        }
    )
    return SemanticCatalog(project)


@pytest.mark.parametrize(
    ("attribute", "expected_type", "expected_id"),
    [
        ("domains", DomainEntry, "domain:sales"),
        ("datasources", DatasourceEntry, "datasource:warehouse"),
        ("entities", EntityEntry, "entity:sales.orders"),
        ("dimensions", DimensionEntry, "dimension:sales.orders.region"),
        (
            "time_dimensions",
            TimeDimensionEntry,
            "time_dimension:sales.orders.ordered_at",
        ),
        ("measures", MeasureEntry, "measure:sales.orders.amount"),
        ("metrics", MetricEntry, "metric:sales.revenue"),
        (
            "relationships",
            RelationshipEntry,
            "relationship:sales.orders_to_users",
        ),
    ],
)
def test_catalog_global_collections_are_typed_and_use_typed_ids(
    semantic_project_factory,
    attribute: str,
    expected_type: type[CatalogEntry],
    expected_id: str,
) -> None:
    collection = getattr(_catalog(semantic_project_factory), attribute)

    assert isinstance(collection, CatalogCollection)
    keys = [ref.key for ref in collection.refs]
    assert expected_id in keys
    assert all(type(item) is expected_type for item in collection.items)
    assert keys == sorted(keys)
    assert all(item.key in keys for item in collection)


def test_catalog_collection_implements_shared_result_and_consumption_protocol(
    semantic_project_factory,
    capsys,
) -> None:
    metrics = _catalog(semantic_project_factory).metrics

    assert isinstance(metrics.items, tuple)
    assert tuple(ref.kind.value for ref in metrics.refs) == ("metric",)
    assert metrics[0] is metrics.items[0]
    assert list(metrics) == list(metrics.items)
    assert "CatalogCollection" in repr(metrics)
    assert "metric:sales.revenue" in metrics.render()
    assert metrics.show() is None
    assert "metric:sales.revenue" in capsys.readouterr().out


def test_catalog_collection_has_one_public_kind_type_parameter() -> None:
    assert tuple(parameter.__name__ for parameter in CatalogCollection.__type_params__) == (
        "KindT",
    )


def test_scoped_navigation_matches_the_declared_matrix(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    sales = catalog.domains.get("sales")
    orders = sales.entities.get("orders")
    warehouse = catalog.datasources.get("warehouse")

    assert orders.key == "entity:sales.orders"
    assert [ref.key for ref in orders.dimensions.refs] == [
        "dimension:sales.orders.region",
        "dimension:sales.orders.user_id",
    ]
    assert [ref.key for ref in orders.time_dimensions.refs] == [
        "time_dimension:sales.orders.ordered_at"
    ]
    assert [ref.key for ref in orders.measures.refs] == ["measure:sales.orders.amount"]
    assert [ref.key for ref in orders.metrics.refs] == ["metric:sales.revenue"]
    assert [ref.key for ref in orders.relationships.refs] == ["relationship:sales.orders_to_users"]
    assert [ref.key for ref in warehouse.entities.refs] == [
        "entity:ops.events",
        "entity:sales.orders",
        "entity:sales.users",
    ]
    assert not hasattr(warehouse, "measures")


def test_relationship_endpoints_are_concrete_entities(semantic_project_factory) -> None:
    relationship = _catalog(semantic_project_factory).relationships.get("orders_to_users")

    assert relationship.from_entity.key == "entity:sales.orders"
    assert relationship.to_entity.key == "entity:sales.users"


# ---------------------------------------------------------------------------
# Teaching lookup error contracts
# ---------------------------------------------------------------------------


def test_catalog_require_rejects_raw_short_name_and_teaches_exact_typed_lookup(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.require("revenue")  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "requires an exact Ref[kind]" in message
    assert "ms.Ref.<kind>(path)" in message


def test_collection_get_rejects_global_typed_identity(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get("entity.sales.orders")

    assert "accepts one local name segment only" in str(exc_info.value)
    assert "catalog.require(ms.Ref.<kind>(path))" in str(exc_info.value)


def test_collection_get_reports_existing_object_outside_scope(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.domains.get("sales").entities.get("events")

    message = str(exc_info.value)
    assert "not found" in message.lower()
    assert "domain:sales" in message


def test_collection_get_rejects_bare_semantic_id(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get("sales.revenue")

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "accepts one local name segment only" in str(exc_info.value)


def test_collection_get_ambiguous_short_name_teaches_scope_narrowing(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.dimensions.get("region")

    assert exc_info.value.kind == ErrorKind.AMBIGUOUS_REFERENCE
    assert "ms.Ref.dimension('sales.orders.region')" in str(exc_info.value)
    assert "ms.Ref.dimension('ops.events.region')" in str(exc_info.value)


def test_collection_get_scoped_short_name_resolves_uniquely(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    region = catalog.domains.get("sales").entities.get("orders").dimensions.get("region")

    assert region.key == "dimension:sales.orders.region"


# ---------------------------------------------------------------------------
# Self-describing cards and bounded repr
# ---------------------------------------------------------------------------


def test_domain_card_advertises_live_navigation_counts(semantic_project_factory) -> None:
    rendered = _catalog(semantic_project_factory).domains.get("sales").render()

    for expected in (
        "entities: 2 -> .entities",
        "dimensions: 3 -> .dimensions",
        "time_dimensions: 1 -> .time_dimensions",
        "measures: 1 -> .measures",
        "metrics: 1 -> .metrics",
        "relationships: 1 -> .relationships",
    ):
        assert expected in rendered


def test_zero_count_navigation_remains_visible(semantic_project_factory) -> None:
    rendered = _catalog(semantic_project_factory).entities.get("users").render()

    assert "measures: 0 -> .measures" in rendered
    assert "metrics: 0 -> .metrics" in rendered


def test_relationship_card_shows_typed_endpoints(semantic_project_factory) -> None:
    rendered = _catalog(semantic_project_factory).relationships.get("orders_to_users").render()

    assert "from_entity: entity:sales.orders" in rendered
    assert "to_entity: entity:sales.users" in rendered


def test_object_and_collection_repr_are_bounded(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert repr(catalog.metrics.get("revenue")) == (
        "<MetricEntry metric:sales.revenue; call .show() to inspect>"
    )
    assert "CatalogCollection type=MetricEntry scope=catalog count=" in repr(catalog.metrics)
    assert "\n" not in repr(catalog.metrics)
