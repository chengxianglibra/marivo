"""Tests for typed catalog navigation — global collections, scoped collections,
and the navigation matrix."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.refs import SemanticKind
from marivo.semantic.catalog import (
    DomainEntry,
    SemanticCatalog,
    _normalize_semantic_input,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from tests.shared_fixtures import catalog_entry, catalog_value
from tests.support.refs import as_ref

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


def test_relationship_endpoints_disclose_exact_entity_refs(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    relationship = catalog_value(catalog, "orders_to_users", kind="relationship")
    assert relationship._parameter.from_entity.ref.key == "entity:sales.orders"
    assert relationship._parameter.to_entity.ref.key == "entity:sales.users"
    assert (
        relationship._parameter.from_entity
        == catalog_value(catalog, "sales.orders", kind="entity")._exact_ref
    )
    assert (
        relationship._parameter.to_entity
        == catalog_value(catalog, "sales.users", kind="entity")._exact_ref
    )
    assert relationship._parameter.from_entity.version.startswith("sha256:")
    assert relationship.contract().facts[:2] == (
        ("from", "entity:sales.orders"),
        ("to", "entity:sales.users"),
    )


# ---------------------------------------------------------------------------
# Teaching lookup error contracts
# ---------------------------------------------------------------------------


def test_semantic_input_normalization_accepts_values_like_their_refs(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    for value, exact_ref, kind in (
        (
            catalog_value(catalog, "sales.orders", kind="entity"),
            ms.ref.entity("sales.orders"),
            SemanticKind.ENTITY,
        ),
        (
            catalog_value(catalog, "sales.orders.region", kind="field"),
            ms.ref.dimension("sales.orders.region"),
            SemanticKind.DIMENSION,
        ),
        (
            catalog_value(catalog, "orders_to_users", kind="relationship"),
            ms.ref.relationship("sales.orders_to_users"),
            SemanticKind.RELATIONSHIP,
        ),
    ):
        by_value = _normalize_semantic_input(
            catalog, value, allowed_kinds=frozenset({kind}), location="test.input"
        )
        by_ref = _normalize_semantic_input(
            catalog, exact_ref, allowed_kinds=frozenset({kind}), location="test.input"
        )
        assert by_value == by_ref
        assert by_value == as_ref(exact_ref)


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


def test_semantic_input_normalization_accepts_entries_like_their_refs(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    sales = catalog_entry(catalog, "sales", kind="domain")
    by_entry = _normalize_semantic_input(
        catalog, sales, allowed_kinds=frozenset({SemanticKind.DOMAIN}), location="test.input"
    )
    by_ref = _normalize_semantic_input(
        catalog, sales.ref, allowed_kinds=frozenset({SemanticKind.DOMAIN}), location="test.input"
    )
    assert by_entry == by_ref == sales._ref


def test_semantic_input_rejects_unregistered_entry_subclass_and_duck_type(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    sales = catalog_entry(catalog, "sales", kind="domain")

    class UnregisteredDomainEntry(DomainEntry):
        pass

    forged = UnregisteredDomainEntry(_ref=sales._ref, _details=sales._details, _catalog=catalog)
    with pytest.raises(SemanticRuntimeError, match="not a registered concrete") as subclass:
        _normalize_semantic_input(
            catalog, forged, allowed_kinds=frozenset({SemanticKind.DOMAIN}), location="test.domain"
        )
    assert subclass.value.received == "UnregisteredDomainEntry(domain:sales)"

    class DuckDomain:
        ref = sales.ref

    with pytest.raises(SemanticRuntimeError, match="duck-typed") as duck:
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

    with pytest.raises(SemanticRuntimeError, match="bare strings") as bare:
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
            ms.ref.entity("sales.orders"),  # type: ignore[arg-type]
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
    with pytest.raises(SemanticRuntimeError, match="another catalog") as exc_info:
        _normalize_semantic_input(
            catalog,
            foreign_domain,
            allowed_kinds=frozenset({SemanticKind.DOMAIN}),
            location="test.domain",
        )
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert exc_info.value.repair.snippet is None


def test_semantic_input_stale_entry_has_executable_exact_reacquisition(
    semantic_project_factory,
) -> None:
    old_catalog = _catalog(semantic_project_factory)
    stale_domain = catalog_entry(old_catalog, "sales", kind="domain")
    current_catalog = _catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError, match="earlier catalog instance") as exc_info:
        _normalize_semantic_input(
            current_catalog,
            stale_domain,
            allowed_kinds=frozenset({SemanticKind.DOMAIN}),
            location="test.domain",
        )
    error = exc_info.value
    assert error.repair is not None
    assert error.repair.kind == "reacquire"
    assert error.repair.snippet == "entry = catalog.get('domain:sales')"
    namespace: dict[str, object] = {"catalog": current_catalog}
    exec(error.repair.snippet, namespace)
    assert namespace["entry"] is catalog_entry(current_catalog, "sales", kind="domain")


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
    with pytest.raises(SemanticRuntimeError, match="stale catalog instance") as exc_info:
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


def test_relationship_contract_discloses_typed_endpoints_and_cardinality(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    rendered = catalog_value(catalog, "orders_to_users", kind="relationship").contract().render()
    assert "from: entity:sales.orders" in rendered
    assert "to: entity:sales.users" in rendered
    assert "cardinality: many_to_one" in rendered
