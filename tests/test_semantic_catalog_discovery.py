"""Tests for semantic layer discovery fixes.

Covers four related catalog discovery behaviors:
1. catalog.metrics returns metrics at top level
2. catalog.<collection> provides typed browsing
3. DomainDetails and other *Details types have no render/show/repr
4. Catalog objects expose typed children via details() only
"""

from __future__ import annotations

import textwrap

import pytest

from marivo.semantic.catalog import (
    DatasourceDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
    Relationship,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticRef,
    SimpleMetricDetails,
    TimeDimensionDetails,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import ParityStatus, SourceLocation
from marivo.semantic.refs import make_ref

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
    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=md.table("orders"))

    @ms.dimension(entity=orders)
    def region(table):
        return table.region

    @ms.time_dimension(entity=orders, granularity="day", parse=ms.timestamp(timezone="UTC"))
    def created_at(table):
        return table.created_at

    @ms.metric(
        entities=[orders],
        additivity="additive",
    )
    def revenue(table):
        return table.amount.sum()
""")


def _make_catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def _make_multi_domain_catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=md.table('events'))\n"
                "@ms.metric(entities=[events], additivity='additive', )\n"
                "def event_count(table):\n"
                "    return table.id.nunique()\n"
            ),
        }
    )
    return SemanticCatalog(project)


def _make_ref(r: str, kind: SemanticKind) -> SemanticRef:
    return make_ref(r, kind)


def _make_ctx():
    from marivo.semantic.catalog import AiContextView

    return AiContextView(
        business_definition=None,
        guardrails=(),
        synonyms=(),
        examples=(),
        instructions=None,
        owner_notes=None,
    )


def _make_loc() -> SourceLocation:
    return SourceLocation(file="models/semantic/sales/_domain.py", line=5)


def _common_details_kwargs(*, python_symbol: str = "") -> dict[str, object]:
    return {
        "python_symbol": python_symbol,
    }


# ---------------------------------------------------------------------------
# Change 1: catalog.metrics returns metrics at top level
# ---------------------------------------------------------------------------


def test_discovery_metrics_returns_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    assert len(result.items) >= 1
    assert all(str(obj.ref.kind) == "metric" for obj in result.items)


def test_discovery_metrics_includes_revenue(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    refs = {obj.ref.id for obj in result.items}
    assert "sales.revenue" in refs


def test_discovery_metrics_cross_domain(semantic_project_factory):
    catalog = _make_multi_domain_catalog(semantic_project_factory)
    result = catalog.metrics
    refs = {obj.ref.id for obj in result.items}
    assert "sales.revenue" in refs
    assert "ops.event_count" in refs


def test_discovery_entities_returns_entities(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.entities
    assert len(result.items) >= 1
    assert all(str(obj.ref.kind) == "entity" for obj in result.items)


def test_discovery_dimensions_returns_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.dimensions
    assert len(result.items) >= 1
    assert all(str(obj.ref.kind) == "dimension" for obj in result.items)


def test_discovery_time_dimensions_returns_time_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.time_dimensions
    assert len(result.items) >= 1
    assert all(str(obj.ref.kind) == "time_dimension" for obj in result.items)


def test_discovery_relationships_returns_relationships(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
                "users = ms.entity(name='users', datasource=md.ref('datasource.warehouse'), source=md.table('users'))\n"
                "@ms.dimension(entity=orders)\n"
                "def user_id(table):\n"
                "    return table.user_id\n"
                "@ms.dimension(entity=users)\n"
                "def id(table):\n"
                "    return table.id\n"
                "ms.relationship(\n"
                "    name='orders_to_users',\n"
                "    from_entity=orders,\n"
                "    to_entity=users,\n"
                "    keys=[ms.join_on(user_id, id)],\n"
                ")\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)
    result = catalog.relationships
    assert len(result.items) >= 1
    assert all(isinstance(obj, Relationship) for obj in result.items)


def test_discovery_top_level_domains_and_datasources(
    semantic_project_factory,
):
    """Top-level domain/datasource collections return only container kinds."""
    catalog = _make_catalog(semantic_project_factory)
    domain_kinds = {str(obj.ref.kind) for obj in catalog.domains.items}
    ds_kinds = {str(obj.ref.kind) for obj in catalog.datasources.items}
    assert domain_kinds == {"domain"}
    assert ds_kinds == {"datasource"}


# ---------------------------------------------------------------------------
# Change 2: Typed collections provide scoped browsing via collection.get(...)
# ---------------------------------------------------------------------------


def test_discovery_entities_collection_returns_entities(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    entity_result = catalog.entities
    metric_result = catalog.metrics
    assert any(str(obj.ref.kind) == "entity" for obj in entity_result.items)
    assert any(str(obj.ref.kind) == "metric" for obj in metric_result.items)


def test_discovery_metrics_collection_has_revenue(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    assert all(str(obj.ref.kind) == "metric" for obj in result.items)
    assert any(obj.ref.id == "sales.revenue" for obj in result.items)


def test_discovery_entities_collection_has_orders(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.entities
    assert all(str(obj.ref.kind) == "entity" for obj in result.items)
    assert any(obj.ref.id == "sales.orders" for obj in result.items)


def test_discovery_multi_domain_metrics_contains_both(semantic_project_factory):
    catalog = _make_multi_domain_catalog(semantic_project_factory)
    result = catalog.metrics
    refs = {obj.ref.id for obj in result.items}
    assert "ops.event_count" in refs
    assert "sales.revenue" in refs


def test_discovery_unknown_domain_raises_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("domain.nonexistent")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


# ---------------------------------------------------------------------------
# Change 3: *Details types have render/show/repr
# ---------------------------------------------------------------------------


def test_discovery_domain_details_repr_is_single_line():
    d = DomainDetails(
        ref=_make_ref("sales", SemanticKind.DOMAIN),
        kind=SemanticKind.DOMAIN,
        name="sales",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(),
        children=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        dependents=(),
        **_common_details_kwargs(),
        owner="Mina Zhang",
        default=True,
    )
    r = repr(d)
    assert isinstance(r, str)
    assert "\n" not in r
    assert "sales" in r


def test_discovery_domain_details_render_returns_str():
    d = DomainDetails(
        ref=_make_ref("sales", SemanticKind.DOMAIN),
        kind=SemanticKind.DOMAIN,
        name="sales",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(),
        children=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        dependents=(),
        **_common_details_kwargs(),
        owner="Mina Zhang",
        default=True,
    )
    rendered = d.render()
    assert isinstance(rendered, str)
    assert "sales" in rendered
    assert "owner: Mina Zhang" in rendered
    assert "children" in rendered


def test_discovery_domain_details_show_prints_output(capsys):
    d = DomainDetails(
        ref=_make_ref("sales", SemanticKind.DOMAIN),
        kind=SemanticKind.DOMAIN,
        name="sales",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(),
        children=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        dependents=(),
        **_common_details_kwargs(),
        owner="Mina Zhang",
        default=True,
    )
    d.show()
    out = capsys.readouterr().out
    assert "sales" in out


def test_discovery_metric_details_repr_is_single_line():
    d = SimpleMetricDetails(
        ref=_make_ref("sales.revenue", SemanticKind.METRIC),
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol="revenue"),
        entities=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        root_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        aggregation=None,
        measure=None,
        additivity="additive",
        fanout_policy="block",
        unit=None,
        provenance=None,
        parity_status=ParityStatus.UNVERIFIED,
        fold=None,
        status_time_dimension=None,
    )
    r = repr(d)
    assert isinstance(r, str)
    assert "\n" not in r
    assert "revenue" in r


def test_discovery_metric_details_render_shows_additivity():
    d = SimpleMetricDetails(
        ref=_make_ref("sales.revenue", SemanticKind.METRIC),
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol="revenue"),
        entities=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        root_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        aggregation=None,
        measure=None,
        additivity="additive",
        fanout_policy="block",
        unit=None,
        provenance=None,
        parity_status=ParityStatus.UNVERIFIED,
        fold=None,
        status_time_dimension=None,
    )
    rendered = d.render()
    assert "additive" in rendered


def test_discovery_datasource_details_repr():
    d = DatasourceDetails(
        ref=_make_ref("warehouse", SemanticKind.DATASOURCE),
        kind=SemanticKind.DATASOURCE,
        name="warehouse",
        domain=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol="warehouse"),
        backend_type="duckdb",
        fields={"path": ":memory:"},
        env_refs={},
    )
    r = repr(d)
    assert "warehouse" in r


def test_discovery_entity_details_render():
    from marivo.semantic.dtos import TableSource

    d = EntityDetails(
        ref=_make_ref("sales.orders", SemanticKind.ENTITY),
        kind=SemanticKind.ENTITY,
        name="orders",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("warehouse", SemanticKind.DATASOURCE),),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol="orders"),
        datasource=_make_ref("warehouse", SemanticKind.DATASOURCE),
        source=TableSource(table="orders", database=None),
        primary_key=("order_id",),
        versioning=None,
    )
    rendered = d.render()
    assert isinstance(rendered, str)
    assert "orders" in rendered


def test_discovery_dimension_details_render():
    d = DimensionDetails(
        ref=_make_ref("sales.orders.region", SemanticKind.DIMENSION),
        kind=SemanticKind.DIMENSION,
        name="region",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol="region"),
        entity=_make_ref("sales.orders", SemanticKind.ENTITY),
    )
    rendered = d.render()
    assert "region" in rendered


def test_discovery_time_dimension_details_render():
    d = TimeDimensionDetails(
        ref=_make_ref("sales.orders.created_at", SemanticKind.TIME_DIMENSION),
        kind=SemanticKind.TIME_DIMENSION,
        name="created_at",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol="created_at"),
        entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        parse_kind="timestamp",
        data_type="timestamp",
        granularity="day",
        format=None,
        timezone="UTC",
        is_default=True,
        sample_interval=None,
    )
    rendered = d.render()
    assert "created_at" in rendered


def test_discovery_relationship_details_render():
    d = RelationshipDetails(
        ref=_make_ref("sales.orders_to_users", SemanticKind.RELATIONSHIP),
        kind=SemanticKind.RELATIONSHIP,
        name="orders_to_users",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(
            _make_ref("sales.orders", SemanticKind.ENTITY),
            _make_ref("sales.users", SemanticKind.ENTITY),
        ),
        children=(),
        dependents=(),
        **_common_details_kwargs(),
        from_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        to_entity=_make_ref("sales.users", SemanticKind.ENTITY),
        from_keys=("user_id",),
        to_keys=("id",),
    )
    rendered = d.render()
    assert "orders_to_users" in rendered


# ---------------------------------------------------------------------------
# Change 4: Catalog object children via details() only
# ---------------------------------------------------------------------------


def test_discovery_domain_object_children_returns_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    domain_obj = catalog.get("domain.sales")
    children = domain_obj.details().children
    assert isinstance(children, tuple)
    child_refs = {r.id for r in children}
    assert "sales.orders" in child_refs
    assert "sales.revenue" in child_refs


def test_discovery_entity_object_children_returns_field_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    entity_obj = catalog.get("entity.sales.orders")
    children = entity_obj.details().children
    assert isinstance(children, tuple)
    child_refs = {r.id for r in children}
    assert "sales.orders.region" in child_refs or "sales.orders.created_at" in child_refs


def test_discovery_metric_object_children_returns_empty_tuple(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    metric_obj = catalog.get("metric.sales.revenue")
    assert metric_obj.details().children == ()


def test_discovery_dimension_object_children_returns_empty_tuple(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    dim_obj = catalog.get("dimension.sales.orders.region")
    assert dim_obj.details().children == ()
