"""Tests for semantic layer discovery fixes.

Covers four related catalog discovery behaviors:
1. catalog.list(kind=SemanticKind.METRIC) returns metrics at top level
2. catalog.list(domain_ref) scopes browsing by ref
3. DomainDetails and other *Details types have no render/show/repr
4. SemanticObject has no public children property
"""

from __future__ import annotations

import textwrap

import pytest

from marivo.semantic.catalog import (
    DatasourceDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
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
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")

_DATASETS_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

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
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
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
# Change 1: catalog.list(kind=SemanticKind.METRIC) returns metrics at top level
# ---------------------------------------------------------------------------


def test_discovery_list_kind_metric_returns_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(kind=SemanticKind.METRIC)
    assert len(result.objects) >= 1
    assert all(str(obj.kind) == "metric" for obj in result.objects)


def test_discovery_list_kind_metric_includes_revenue(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(kind=SemanticKind.METRIC)
    refs = {obj.ref.id for obj in result.objects}
    assert "sales.revenue" in refs


def test_discovery_list_kind_metric_cross_domain(semantic_project_factory):
    catalog = _make_multi_domain_catalog(semantic_project_factory)
    result = catalog.list(kind=SemanticKind.METRIC)
    refs = {obj.ref.id for obj in result.objects}
    assert "sales.revenue" in refs
    assert "ops.event_count" in refs


def test_discovery_list_kind_entity_returns_entities(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(kind=SemanticKind.ENTITY)
    assert len(result.objects) >= 1
    assert all(str(obj.kind) == "entity" for obj in result.objects)


def test_discovery_list_kind_dimension_returns_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(kind=SemanticKind.DIMENSION)
    assert len(result.objects) >= 1
    assert all(str(obj.kind) == "dimension" for obj in result.objects)


def test_discovery_list_kind_time_dimension_returns_time_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(kind=SemanticKind.TIME_DIMENSION)
    assert len(result.objects) >= 1
    assert all(str(obj.kind) == "time_dimension" for obj in result.objects)


def test_discovery_list_kind_relationship_returns_relationships(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "users = ms.entity(name='users', datasource='warehouse', source=ms.table('users'))\n"
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
    result = catalog.list(kind=SemanticKind.RELATIONSHIP)
    assert len(result.objects) >= 1
    assert all(str(obj.kind) == "relationship" for obj in result.objects)


def test_discovery_list_no_kind_still_returns_domains_and_datasources_only(
    semantic_project_factory,
):
    """When kind is not specified, top level still shows only domains + datasources."""
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list()
    kinds = {str(obj.kind) for obj in result.objects}
    # Leaf kinds should NOT appear without explicit kind=
    assert "metric" not in kinds
    assert "entity" not in kinds
    assert "dimension" not in kinds
    # Only container kinds appear
    assert "domain" in kinds
    assert "datasource" in kinds


# ---------------------------------------------------------------------------
# Change 2: catalog.list(domain_ref) scope
# ---------------------------------------------------------------------------


def test_discovery_domain_filter_returns_domain_children(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref)
    kinds = {str(obj.kind) for obj in result.objects}
    assert "entity" in kinds
    assert "metric" in kinds


def test_discovery_domain_filter_with_kind_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.METRIC)
    assert all(str(obj.kind) == "metric" for obj in result.objects)
    assert any(obj.ref.id == "sales.revenue" for obj in result.objects)


def test_discovery_domain_filter_with_kind_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.ENTITY)
    assert all(str(obj.kind) == "entity" for obj in result.objects)
    assert any(obj.ref.id == "sales.orders" for obj in result.objects)


def test_discovery_domain_filter_multi_domain_scopes_correctly(semantic_project_factory):
    catalog = _make_multi_domain_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.ops").ref, kind=SemanticKind.METRIC)
    refs = {obj.ref.id for obj in result.objects}
    assert "ops.event_count" in refs
    assert "sales.revenue" not in refs


def test_discovery_domain_filter_unknown_domain_raises_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("domain.nonexistent")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


def test_discovery_domain_keyword_is_removed(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(TypeError):
        catalog.list(domain="sales")  # type: ignore[call-arg]


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
        default=True,
    )
    rendered = d.render()
    assert isinstance(rendered, str)
    assert "sales" in rendered
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
# Change 4: SemanticObject.children property
# ---------------------------------------------------------------------------


def test_discovery_domain_object_children_returns_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    domain_obj = catalog.get("domain.sales")
    children = domain_obj.children
    assert isinstance(children, tuple)
    child_refs = {r.id for r in children}
    assert "sales.orders" in child_refs
    assert "sales.revenue" in child_refs


def test_discovery_entity_object_children_returns_field_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    entity_obj = catalog.get("entity.sales.orders")
    children = entity_obj.children
    assert isinstance(children, tuple)
    child_refs = {r.id for r in children}
    assert "sales.orders.region" in child_refs or "sales.orders.created_at" in child_refs


def test_discovery_metric_object_children_returns_empty_tuple(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    metric_obj = catalog.get("metric.sales.revenue")
    assert metric_obj.children == ()


def test_discovery_dimension_object_children_returns_empty_tuple(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    dim_obj = catalog.get("dimension.sales.orders.region")
    assert dim_obj.children == ()
