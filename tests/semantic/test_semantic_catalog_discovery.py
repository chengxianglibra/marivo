"""Tests for semantic catalog discovery on the value-based surface.

Covers four related catalog discovery behaviors:
1. catalog.metrics and catalog.entities return expression values at top level
2. catalog.relationships returns Relationship values
3. catalog.fields holds Dimension, TimeField and Measure values
4. domains/datasources collections resolve only their own member paths
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from typing import TypeAlias

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef
from marivo.semantic._deferred_semantics import consume_field, consume_metric
from marivo.semantic.catalog import (
    DatasourceEntry,
    DomainEntry,
    SemanticCatalog,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.expressions import (
    Entity,
    Relationship,
)
from marivo.semantic.reader import SemanticProject

ProjectFactory: TypeAlias = Callable[..., SemanticProject]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_DATASETS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
    )
    region = ms.dimension_column(name="region", entity=orders, column="region")
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        parse=ms.timestamp(timezone="UTC"),
        is_default=True,
    )
    amount = ms.measure_column(
        name="amount",
        entity=orders,
        column="amount",
        additivity=ms.additive_all(),
        unit="CNY",
    )

    finalized = ms.finalize()
    amount_value = finalized.resolve(amount)
    created_at_value = finalized.resolve(created_at)

    @ms.metric()
    def revenue():
        return amount_value.sum(time=created_at_value)
""")

_OPS_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="ops", owner="Mina Zhang")
""")

_OPS_DATASETS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    events = ms.entity(
        name="events",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("events"),
    )

    finalized = ms.finalize()
    events_value = finalized.resolve(events)

    @ms.metric()
    def event_count():
        return events_value.count()
""")

_RELATIONSHIP_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
        primary_key=["order_id"],
    )
    users = ms.entity(
        name="users",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("users"),
        primary_key=["id"],
    )
    user_id = ms.dimension_column(name="user_id", entity=orders, column="user_id")
    id = ms.dimension_column(name="id", entity=users, column="id")
    ms.relationship(
        name="orders_to_users",
        from_entity=orders,
        to_entity=users,
        keys=[ms.join_on(user_id, id)],
    )
