"""Tests for typed catalog navigation — global collections, scoped collections,
and the navigation matrix."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from marivo.refs import SemanticKind
from marivo.semantic.catalog import (
    Domain,
    SemanticCatalog,
    _normalize_semantic_input,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from tests.shared_fixtures import catalog_entry, catalog_value

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Analytics", default=True)
"""

_OBJECTS_PY = """import marivo.datasource as md
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

region = ms.dimension_column(name="region", entity=orders, column="region")
user_id = ms.dimension_column(name="user_id", entity=orders, column="user_id")
id = ms.dimension_column(name="id", entity=users, column="id")
ordered_at = ms.time_dimension_column(
    name="ordered_at",
    entity=orders,
    column="ordered_at",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
)
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD"
)

ms.relationship(
    name="orders_to_users",
    from_entity=orders,
    to_entity=users,
    keys=[ms.join_on(user_id, id)],
)
ms.relationship(
    name="orders_self",
    from_entity=orders,
    to_entity=orders,
    keys=[ms.join_on(user_id, user_id)],
)

orders_value = orders
ordered_at_value = ordered_at
amount_value = amount


@ms.metric()
def revenue():
    return amount_value.sum(time=ordered_at_value)
"""

_OPS_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="ops", owner="Operations")
"""

_OPS_OBJECTS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

events = ms.entity(name="events", datasource=ms.ref.datasource("warehouse"), source=md.table("events"))

@ms.dimension(entity=events)
def region(table):
    return table.region
"""


def _catalog(
    semantic_project_factory,
    *,
    workspace_dir: Path | None = None,
) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent(_DOMAIN_PY),
            "sales/objects.py": textwrap.dedent(_OBJECTS_PY),
            "ops/_domain.py": textwrap.dedent(_OPS_DOMAIN_PY),
            "ops/objects.py": textwrap.dedent(_OPS_OBJECTS_PY),
        },
        workspace_dir=workspace_dir,
    )
    return SemanticCatalog(project)


