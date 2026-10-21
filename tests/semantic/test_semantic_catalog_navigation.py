"""Tests for typed catalog navigation — global collections, scoped collections,
and the navigation matrix."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo.refs import SemanticKind
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, AgentResult
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS
from marivo.semantic._deferred_semantics import consume_field, consume_metric
from marivo.semantic.catalog import (
    CatalogCollection,
    DatasourceEntry,
    DomainEntry,
    SemanticCatalog,
    _normalize_semantic_input,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.expressions import (
    Entity,
    Relationship,
)

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Analytics", default=True)
"""

_OBJECTS_PY = """\
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

region = ms.dimension_column(name="region", entity=orders, column="region")
user_id = ms.dimension_column(name="user_id", entity=orders, column="user_id")
id = ms.dimension_column(name="id", entity=users, column="id")
ordered_at = ms.time_dimension_column(
    name="ordered_at",
    entity=orders,
    column="ordered_at",
    granularity="day",
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

finalized = ms.finalize()
orders_value = finalized.resolve(orders)
ordered_at_value = finalized.resolve(ordered_at)
amount_value = finalized.resolve(amount)


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


def test_every_displayed_collection_key_round_trips_through_get(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    for member in CATALOG_MEMBER_CONTRACTS:
        collection = getattr(catalog, member.property_name)
        for ref, item in zip(collection.refs, collection.items, strict=True):
            value = collection.get(ref.path)
            if isinstance(item, Deferred) and ref.kind.value != "metric":
                assert consume_field(value) == consume_field(item)
            else:
                assert value is item


def test_semantic_catalog_is_a_bounded_self_describing_result(
    semantic_project_factory,
    capsys,
) -> None:
    catalog = _catalog(semantic_project_factory)

    assert isinstance(catalog, AgentResult)
    assert "SemanticCatalog" in repr(catalog)
    assert "\n" not in repr(catalog)
    rendered = catalog.render()
    for member in CATALOG_MEMBER_CONTRACTS:
        collection = getattr(catalog, member.property_name)
        if len(collection):
            assert f"catalog.{member.property_name}.show()" in rendered
            assert member.item_type_name in rendered
        else:
            assert member.property_name in rendered.split("empty_collections: ", 1)[1]
    assert catalog.definition_fingerprint in rendered
    assert catalog.show() is None
    assert capsys.readouterr().out.rstrip() == rendered


def test_collection_show_uses_one_receiver_qualified_selection_template(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    global_rendered = catalog.metrics.render()
    scoped_rendered = catalog.domains.get("sales").metrics.render()

    assert global_rendered.count("catalog.metrics.get(<displayed path>)") == 1
    assert scoped_rendered.count("collection.get(<displayed path>)") == 1
    assert "metric:sales.revenue" in global_rendered


def test_empty_collection_show_explains_blocked_selection(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    collection = catalog.work_schedules

    rendered = collection.render()
    assert "catalog.work_schedules.get(...): blocked; reason=empty_collection" in rendered


def test_catalog_output_respects_default_and_small_byte_budgets(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    assert len(catalog.render().encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    small = catalog.render(max_output_bytes=640)
    assert len(small.encode("utf-8")) <= 640
    assert "truncated" in small


def test_renderable_catalog_objects_expose_only_public_dir_members(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    assert "metrics" in dir(catalog)
    assert "render" in dir(catalog)
    assert "_index" not in dir(catalog)
    assert "items" in dir(catalog.metrics)
    assert "get" in dir(catalog.metrics)
    assert "contract" not in dir(catalog.metrics)
    assert "_catalog" not in dir(catalog.metrics)


def test_catalog_collection_has_one_public_kind_type_parameter() -> None:
    assert tuple(parameter.__name__ for parameter in CatalogCollection.__type_params__) == (
        "ItemT",
    )


def test_scoped_navigation_matches_the_declared_matrix(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    sales = catalog.domains.get("sales")
    orders = sales.entities.get("orders")
    warehouse = catalog.datasources.get("warehouse")

    assert isinstance(sales, DomainEntry)
    assert isinstance(warehouse, DatasourceEntry)
    assert orders.ref.ref.key == "entity:sales.orders"
    assert [ref.key for ref in sales.entities.refs] == [
        "entity:sales.orders",
        "entity:sales.users",
    ]
    assert [ref.key for ref in sales.fields.refs] == [
        "dimension:sales.orders.region",
        "dimension:sales.orders.user_id",
        "dimension:sales.users.id",
        "measure:sales.orders.amount",
        "time_dimension:sales.orders.ordered_at",
    ]
    assert [ref.key for ref in sales.metrics.refs] == ["metric:sales.revenue"]
    assert [ref.key for ref in sales.relationships.refs] == [
        "relationship:sales.orders_self",
        "relationship:sales.orders_to_users",
    ]
    assert sales.period_calendars.refs == ()
    assert sales.temporal_sets.refs == ()
    assert sales.work_schedules.refs == ()
    assert [ref.key for ref in warehouse.entities.refs] == [
        "entity:ops.events",
        "entity:sales.orders",
        "entity:sales.users",
    ]
    assert warehouse.details().dependents == warehouse.entities.refs
    assert not hasattr(warehouse, "fields")
    assert not hasattr(warehouse, "metrics")


def test_catalog_collections_hold_typed_expression_values(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert isinstance(catalog.entities.get("sales.orders"), Entity)
    assert isinstance(catalog.fields.get("sales.orders.region"), Deferred)
    assert isinstance(catalog.fields.get("sales.orders.ordered_at"), Deferred)
    assert isinstance(catalog.fields.get("sales.orders.amount"), Deferred)
    assert isinstance(catalog.metrics.get("sales.revenue"), Deferred)
    assert isinstance(catalog.relationships.get("orders_to_users"), Relationship)


def test_relationship_endpoints_disclose_exact_entity_refs(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    relationship = catalog.relationships.get("orders_to_users")

    assert relationship.from_entity.ref.key == "entity:sales.orders"
    assert relationship.to_entity.ref.key == "entity:sales.users"
    assert relationship.from_entity == catalog.entities.get("sales.orders").ref
    assert relationship.to_entity == catalog.entities.get("sales.users").ref
    assert relationship.from_entity.version.startswith("sha256:")
    assert relationship.contract().facts[:2] == (
        ("from", "entity:sales.orders"),
        ("to", "entity:sales.users"),
    )


# ---------------------------------------------------------------------------
# Teaching lookup error contracts
# ---------------------------------------------------------------------------


def test_collection_get_reports_missing_full_path(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    sales_entities = catalog.domains.get("sales").entities

    with pytest.raises(SemanticRuntimeError) as exc_info:
        sales_entities.get("sales.orders.region")

    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert "was not found in catalog.entities" in str(exc_info.value)


def test_collection_get_reports_existing_object_outside_scope(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.domains.get("sales").entities.get("events")

    message = str(exc_info.value)
    assert "not found" in message.lower()
    assert "domain:sales" in message


def test_collection_get_accepts_exact_full_path(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    metric = catalog.metrics.get("sales.revenue")

    assert consume_metric(metric).node.ref.ref.key == "metric:sales.revenue"


@pytest.mark.parametrize("key", ("metric:sales.revenue", "unknown:sales.revenue", "metric:sales"))
def test_collection_get_rejects_kind_prefixed_strings(
    semantic_project_factory,
    key: str,
) -> None:
    catalog = _catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get(key)
    error = exc_info.value
    assert error.kind == ErrorKind.INVALID_REF
    assert error.expected == "a plain local name or full semantic path string"
    assert error.received == key
    assert error.repair is not None
    assert error.repair.candidates == ("sales.revenue",)


def test_collection_get_ambiguous_short_name_teaches_scope_narrowing(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.fields.get("region")

    assert exc_info.value.kind == ErrorKind.AMBIGUOUS_REFERENCE
    assert "catalog.fields.get('sales.orders.region')" in str(exc_info.value)
    assert "catalog.fields.get('ops.events.region')" in str(exc_info.value)


def test_collection_get_scoped_short_name_resolves_uniquely(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    region = catalog.domains.get("sales").fields.get("region")

    assert consume_field(region).reference.ref.key == "dimension:sales.orders.region"


def test_collection_get_accepts_full_path_in_scope(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    region = catalog.domains.get("sales").fields.get("sales.orders.region")

    assert consume_field(region).reference.ref.key == "dimension:sales.orders.region"


def test_collection_get_full_path_cannot_escape_scope(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.domains.get("sales").fields.get("ops.events.region")
    message = str(exc_info.value)
    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert "outside collection scope domain:sales" in message
    assert "hard visibility boundary" in message
    assert "catalog.fields.get('ops.events.region')" in message


@pytest.mark.parametrize(
    "key", (ms.ref.metric("sales.revenue"), ms.ref.entity("sales.orders"), 1, None)
)
def test_collection_get_rejects_non_string_inputs(semantic_project_factory, key: object) -> None:
    catalog = _catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get(key)  # type: ignore[arg-type]  # Exercise runtime input validation.
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "plain local name or full semantic path string" in str(exc_info.value)


def test_semantic_input_normalization_accepts_values_like_their_refs(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    for value, exact_ref, kind in (
        (catalog.entities.get("sales.orders"), ms.ref.entity("sales.orders"), SemanticKind.ENTITY),
        (
            catalog.fields.get("sales.orders.region"),
            ms.ref.dimension("sales.orders.region"),
            SemanticKind.DIMENSION,
        ),
        (
            catalog.relationships.get("orders_to_users"),
            ms.ref.relationship("sales.orders_to_users"),
            SemanticKind.RELATIONSHIP,
        ),
    ):
        by_value = _normalize_semantic_input(
            catalog,
            value,
            allowed_kinds=frozenset({kind}),
            location="test.input",
        )
        by_ref = _normalize_semantic_input(
            catalog,
            exact_ref,
            allowed_kinds=frozenset({kind}),
            location="test.input",
        )

        assert by_value == by_ref
        assert by_value == exact_ref


def test_semantic_input_rejects_published_metric_as_a_physical_input(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError, match="requires physical inputs") as exc_info:
        _normalize_semantic_input(
            catalog,
            catalog.metrics.get("sales.revenue"),
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
    sales = catalog.domains.get("sales")

    by_entry = _normalize_semantic_input(
        catalog,
        sales,
        allowed_kinds=frozenset({SemanticKind.DOMAIN}),
        location="test.input",
    )
    by_ref = _normalize_semantic_input(
        catalog,
        sales.ref,
        allowed_kinds=frozenset({SemanticKind.DOMAIN}),
        location="test.input",
    )

    assert by_entry == by_ref == sales.ref


def test_semantic_input_rejects_unregistered_entry_subclass_and_duck_type(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    sales = catalog.domains.get("sales")

    class UnregisteredDomainEntry(DomainEntry):
        pass

    forged = UnregisteredDomainEntry(
        ref=sales.ref,
        _details=sales.details(),
        _catalog=catalog,
    )

    with pytest.raises(SemanticRuntimeError, match="not a registered concrete") as subclass:
        _normalize_semantic_input(
            catalog,
            forged,
            allowed_kinds=frozenset({SemanticKind.DOMAIN}),
            location="test.domain",
        )
    assert subclass.value.received == "UnregisteredDomainEntry(domain:sales)"

    class DuckDomain:
        ref = sales.ref

    with pytest.raises(SemanticRuntimeError, match="duck-typed") as duck:
        _normalize_semantic_input(
            catalog,
            DuckDomain(),  # type: ignore[arg-type]
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
    tmp_path: Path,
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    other_catalog = _catalog(
        semantic_project_factory,
        workspace_dir=other_workspace,
    )
    foreign_domain = other_catalog.domains.get("sales")

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
    stale_domain = old_catalog.domains.get("sales")
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
    assert error.repair.snippet == "entry = catalog.domains.get('sales')"
    namespace: dict[str, object] = {"catalog": current_catalog}
    exec(error.repair.snippet, namespace)
    assert namespace["entry"] is current_catalog.domains.get("sales")


def test_semantic_input_stale_missing_path_requires_explicit_current_choice(
    tmp_path: Path,
    semantic_project_factory,
) -> None:
    old_catalog = _catalog(semantic_project_factory)
    stale_domain = old_catalog.domains.get("ops")
    # The current catalog no longer defines the ops domain in the same root.
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


def test_domain_card_advertises_live_navigation_counts(semantic_project_factory) -> None:
    rendered = _catalog(semantic_project_factory).domains.get("sales").render()

    for expected in (
        "entry.entities.show(): count=2",
        "entry.fields.show(): count=5",
        "entry.metrics.show(): count=1",
        "entry.relationships.show(): count=2",
    ):
        assert expected in rendered


def test_zero_count_navigation_remains_visible(semantic_project_factory) -> None:
    rendered = _catalog(semantic_project_factory).domains.get("ops").render()

    empty = rendered.split("empty_collections: ", 1)[1].splitlines()[0]
    assert "metrics" in empty
    assert "relationships" in empty
    assert "period_calendars" in empty


def test_relationship_contract_discloses_typed_endpoints_and_cardinality(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    rendered = catalog.relationships.get("orders_to_users").contract().render()

    assert "from: entity:sales.orders" in rendered
    assert "to: entity:sales.users" in rendered
    assert "cardinality: many_to_one" in rendered


def test_object_and_collection_repr_are_bounded(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert repr(catalog.describe(ms.ref.metric("sales.revenue"))).startswith("<ExpressionContract ")
    assert "call .show()" in repr(catalog.describe(ms.ref.metric("sales.revenue")))
    assert repr(catalog.metrics) == (
        "<CatalogCollection metrics scope=catalog count=1; call .show() to inspect>"
    )
    assert repr(catalog.domains.get("sales").metrics) == (
        "<CatalogCollection metrics scope=domain:sales count=1; call .show() to inspect>"
    )
    assert "\n" not in repr(catalog.metrics)


def test_exact_collection_lookup_does_not_enumerate_members(
    semantic_project_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = catalog.metrics.get("sales.revenue")
    key = "sales.revenue"
    global_collection = catalog.metrics
    scoped_collection = catalog.domains.get("sales").metrics

    def reject_items(_collection: object) -> None:
        pytest.fail("exact lookup enumerated collection members")

    monkeypatch.setattr(CatalogCollection, "items", property(reject_items))
    assert global_collection.get(key) is revenue
    assert scoped_collection.get(key) is revenue


def test_collection_with_unknown_scope_remains_empty(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    collection = CatalogCollection(catalog, "metrics", scope_ref=ms.ref.entity("sales.missing"))
    assert collection.items == ()
    assert len(collection) == 0
    with pytest.raises(
        SemanticRuntimeError, match=r"outside collection scope entity:sales\.missing"
    ):
        collection.get("sales.revenue")


def test_navigation_preserves_declared_ownership_and_relationship_order(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    warehouse = catalog.datasources.get("warehouse")

    assert [ref.key for ref in catalog.domains.get("sales").details().children] == [
        "entity:sales.orders",
        "entity:sales.users",
        "metric:sales.revenue",
    ]
    assert [ref.key for ref in catalog.domains.get("ops").details().children] == [
        "entity:ops.events"
    ]
    assert [ref.key for ref in catalog.relationships.refs] == [
        "relationship:sales.orders_self",
        "relationship:sales.orders_to_users",
    ]
    # Datasource membership follows declaration order, not metric dependency.
    assert warehouse.entities.refs == (
        ms.ref.entity("ops.events"),
        ms.ref.entity("sales.orders"),
        ms.ref.entity("sales.users"),
    )
    assert warehouse.details().dependents == warehouse.entities.refs
    assert consume_metric(catalog.metrics.get("sales.revenue")).roots == (
        catalog.entities.get("sales.orders").ref,
    )