""")


def _make_catalog(semantic_project_factory: ProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def _make_multi_domain_catalog(semantic_project_factory: ProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": _OPS_DOMAIN_PY,
            "ops/datasets.py": _OPS_DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def _make_relationship_catalog(semantic_project_factory: ProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RELATIONSHIP_PY,
        }
    )
    return SemanticCatalog(project)


# ---------------------------------------------------------------------------
# Top-level collections return the published values
# ---------------------------------------------------------------------------


def test_discovery_metrics_returns_metrics(semantic_project_factory: ProjectFactory) -> None:
    result = _make_catalog(semantic_project_factory).metrics

    assert result.refs == (ms.ref.metric("sales.revenue"),)
    assert all(isinstance(obj, Deferred) for obj in result.items)
    for obj in result.items:
        assert isinstance(consume_metric(obj).node, DeclaredMetricRef)
        assert str(consume_metric(obj).node.ref.ref.kind) == "metric"


def test_discovery_metrics_cross_domain(semantic_project_factory: ProjectFactory) -> None:
    result = _make_multi_domain_catalog(semantic_project_factory).metrics

    assert {ref.path for ref in result.refs} == {"sales.revenue", "ops.event_count"}
    assert all(isinstance(obj, Deferred) for obj in result.items)


def test_discovery_entities_returns_entities(semantic_project_factory: ProjectFactory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.entities

    assert result.refs == (ms.ref.entity("sales.orders"),)
    assert all(isinstance(obj, Entity) for obj in result.items)

    orders = result.get("sales.orders")
    assert orders.ref.ref == ms.ref.entity("sales.orders")

    created_at = catalog.fields.get("sales.orders.created_at")
    assert isinstance(created_at, Deferred)
    assert orders.default_time == consume_field(created_at).reference


def test_discovery_fields_holds_dimension_time_field_and_measure(
    semantic_project_factory: ProjectFactory,
) -> None:
    result = _make_catalog(semantic_project_factory).fields

    kinds: set[str] = set()
    for value in result.items:
        assert isinstance(value, Deferred)
        kinds.add(str(consume_field(value).reference.ref.kind))
    assert kinds == {"dimension", "time_dimension", "measure"}
    assert {ref.key for ref in result.refs} == {
        "dimension:sales.orders.region",
        "time_dimension:sales.orders.created_at",
        "measure:sales.orders.amount",
    }


def test_discovery_fields_get_returns_typed_values(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    assert isinstance(catalog.fields.get("sales.orders.region"), Deferred)
    assert isinstance(catalog.fields.get("sales.orders.created_at"), Deferred)

    amount = catalog.fields.get("sales.orders.amount")
    assert isinstance(amount, Deferred)
    assert consume_field(amount).unit == "CNY"
    assert consume_field(amount).root.ref == ms.ref.entity("sales.orders")


def test_discovery_relationships_returns_relationships(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _make_relationship_catalog(semantic_project_factory)
    result = catalog.relationships

    assert result.refs == (ms.ref.relationship("sales.orders_to_users"),)
    relationship = result.get("sales.orders_to_users")
    assert isinstance(relationship, Relationship)
    assert relationship.from_entity.ref == ms.ref.entity("sales.orders")
    assert relationship.to_entity.ref == ms.ref.entity("sales.users")
    assert relationship.cardinality == "many_to_one"
    assert isinstance(catalog.relationships.get("sales.orders_to_users"), Relationship)


# ---------------------------------------------------------------------------
# Domain and datasource collections return only their own kinds
# ---------------------------------------------------------------------------


def test_discovery_top_level_domains_and_datasources_return_only_their_kinds(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    assert {str(ref.kind) for ref in catalog.domains.refs} == {"domain"}
    assert all(isinstance(obj, DomainEntry) for obj in catalog.domains.items)

    assert {str(ref.kind) for ref in catalog.datasources.refs} == {"datasource"}
    assert all(isinstance(obj, DatasourceEntry) for obj in catalog.datasources.items)
    assert "datasource:warehouse" in {ref.key for ref in catalog.datasources.refs}


def test_discovery_domain_and_datasource_scope_their_members(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    sales = catalog.domains.get("sales")
    assert sales.metrics.refs == (ms.ref.metric("sales.revenue"),)
    assert {ref.key for ref in sales.fields.refs} == {
        "dimension:sales.orders.region",
        "time_dimension:sales.orders.created_at",
        "measure:sales.orders.amount",
    }

    warehouse = catalog.datasources.get("warehouse")
    assert warehouse.entities.refs == (ms.ref.entity("sales.orders"),)


# ---------------------------------------------------------------------------
# Collection membership and NOT_FOUND behavior
# ---------------------------------------------------------------------------


def test_discovery_collection_resolves_every_expression_member(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    assert catalog.entities.refs
    for ref in catalog.entities.refs:
        assert isinstance(catalog.entities.get(ref.path), Entity)
    assert catalog.fields.refs
    for ref in catalog.fields.refs:
        assert isinstance(catalog.fields.get(ref.path), Deferred)
    assert catalog.metrics.refs
    for ref in catalog.metrics.refs:
        assert isinstance(catalog.metrics.get(ref.path), Deferred)


def test_discovery_unknown_domain_raises_error(semantic_project_factory: ProjectFactory) -> None:
    catalog = _make_catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.domains.get("nonexistent")

    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert "sales" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Plain reads stay silent
# ---------------------------------------------------------------------------


def test_discovery_reads_emit_no_stdout(
    semantic_project_factory: ProjectFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    _ = catalog.metrics.refs
    _ = catalog.fields.items
    _ = catalog.entities.get("sales.orders")
    _ = len(catalog.relationships)
    _ = catalog.domains.get("sales").metrics
    _ = repr(catalog.fields)

    assert capsys.readouterr().out == ""
