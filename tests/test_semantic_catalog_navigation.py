"""Tests for typed catalog navigation — global collections, scoped collections,
and the navigation matrix."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.refs import DimensionKind, Ref, SemanticKind
from marivo.render import AgentResult
from marivo.semantic._capabilities.catalog_members import CATALOG_MEMBER_CONTRACTS
from marivo.semantic.catalog import (
    CatalogCollection,
    CatalogEntry,
    DatasourceEntry,
    DimensionEntry,
    DomainEntry,
    EntityEntry,
    EventEntry,
    MeasureEntry,
    MetricEntry,
    RelationshipEntry,
    SemanticCatalog,
    TimeDimensionEntry,
    _normalize_semantic_input,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError

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
    primary_key=["user_id"],
)
users = ms.entity(name="users", datasource=ms.ref.datasource("warehouse"), source=md.table("users"))

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

@ms.event(
    name="order_created",
    identity=(user_id,),
    occurred_at=ordered_at,
    participants=(ms.participant(name="order", cardinality="one"),),
)
def order_created(rows):
    return ms.all_rows()
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
        ("events", EventEntry, "event:sales.order_created"),
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


def test_every_displayed_collection_key_round_trips_through_get(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    for member in CATALOG_MEMBER_CONTRACTS:
        collection = getattr(catalog, member.property_name)
        for entry in collection.items:
            assert collection.get(entry.key) is entry


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
        assert member.property_name in rendered
        assert member.entry_type_name in rendered
    assert catalog.definition_fingerprint in rendered
    assert catalog.show() is None
    assert capsys.readouterr().out.rstrip() == rendered


def test_renderable_catalog_objects_expose_only_public_dir_members(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    assert "metrics" in dir(catalog)
    assert "render" in dir(catalog)
    assert "_index" not in dir(catalog)
    assert "items" in dir(catalog.metrics)
    assert "get" in dir(catalog.metrics)
    assert "_catalog" not in dir(catalog.metrics)


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
    assert "ms.ref.<kind>(path)" in message


def test_catalog_require_remains_ref_only(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = catalog.metrics.get("sales.revenue")

    with pytest.raises(SemanticRuntimeError, match="requires an exact Ref") as exc_info:
        catalog.require(revenue)  # type: ignore[arg-type]

    assert "Pass entry.ref" in str(exc_info.value)


def test_collection_get_rejects_invalid_full_path_for_collection_kind(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get("entity.sales.orders")

    assert "metric ref path must contain exactly 2 segments" in str(exc_info.value)


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

    assert metric.key == "metric:sales.revenue"


def test_collection_get_accepts_displayed_typed_key_in_scope(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    metric = catalog.domains.get("sales").metrics.get("metric:sales.revenue")

    assert metric is catalog.metrics.get("sales.revenue")


def test_collection_get_reports_missing_displayed_typed_key(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get("metric:sales.missing")

    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert exc_info.value.semantic_refs == ("metric:sales.missing",)
    assert "metric:sales.revenue" in str(exc_info.value)


@pytest.mark.parametrize(
    "key",
    (
        "unknown:sales.revenue",
        "metric:sales",
    ),
)
def test_collection_get_rejects_malformed_typed_key_with_current_candidates(
    semantic_project_factory,
    key: str,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get(key)

    error = exc_info.value
    assert error.kind == ErrorKind.INVALID_REF
    assert error.expected == "metric:<path>"
    assert error.received == key
    assert error.repair is not None
    assert error.repair.candidates == ("metric:sales.revenue",)


def test_collection_get_ambiguous_short_name_teaches_scope_narrowing(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.dimensions.get("region")

    assert exc_info.value.kind == ErrorKind.AMBIGUOUS_REFERENCE
    assert "catalog.dimensions.get('sales.orders.region')" in str(exc_info.value)
    assert "catalog.dimensions.get('ops.events.region')" in str(exc_info.value)


def test_collection_get_scoped_short_name_resolves_uniquely(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    region = catalog.domains.get("sales").entities.get("orders").dimensions.get("region")

    assert region.key == "dimension:sales.orders.region"


def test_collection_get_accepts_exact_same_kind_ref_globally_and_in_scope(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    region_ref = ms.ref.dimension("sales.orders.region")

    global_region = catalog.dimensions.get(region_ref)
    scoped_region = catalog.entities.get("sales.orders").dimensions.get(region_ref)

    assert global_region is scoped_region


def test_collection_get_accepts_full_path_in_scope(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    region = catalog.entities.get("sales.orders").dimensions.get("sales.orders.region")

    assert region.key == "dimension:sales.orders.region"


@pytest.mark.parametrize(
    "key",
    (
        "ops.events.region",
        "dimension:ops.events.region",
        ms.ref.dimension("ops.events.region"),
    ),
)
def test_collection_get_full_path_and_ref_cannot_escape_scope(
    semantic_project_factory,
    key: str | Ref[DimensionKind],
) -> None:
    catalog = _catalog(semantic_project_factory)
    sales_dimensions = catalog.domains.get("sales").dimensions

    with pytest.raises(SemanticRuntimeError) as exc_info:
        sales_dimensions.get(key)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "outside collection scope domain:sales" in message
    assert "hard visibility boundary" in message
    assert "catalog.require(ms.ref.dimension('ops.events.region'))" in message


def test_collection_get_wrong_kind_ref_names_correct_collection(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get(ms.ref.entity("sales.orders"))  # type: ignore[arg-type]

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "Expected metric, received entity" in str(exc_info.value)
    assert "catalog.entities" in str(exc_info.value)


def test_collection_get_wrong_kind_typed_key_names_correct_collection(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.metrics.get("entity:sales.orders")

    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert exc_info.value.semantic_refs == ("entity:sales.orders",)
    assert "Expected metric, received entity" in str(exc_info.value)
    assert "catalog.entities" in str(exc_info.value)


def test_verify_accepts_metric_and_event_entries_like_their_refs(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    for entry in (
        catalog.metrics.get("sales.revenue"),
        catalog.events.get("sales.order_created"),
    ):
        by_entry = catalog.verify(entry)
        by_ref = catalog.verify(entry.ref)

        assert by_entry == by_ref
        assert by_entry.ref == entry.path


def test_semantic_input_rejects_unregistered_entry_subclass_and_duck_type(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    metric = catalog.metrics.get("sales.revenue")

    class UnregisteredMetricEntry(MetricEntry):
        pass

    forged = UnregisteredMetricEntry(
        ref=metric.ref,
        _details=metric.details(),
        _catalog=catalog,
    )

    with pytest.raises(SemanticRuntimeError, match="not a registered concrete") as subclass:
        _normalize_semantic_input(
            catalog,
            forged,
            allowed_kinds=frozenset({SemanticKind.METRIC}),
            location="test.metric",
        )
    assert subclass.value.received == "UnregisteredMetricEntry(metric:sales.revenue)"

    class DuckMetric:
        ref = metric.ref

    with pytest.raises(SemanticRuntimeError, match="duck-typed") as duck:
        _normalize_semantic_input(
            catalog,
            DuckMetric(),  # type: ignore[arg-type]
            allowed_kinds=frozenset({SemanticKind.METRIC}),
            location="test.metric",
        )
    assert duck.value.received == "DuckMetric"


def test_semantic_input_rejects_bare_string_and_wrong_kind_without_guessing(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError, match="bare strings") as bare:
        catalog.verify("sales.revenue")  # type: ignore[arg-type]
    assert bare.value.repair is not None
    assert bare.value.repair.kind == "inspect"
    assert bare.value.repair.snippet is None

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
    foreign_metric = other_catalog.metrics.get("sales.revenue")

    with pytest.raises(SemanticRuntimeError, match="another catalog") as exc_info:
        catalog.verify(foreign_metric)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert exc_info.value.repair.snippet is None


def test_semantic_input_stale_entry_has_executable_exact_reacquisition(
    semantic_project_factory,
) -> None:
    old_catalog = _catalog(semantic_project_factory)
    stale_metric = old_catalog.metrics.get("sales.revenue")
    current_catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError, match="earlier catalog instance") as exc_info:
        current_catalog.verify(stale_metric)

    error = exc_info.value
    assert error.repair is not None
    assert error.repair.kind == "reacquire"
    assert error.repair.snippet == "entry = catalog.metrics.get('sales.revenue')"
    namespace: dict[str, object] = {"catalog": current_catalog}
    exec(error.repair.snippet, namespace)
    assert namespace["entry"] is current_catalog.metrics.get("sales.revenue")


def test_semantic_input_stale_missing_path_requires_explicit_current_choice(
    semantic_project_factory,
) -> None:
    old_catalog = _catalog(semantic_project_factory)
    stale_metric = old_catalog.metrics.get("sales.revenue")
    current_project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent(_DOMAIN_PY),
            "sales/objects.py": textwrap.dedent(
                _OBJECTS_PY.replace(
                    '\nrevenue = ms.aggregate(name="revenue", measure=amount, agg="sum")\n',
                    "\n",
                )
            ),
            "ops/_domain.py": textwrap.dedent(_OPS_DOMAIN_PY),
            "ops/objects.py": textwrap.dedent(_OPS_OBJECTS_PY),
        }
    )
    current_catalog = SemanticCatalog(current_project)

    with pytest.raises(SemanticRuntimeError, match="stale catalog instance") as exc_info:
        current_catalog.verify(stale_metric)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert exc_info.value.repair.snippet is None
    assert exc_info.value.repair.candidates == ()


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