def test_relationship_endpoints_navigate_captured_entity_objects(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    relationship = catalog.get("relationship:sales.orders_to_users")
    assert relationship.kind == "relationship"
    assert relationship.from_entity.id == "entity:sales.orders"
    assert relationship.to_entity.id == "entity:sales.users"
    assert relationship.from_entity.primary_key == ("order_id",)
    assert relationship.definition.cardinality == "many_to_one"
    assert not hasattr(relationship, "value") and not hasattr(relationship, "ref")


def test_semantic_input_normalization_accepts_direct_named_objects(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    for identity in (
        "entity:sales.orders",
        "dimension:sales.orders.region",
        "relationship:sales.orders_to_users",
    ):
        value = catalog.get(identity)
        normalized = _normalize_semantic_input(
            catalog, value, allowed_kinds=frozenset({value._ref.kind}), location="test.input"
        )
        assert normalized == value._ref


def test_semantic_input_rejects_published_metric_as_a_physical_input(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError, match="requires physical inputs") as exc_info:
        _normalize_semantic_input(
            catalog,
            catalog_value(catalog, "sales.revenue", kind="metric"),
            allowed_kinds=frozenset({SemanticKind.METRIC}),
            location="test.input",
        )
    assert exc_info.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    assert exc_info.value.semantic_refs == ("metric:sales.revenue",)
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.snippet is None


def test_semantic_input_normalization_accepts_reloaded_same_project(
    semantic_project_factory,
) -> None:
    original = _catalog(semantic_project_factory)
    sales = original.get("domain:sales")
    receiving = _catalog(semantic_project_factory)
    normalized = _normalize_semantic_input(
        receiving, sales, allowed_kinds=frozenset({SemanticKind.DOMAIN}), location="test.input"
    )
    assert normalized == sales._ref


def test_semantic_input_rejects_unregistered_entry_subclass_and_duck_type(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    sales = catalog_entry(catalog, "sales", kind="domain")

    class UnregisteredDomain(Domain):
        pass

    forged = UnregisteredDomain(_ref=sales._ref, _details=sales._details, _catalog=catalog)
    with pytest.raises(SemanticRuntimeError, match="concrete named semantic object") as subclass:
        _normalize_semantic_input(
            catalog, forged, allowed_kinds=frozenset({SemanticKind.DOMAIN}), location="test.domain"
        )
    assert subclass.value.received == "UnregisteredDomain(domain:sales)"

    class DuckDomain:
        id = sales.id

    with pytest.raises(SemanticRuntimeError, match="named semantic object") as duck:
        _normalize_semantic_input(
            catalog,
            DuckDomain(),
            allowed_kinds=frozenset({SemanticKind.DOMAIN}),
            location="test.domain",
        )
    assert duck.value.received == "DuckDomain"


def test_semantic_input_rejects_bare_string_and_wrong_kind_without_guessing(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError, match="named semantic object") as bare:
        _normalize_semantic_input(
            catalog,
            "sales.revenue",  # type: ignore[arg-type]
            allowed_kinds=frozenset({SemanticKind.METRIC}),
            location="test.metric",
        )
    assert bare.value.repair is not None
    assert bare.value.repair.kind == "inspect"
    assert bare.value.repair.snippet is None
    assert bare.value.repair.candidates == ("metric:sales.revenue",)

    with pytest.raises(SemanticRuntimeError, match="received semantic kind entity") as wrong:
        _normalize_semantic_input(
            catalog,
            catalog.get("entity:sales.orders"),
            allowed_kinds=frozenset({SemanticKind.METRIC}),
            location="test.metric",
        )
    assert wrong.value.repair is not None
    assert wrong.value.repair.kind == "inspect"
    assert wrong.value.repair.snippet is None
    assert wrong.value.repair.candidates == ("metric:sales.revenue",)


def test_semantic_input_rejects_cross_catalog_entry_without_retry(
    tmp_path: Path, semantic_project_factory
) -> None:
    catalog = _catalog(semantic_project_factory)
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    other_catalog = _catalog(semantic_project_factory, workspace_dir=other_workspace)
    foreign_domain = catalog_entry(other_catalog, "sales", kind="domain")
    with pytest.raises(SemanticRuntimeError, match="belongs to project") as exc_info:
        _normalize_semantic_input(
            catalog,
            foreign_domain,
            allowed_kinds=frozenset({SemanticKind.DOMAIN}),
            location="test.domain",
        )
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert exc_info.value.repair.snippet == "subject = catalog.get('domain:sales')"


def test_original_object_navigation_keeps_captured_catalog(semantic_project_factory) -> None:
    original = _catalog(semantic_project_factory)
    sales = original.get("domain:sales")
    receiving = _catalog(semantic_project_factory)
    assert sales._catalog is original
    assert (
        _normalize_semantic_input(
            receiving, sales, allowed_kinds=frozenset({SemanticKind.DOMAIN}), location="test.domain"
        )
        == sales._ref
    )
    assert sales.related("members").scope == original.search().scope


def test_semantic_input_stale_missing_path_requires_explicit_current_choice(
    tmp_path: Path, semantic_project_factory
) -> None:
    old_catalog = _catalog(semantic_project_factory)
    stale_domain = catalog_entry(old_catalog, "ops", kind="domain")
    shutil.rmtree(tmp_path / "models" / "semantic" / "ops")
    current_project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent(_DOMAIN_PY),
            "sales/objects.py": textwrap.dedent(_OBJECTS_PY),
        }
    )
    current_catalog = SemanticCatalog(current_project)
    with pytest.raises(
        SemanticRuntimeError, match="outside the receiving Catalog scope"
    ) as exc_info:
        _normalize_semantic_input(
            current_catalog,
            stale_domain,
            allowed_kinds=frozenset({SemanticKind.DOMAIN}),
            location="test.domain",
        )
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert exc_info.value.repair.snippet is None
    assert exc_info.value.repair.candidates == ("domain:sales",)


# ---------------------------------------------------------------------------
# Self-describing navigation and bounded repr
# ---------------------------------------------------------------------------


def test_relationship_definition_discloses_endpoints_and_cardinality(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    relationship = catalog.get("relationship:sales.orders_to_users")
    rendered = relationship.render()
    assert "entity:sales.orders -> entity:sales.users" in rendered
    assert "cardinality: many_to_one" in rendered
