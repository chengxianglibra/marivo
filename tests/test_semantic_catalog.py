"""Tests for marivo.semantic.catalog — SemanticCatalog public API."""

from __future__ import annotations

import textwrap
from typing import get_type_hints

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import DatasourceRef
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES
from marivo.semantic.catalog import (
    AiContextView,
    CatalogCollection,
    CatalogObject,
    Datasource,
    DatasourceDetails,
    DerivedMetricDetails,
    Dimension,
    DimensionDetails,
    Domain,
    DomainDetails,
    Entity,
    EntityDetails,
    Measure,
    MeasureDetails,
    Metric,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticRef,
    SimpleMetricDetails,
    SnapshotVersioning,
    TimeDimension,
    TimeDimensionDetails,
    ValidityVersioning,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import ParityStatus, SourceLocation, SymbolKind
from marivo.semantic.refs import DimensionRef, MeasureRef, MetricRef, TimeDimensionRef, make_ref

# --- SemanticKind ---


def test_semantic_kind_is_symbol_kind_alias():
    assert SemanticKind is SymbolKind


def test_semantic_kind_has_all_required_values():
    kinds = {str(k) for k in SemanticKind}
    assert kinds >= {
        "domain",
        "datasource",
        "entity",
        "dimension",
        "time_dimension",
        "metric",
        "relationship",
    }


# --- SemanticRef ---


def test_semantic_ref_str_returns_ref_string():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    assert str(ref) == "sales.revenue"


def test_semantic_ref_repr_includes_ref_and_kind():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    r = repr(ref)
    assert "sales.revenue" in r
    assert "MetricRef" in r  # kind encoded by subclass name


def test_semantic_ref_equality_by_value():
    a = make_ref("sales.revenue", SemanticKind.METRIC)
    b = make_ref("sales.revenue", SemanticKind.METRIC)
    assert a == b


def test_semantic_ref_is_frozen():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    with pytest.raises((AttributeError, TypeError)):
        ref.id = "other"  # type: ignore[misc]


# --- AiContextView ---


def test_ai_context_view_has_all_spec_fields():
    ctx = AiContextView(
        business_definition="Revenue from completed orders.",
        guardrails=("Exclude refunds.",),
    )
    assert ctx.business_definition == "Revenue from completed orders."
    assert ctx.guardrails == ("Exclude refunds.",)


def test_ai_context_view_defaults_to_empty():
    ctx = AiContextView(
        business_definition=None,
        guardrails=(),
    )
    assert ctx.guardrails == ()


def _make_ref(r: str, kind: SemanticKind) -> SemanticRef:
    return make_ref(r, kind)


def _make_ctx() -> AiContextView:
    return AiContextView(
        business_definition="Revenue from completed orders.",
        guardrails=("Exclude refunds.",),
    )


def _make_loc() -> SourceLocation:
    return SourceLocation(file="models/semantic/sales/_domain.py", line=5)


def _common_details_kwargs(*, python_symbol: str = "revenue") -> dict[str, object]:
    return {
        "python_symbol": python_symbol,
    }


# --- Kind-specific details ---


def test_datasource_details_fields():
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
        env_refs={"password": "WAREHOUSE_PASSWORD"},
    )
    assert d.backend_type == "duckdb"
    assert d.domain is None
    assert d.fields == {"path": ":memory:"}
    assert d.env_refs == {"password": "WAREHOUSE_PASSWORD"}


def test_domain_details_fields():
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
        **_common_details_kwargs(python_symbol=""),
        owner="Mina Zhang",
        default=True,
    )
    assert d.children[0].id == "sales.orders"
    assert d.owner == "Mina Zhang"
    assert d.default is True


def test_entity_details_fields():
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
    assert d.datasource.id == "datasource.warehouse"
    assert not hasattr(d.datasource, "name")
    assert d.primary_key == ("order_id",)
    assert d.versioning is None


def test_snapshot_versioning_fields():
    v = SnapshotVersioning(
        kind="snapshot",
        partition_field="created_date",
        grain="day",
        timezone="UTC",
        format="%Y-%m-%d",
    )
    assert v.kind == "snapshot"
    assert v.grain == "day"


def test_validity_versioning_fields():
    v = ValidityVersioning(
        kind="validity",
        valid_from="valid_from",
        valid_to="valid_to",
        interval="closed_open",
        open_end=(None,),
        timezone=None,
    )
    assert v.kind == "validity"
    assert v.interval == "closed_open"


def test_dimension_details_fields():
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
    assert d.entity.id == "sales.orders"
    assert not hasattr(d, "dimension_kind")


def test_measure_details_fields():
    d = MeasureDetails(
        ref=_make_ref("sales.orders.amount", SemanticKind.MEASURE),
        kind=SemanticKind.MEASURE,
        name="amount",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(_make_ref("sales.revenue", SemanticKind.METRIC),),
        **_common_details_kwargs(python_symbol="amount"),
        entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        additivity="additive",
        unit="USD",
    )
    assert d.entity.id == "sales.orders"
    assert d.additivity == "additive"
    assert d.unit == "USD"


def test_time_dimension_details_fields():
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
    assert d.parse_kind == "timestamp"
    assert d.granularity == "day"
    assert d.is_default is True
    assert d.sample_interval is None
    assert not hasattr(d, "required_prefix")


def test_metric_details_fields():
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
    assert d.metric_type == "simple"
    assert d.aggregation is None
    assert d.fold is None
    assert d.status_time_dimension is None


def test_relationship_details_fields():
    d = RelationshipDetails(
        ref=_make_ref("sales.orders_customers", SemanticKind.RELATIONSHIP),
        kind=SemanticKind.RELATIONSHIP,
        name="orders_customers",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(
            _make_ref("sales.orders", SemanticKind.ENTITY),
            _make_ref("sales.customers", SemanticKind.ENTITY),
        ),
        children=(),
        dependents=(),
        **_common_details_kwargs(python_symbol=""),
        from_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        to_entity=_make_ref("sales.customers", SemanticKind.ENTITY),
        from_keys=("customer_id",),
        to_keys=("id",),
    )
    assert d.from_keys == ("customer_id",)
    assert d.to_keys == ("id",)


# --- CatalogObject concrete-type contract ---


@pytest.mark.parametrize(
    ("typed_id", "expected_type", "expected_name"),
    [
        ("domain.sales", Domain, "sales"),
        ("datasource.warehouse", Datasource, "warehouse"),
        ("entity.sales.orders", Entity, "orders"),
        ("dimension.sales.orders.region", Dimension, "region"),
        (
            "time_dimension.sales.orders.created_at",
            TimeDimension,
            "created_at",
        ),
        ("metric.sales.revenue", Metric, "revenue"),
    ],
)
def test_catalog_get_returns_concrete_catalog_object(
    semantic_project_factory,
    typed_id: str,
    expected_type: type[CatalogObject],
    expected_name: str,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    obj = catalog.get(typed_id)

    assert type(obj) is expected_type
    assert obj.id == typed_id
    assert obj.name == expected_name
    assert obj.ref.kind.value == typed_id.partition(".")[0]


def test_catalog_object_high_frequency_surface_is_minimal(semantic_project_factory) -> None:
    revenue = _make_catalog(semantic_project_factory).get("metric.sales.revenue")

    for removed in (
        "semantic_id",
        "kind",
        "domain",
        "context",
        "source_location",
        "python_symbol",
        "children",
    ):
        assert not hasattr(revenue, removed)


def test_catalog_object_equality_is_concrete_type_and_typed_id(
    semantic_project_factory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    first = catalog.get("metric.sales.revenue")
    second = catalog.get("metric.sales.revenue")

    assert first == second
    assert hash(first) == hash(second)
    assert first != catalog.get("domain.sales")
    assert "business_definition:" in first.render(max_output_bytes=None)


# --- CatalogObject agent result protocol ---


def test_catalog_object_conforms_to_agent_result(semantic_project_factory):
    from tests.test_agent_result_protocol import assert_conforms

    catalog = _make_catalog(semantic_project_factory)
    assert_conforms(catalog.get("metric.sales.revenue"))


def test_catalog_collection_conforms_to_agent_result(semantic_project_factory):
    from tests.test_agent_result_protocol import assert_conforms

    catalog = _make_catalog(semantic_project_factory)
    assert_conforms(catalog.metrics)


def test_catalog_collection_items_property(semantic_project_factory):
    collection = _make_catalog(semantic_project_factory).metrics
    items = collection.items
    assert len(items) == 1
    assert items[0].name == "revenue"


def test_catalog_collection_refs_returns_tuple_of_refs(semantic_project_factory):
    collection = _make_catalog(semantic_project_factory).metrics
    refs = collection.refs()
    assert len(refs) == 1
    assert refs[0].id == "sales.revenue"
    assert isinstance(refs[0], SemanticRef)


def test_catalog_collection_render_returns_str(semantic_project_factory, capsys):
    rendered = _make_catalog(semantic_project_factory).metrics.render()
    assert isinstance(rendered, str)
    assert capsys.readouterr().out == ""


def test_catalog_collection_render_no_trailing_newline(semantic_project_factory):
    assert not _make_catalog(semantic_project_factory).metrics.render().endswith("\n")


def test_catalog_collection_render_contains_ref_and_kind(semantic_project_factory):
    rendered = _make_catalog(semantic_project_factory).metrics.render()
    assert "sales.revenue" in rendered
    assert "metric" in rendered


def test_catalog_collection_render_uses_refs_affordance(semantic_project_factory):
    rendered = _make_catalog(semantic_project_factory).metrics.render()
    assert "available:" in rendered
    assert "- .refs()" in rendered
    assert "- .get(...)" in rendered


def test_catalog_collection_show_prints_render(semantic_project_factory, capsys):
    collection = _make_catalog(semantic_project_factory).metrics
    result = collection.show()
    assert result is None
    out = capsys.readouterr().out
    assert "sales.revenue" in out


def test_semantic_object_details_render_points_to_verify_and_readiness(
    semantic_project_factory,
):
    catalog = _make_catalog(semantic_project_factory)
    details = catalog.get("metric.sales.revenue").details()
    rendered = details.render()
    assert "catalog.verify_object(" in rendered or "catalog.readiness(" in rendered
    assert "certify authored changes" in rendered


def test_simple_metric_details_carry_and_render_filter(semantic_project_factory) -> None:
    """A filtered count must be distinguishable from an unfiltered one in details:
    the filter is part of the persistent metric definition, so it is carried on
    SimpleMetricDetails and rendered. See MR !29 review P1.
    """
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=md.table("orders"))

                @ms.dimension(entity=orders)
                def region(table):
                    return table.region

                all_count = ms.count(name="all_count", entity=orders)
                failed_count = ms.count(
                    name="failed_count", entity=orders, filter=ms.where(region="FAILED")
                )
            """),
        }
    )
    catalog = SemanticCatalog(project)
    all_details = catalog.get("metric.sales.all_count").details()
    failed_details = catalog.get("metric.sales.failed_count").details()
    assert isinstance(all_details, SimpleMetricDetails)
    assert isinstance(failed_details, SimpleMetricDetails)
    assert all_details.filter is None
    assert failed_details.filter == (("region", "FAILED"),)
    rendered = failed_details.render()
    assert "filter" in rendered.lower()
    assert "region=FAILED" in rendered


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

_RICH_DETAILS_DATASETS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=md.ref("datasource.warehouse"),
        source=md.table("orders"),
        ai_context=ms.ai_context(
            business_definition="One row per completed order.",
            guardrails=["Exclude test orders."],
        ),
    )

    @ms.dimension(
        entity=orders,
        ai_context=ms.ai_context(
            business_definition="Region assigned to the completed order.",
            guardrails=["Do not infer sales ownership from region alone."],
        ),
    )
    def region(table):
        return table.region

    @ms.measure(
        entity=orders,
        additivity="additive",
        unit="USD",
        ai_context=ms.ai_context(
            business_definition="Gross order amount before refunds.",
            guardrails=["Does not net out refunds."],
        ),
    )
    def amount(table):
        return table.amount

    @ms.time_dimension(
        entity=orders,
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
        ai_context=ms.ai_context(
            business_definition="Order creation timestamp.",
            guardrails=["Do not use as payment settlement time."],
        ),
    )
    def created_at(table):
        return table.created_at

    revenue = ms.aggregate(
        name="revenue",
        measure=amount,
        agg="sum",
        ai_context=ms.ai_context(
            business_definition="Total gross order amount before refunds.",
            guardrails=["Do not use as net revenue."],
        ),
    )
""")


def _make_catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


# --- Top-level typed collections ---


def test_catalog_domains_returns_models_and_datasources(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    domain_refs = {obj.ref.id for obj in catalog.domains.items}
    datasource_refs = {obj.ref.id for obj in catalog.datasources.items}
    assert "sales" in domain_refs
    assert "datasource.warehouse" in datasource_refs


def test_catalog_list_xxx_attribute_error_points_to_collection_property(
    semantic_project_factory,
) -> None:
    """catalog exposes collection properties (catalog.metrics), not list_xxx()
    methods. The AttributeError must teach the correct property. See issue #32.
    """
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(AttributeError) as exc_info:
        catalog.list_metrics()  # type: ignore[attr-defined]
    message = str(exc_info.value)
    assert "list_metrics" in message
    # The teaching message must point at the collection property by usage.
    assert "catalog.metrics" in message
    # The correct property is usable.
    assert catalog.metrics is not None


def test_catalog_domains_includes_sales_model(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    refs = {obj.ref.id for obj in catalog.domains.items}
    assert "sales" in refs


def test_catalog_datasources_includes_warehouse_datasource(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    refs = {obj.ref.id for obj in catalog.datasources.items}
    assert "datasource.warehouse" in refs


def test_catalog_domains_no_stdout_during_access(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    _ = catalog.domains
    assert capsys.readouterr().out == ""


def test_catalog_domains_returns_catalog_collection(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.domains
    assert isinstance(result, CatalogCollection)


def test_catalog_domains_refs_returns_semantic_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    refs = catalog.domains.refs()
    assert all(isinstance(r, SemanticRef) for r in refs)


def test_metric_measure_and_dimension_objects_publish_exact_ref_annotations():
    assert get_type_hints(Metric)["ref"] is MetricRef
    assert get_type_hints(Measure)["ref"] is MeasureRef
    assert get_type_hints(Dimension)["ref"] is DimensionRef
    assert get_type_hints(TimeDimension)["ref"] is TimeDimensionRef


def test_catalog_typed_collections_return_exact_ref_subclasses(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)

    assert all(isinstance(ref, MetricRef) for ref in catalog.metrics.refs())
    assert all(isinstance(ref, MeasureRef) for ref in catalog.measures.refs())
    assert all(isinstance(ref, DimensionRef) for ref in catalog.dimensions.refs())
    assert all(isinstance(ref, TimeDimensionRef) for ref in catalog.time_dimensions.refs())


def test_catalog_domains_render_includes_refs_affordance(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)

    rendered = catalog.domains.render()

    assert "- .refs()" in rendered


def test_catalog_domains_render_omits_nested_browse_hint(
    semantic_project_factory,
):
    catalog = _make_catalog(semantic_project_factory)

    rendered = catalog.domains.render()

    assert "available:" in rendered


# --- Scoped collections ---


def test_catalog_entities_returns_entities(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.entities
    kinds = {str(obj.ref.kind) for obj in result.items}
    assert "entity" in kinds


def test_catalog_metrics_returns_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    kinds = {str(obj.ref.kind) for obj in result.items}
    assert "metric" in kinds


def test_catalog_entities_includes_orders_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.entities
    refs = {obj.ref.id for obj in result.items}
    assert "sales.orders" in refs


def test_catalog_metrics_includes_revenue_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    refs = {obj.ref.id for obj in result.items}
    assert "sales.revenue" in refs


def test_catalog_entities_render_uses_card_entity_listing(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)

    rendered = catalog.entities.render()

    assert "Entity" in rendered
    assert "sales.orders" in rendered


def test_catalog_relationships_collection(semantic_project_factory):
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

    assert result.ids() == ["relationship.sales.orders_to_users"]
    assert all(str(obj.ref.kind) == "relationship" for obj in result.items)


# --- Typed collections: dimensions and time dimensions ---


def test_catalog_dimensions_returns_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.dimensions
    kinds = {str(obj.ref.kind) for obj in result.items}
    assert "dimension" in kinds


def test_catalog_time_dimensions_returns_time_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.time_dimensions
    kinds = {str(obj.ref.kind) for obj in result.items}
    assert "time_dimension" in kinds


def test_catalog_dimensions_has_correct_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.dimensions
    field_refs = {obj.ref.id for obj in result.items if str(obj.ref.kind) == "dimension"}
    assert "sales.orders.region" in field_refs


def test_catalog_time_dimensions_has_correct_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.time_dimensions
    tf_refs = {obj.ref.id for obj in result.items if str(obj.ref.kind) == "time_dimension"}
    assert "sales.orders.created_at" in tf_refs


@pytest.mark.parametrize(
    ("collection_name", "expected_id"),
    [
        ("dimensions", "dimension.sales.orders.region"),
        ("time_dimensions", "time_dimension.sales.orders.created_at"),
        ("metrics", "metric.sales.revenue"),
    ],
)
def test_catalog_collection_render_uses_card_listing(
    semantic_project_factory,
    collection_name,
    expected_id,
):
    catalog = _make_catalog(semantic_project_factory)

    rendered = getattr(catalog, collection_name).render()

    assert expected_id in rendered
    assert "- .refs()" in rendered
    assert "- .get(...)" in rendered


def test_catalog_measures_render_uses_card_listing(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    rendered = catalog.measures.render()

    assert "Measure" in rendered
    assert "sales.orders.amount" in rendered
    assert "- .get(...)" in rendered


# --- Typed collection kind filtering ---


def test_catalog_metrics_returns_only_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.metrics
    assert all(str(obj.ref.kind) == "metric" for obj in result.items)
    assert len(result.items) >= 1


def test_catalog_entities_returns_only_entities(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.entities
    assert all(str(obj.ref.kind) == "entity" for obj in result.items)


def test_catalog_dimensions_returns_only_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.dimensions
    assert all(str(obj.ref.kind) == "dimension" for obj in result.items)


def test_catalog_time_dimensions_returns_only_time_dimensions(
    semantic_project_factory,
):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.time_dimensions
    assert all(str(obj.ref.kind) == "time_dimension" for obj in result.items)


# --- Typed collection properties ---


@pytest.mark.parametrize(
    "plural",
    [
        "metrics",
        "dimensions",
        "domains",
        "datasources",
        "entities",
        "measures",
        "time_dimensions",
        "relationships",
    ],
)
def test_catalog_plural_attribute_returns_typed_collection(semantic_project_factory, plural):
    catalog = _make_catalog(semantic_project_factory)
    collection = getattr(catalog, plural)
    assert isinstance(collection, CatalogCollection)


def test_catalog_unknown_attribute_raises_plain_attribute_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(AttributeError):
        _ = catalog.totally_unknown_attribute  # type: ignore[attr-defined]


# --- catalog.get() ---


def test_catalog_get_returns_semantic_object_for_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("domain.sales")
    assert obj.ref.id == "sales"
    assert str(obj.ref.kind) == "domain"
    assert obj.details().owner == "Mina Zhang"


def test_catalog_get_returns_semantic_object_for_datasource(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("datasource.warehouse")
    assert obj.ref.id == "datasource.warehouse"
    assert not hasattr(obj.ref, "name")
    assert str(obj.ref.kind) == "datasource"


def test_catalog_get_returns_semantic_object_for_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("entity.sales.orders")
    assert obj.ref.id == "sales.orders"
    assert str(obj.ref.kind) == "entity"
    assert obj.details().domain == "sales"


def test_catalog_get_returns_semantic_object_for_dimension(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("dimension.sales.orders.region")
    assert obj.ref.id == "sales.orders.region"
    assert str(obj.ref.kind) == "dimension"


def test_catalog_get_returns_semantic_object_for_time_dimension(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("time_dimension.sales.orders.created_at")
    assert str(obj.ref.kind) == "time_dimension"


def test_catalog_get_returns_semantic_object_for_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("metric.sales.revenue")
    assert obj.ref.id == "sales.revenue"
    assert str(obj.ref.kind) == "metric"


def test_catalog_get_rejects_semantic_ref_input(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get(ref)  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "<kind>.<semantic_id>" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    ["sales.orders", "sales.revenue", "sales.orders.region"],
)
def test_catalog_get_rejects_bare_semantic_ids(semantic_project_factory, raw):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get(raw)
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "<kind>.<semantic_id>" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    ["sales", "warehouse"],
)
def test_catalog_get_rejects_short_names_with_typed_id_hint(semantic_project_factory, raw):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get(raw)
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "catalog.get(" in str(exc_info.value)


def test_catalog_get_kind_mismatch_raises_not_found(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("metric.sales.orders.region")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert "metric" in str(exc_info.value)


def test_catalog_get_not_found_raises_typed_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("metric.sales.nonexistent")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


def test_catalog_get_not_found_error_mentions_browse_hint(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("metric.sales.missing")
    msg = str(exc_info.value)
    assert "catalog.metrics" in msg or "catalog.domains" in msg


def test_catalog_get_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.get("metric.sales.revenue")
    assert capsys.readouterr().out == ""


def test_catalog_get_context_matches_authored_ai_context(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=md.table("orders"))

                @ms.metric(
                    entities=[orders],
                    additivity="additive",
                    ai_context=ms.ai_context(business_definition="All completed order amounts."),
                )
                def revenue(table):
                    return table.amount.sum()
            """),
        }
    )
    catalog = SemanticCatalog(project)
    obj = catalog.get("metric.sales.revenue")
    assert obj.details().context.business_definition == "All completed order amounts."


def test_catalog_get_business_definition_matches_authored_context(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)
    obj = catalog.get("metric.sales.revenue")
    assert obj.details().context.business_definition == "Total gross order amount before refunds."


def test_catalog_get_source_location_is_populated(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("metric.sales.revenue")
    loc = obj.details().source_location
    assert loc.file != ""
    assert loc.line > 0


def test_catalog_details_expose_ai_context_via_context_field(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    cases = {
        "entity.sales.orders": "orders",
        "dimension.sales.orders.region": "region",
        "measure.sales.orders.amount": "amount",
        "time_dimension.sales.orders.created_at": "created_at",
        "metric.sales.revenue": "revenue",
    }
    for ref, python_symbol in cases.items():
        details = catalog.get(ref).details()
        assert details.context.business_definition
        assert details.context.guardrails
        assert details.python_symbol == python_symbol
        assert details.source_location.file
        assert details.source_location.line > 0


def test_catalog_details_render_includes_agent_consumption_context(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    metric_rendered = catalog.get("metric.sales.revenue").details().render()
    assert "business_definition: Total gross order amount before refunds." in metric_rendered
    assert "guardrails:" in metric_rendered
    assert "- Do not use as net revenue." in metric_rendered
    assert "source_location:" in metric_rendered
    assert "python_symbol: revenue" in metric_rendered
    assert "parents: sales.orders" in metric_rendered
    assert "measure: sales.orders.amount" in metric_rendered
    assert "parity_status:" in metric_rendered

    entity_rendered = catalog.get("entity.sales.orders").details().render()
    assert "datasource: datasource.warehouse" in entity_rendered
    assert "source:" in entity_rendered
    assert "children:" in entity_rendered
    assert "sales.orders.region" in entity_rendered


def test_catalog_details_render_bounds_long_business_definition():
    details = SimpleMetricDetails(
        ref=_make_ref("sales.revenue", SemanticKind.METRIC),
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        context=AiContextView(
            business_definition="Revenue detail. " * 1000,
            guardrails=(),
        ),
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

    rendered = details.render()

    assert len(rendered.encode()) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert "available:" in rendered


def test_catalog_datasource_details_do_not_expose_secret_values(
    semantic_project_factory,
    monkeypatch,
):
    monkeypatch.setenv("TRINO_AUTH", "plaintext-secret")
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                "md.trino(\n"
                "    name='warehouse', host='h', catalog='c', auth_env='TRINO_AUTH')\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.get("datasource.warehouse").details()
    assert isinstance(details, DatasourceDetails)
    assert details.fields == {"host": "h", "catalog": "c"}
    assert details.env_refs == {"auth": "TRINO_AUTH"}
    rendered = details.render()
    assert "TRINO_AUTH" in rendered
    assert "plaintext-secret" not in rendered
    assert "auth: TRINO_AUTH" in rendered


def test_catalog_get_dataset_details_correct_datasource_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("entity.sales.orders")
    d = obj.details()
    assert isinstance(d, EntityDetails)
    assert isinstance(d.datasource, DatasourceRef)
    assert d.datasource.id == "datasource.warehouse"
    assert not hasattr(d.datasource, "name")


def test_catalog_entities_under_datasource_uses_typed_datasource_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)

    result = catalog.entities

    assert [obj.ref.id for obj in result.items] == ["sales.orders"]


def test_catalog_entity_details_source_uses_shared_ir_type(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    from marivo.datasource.ir import TableSourceIR

    details = catalog.get("entity.sales.orders").details()
    assert isinstance(details, EntityDetails)
    assert isinstance(details.source, TableSourceIR)
    assert details.source.to_dict()["table"] == "orders"


def test_catalog_get_metric_details_correct_dataset_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("metric.sales.revenue")
    d = obj.details()
    assert isinstance(d, MetricDetails)
    assert any(r.id == "sales.orders" for r in d.entities)


def test_catalog_metric_details_components_are_role_keyed(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def revenue(table):\n"
                "    return table.amount.sum()\n"
                "@ms.metric(entities=[orders], additivity='additive', )\n"
                "def order_count(table):\n"
                "    return table.order_id.nunique()\n"
                "conversion = ms.ratio(\n"
                "    name='conversion',\n"
                "    numerator=revenue, denominator=order_count,\n"
                ")\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.get("metric.sales.conversion").details()

    assert isinstance(details, DerivedMetricDetails)
    assert details.components == (
        ("numerator", make_ref("sales.revenue", SemanticKind.METRIC)),
        ("denominator", make_ref("sales.order_count", SemanticKind.METRIC)),
    )
    rendered = details.render()
    assert "composition: ratio" in rendered
    assert "components: numerator=sales.revenue, denominator=sales.order_count" in rendered


def test_metric_details_project_effective_scope_and_measure_lineage(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms

                queries = ms.entity(
                    name="queries",
                    datasource=md.ref("datasource.warehouse"),
                    source=md.table("queries"),
                )
                cluster = ms.dimension_column(
                    name="cluster", entity=queries, column="cluster"
                )
                occurred_at = ms.time_dimension_column(
                    name="occurred_at",
                    entity=queries,
                    column="occurred_at",
                    granularity="second",
                    parse=ms.timestamp(timezone="UTC"),
                )
                cache_bytes = ms.measure_column(
                    name="cache_bytes",
                    entity=queries,
                    column="cache_bytes",
                    additivity="additive",
                )
                input_bytes = ms.measure_column(
                    name="input_bytes",
                    entity=queries,
                    column="input_bytes",
                    additivity="additive",
                )
                total_cache_bytes = ms.aggregate(
                    name="total_cache_bytes", measure=cache_bytes, agg="sum"
                )
                total_input_bytes = ms.aggregate(
                    name="total_input_bytes", measure=input_bytes, agg="sum"
                )
                query_count = ms.count(name="query_count", entity=queries)
                cache_hit_rate = ms.ratio(
                    name="cache_hit_rate",
                    numerator=total_cache_bytes,
                    denominator=total_input_bytes,
                )
                weighted_cache = ms.weighted_average(
                    name="weighted_cache",
                    value=total_cache_bytes,
                    weight=total_input_bytes,
                )
                cache_total = ms.linear(
                    name="cache_total", add=[total_cache_bytes, total_input_bytes]
                )
                cumulative_cache = ms.cumulative(
                    name="cumulative_cache", base=total_cache_bytes, over=occurred_at
                )
                nested_rate = ms.ratio(
                    name="nested_rate",
                    numerator=cache_hit_rate,
                    denominator=query_count,
                )
            """),
        }
    )
    catalog = SemanticCatalog(project)

    simple = catalog.get("metric.sales.total_cache_bytes").details()
    assert isinstance(simple, SimpleMetricDetails)
    assert simple.entities == (make_ref("sales.queries", SemanticKind.ENTITY),)
    assert simple.effective_entities == simple.entities
    assert simple.measure_lineage == (
        ("measure", make_ref("sales.queries.cache_bytes", SemanticKind.MEASURE)),
    )

    count = catalog.get("metric.sales.query_count").details()
    assert isinstance(count, SimpleMetricDetails)
    assert count.measure_lineage == ()

    ratio = catalog.get("metric.sales.cache_hit_rate").details()
    assert isinstance(ratio, DerivedMetricDetails)
    assert ratio.entities == ()
    assert ratio.effective_entities == (make_ref("sales.queries", SemanticKind.ENTITY),)
    assert ratio.candidate_dimensions == (
        make_ref("sales.queries.cluster", SemanticKind.DIMENSION),
    )
    assert ratio.candidate_time_dimensions == (
        make_ref("sales.queries.occurred_at", SemanticKind.TIME_DIMENSION),
    )
    assert ratio.measure_lineage == (
        ("numerator", make_ref("sales.queries.cache_bytes", SemanticKind.MEASURE)),
        ("denominator", make_ref("sales.queries.input_bytes", SemanticKind.MEASURE)),
    )

    expected_lineage_by_metric = {
        "metric.sales.weighted_cache": ("value", "weight"),
        "metric.sales.cache_total": ("term0", "term1"),
        "metric.sales.cumulative_cache": ("base",),
        "metric.sales.nested_rate": (
            "numerator.numerator",
            "numerator.denominator",
        ),
    }
    for metric_id, expected_roles in expected_lineage_by_metric.items():
        details = catalog.get(metric_id).details()
        assert isinstance(details, DerivedMetricDetails)
        assert tuple(role for role, _ref in details.measure_lineage) == expected_roles

    rendered = ratio.render()
    assert "effective_entities: sales.queries" in rendered
    assert "candidate_dimensions: sales.queries.cluster" in rendered
    assert "candidate_time_dimensions: sales.queries.occurred_at" in rendered
    assert "measure_lineage: numerator=sales.queries.cache_bytes" in rendered

    metric_card = catalog.get("metric.sales.cache_hit_rate").render()
    assert "composition: ratio (2 components)" in metric_card
    assert "analysis_scope: 1 effective entities; 1 candidate dimensions;" in metric_card
    assert ".details().show() for definition, candidate axes, and measure lineage" in metric_card


def test_catalog_time_dimension_details_include_sample_interval(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
                "@ms.time_dimension(\n"
                "    entity=orders,\n"
                "    granularity='minute',\n"
                "    parse=ms.timestamp(timezone='UTC', sample_interval=(5, 'minute')),\n"
                ")\n"
                "def sampled_at(table):\n"
                "    return table.created_at\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.get("time_dimension.sales.orders.sampled_at").details()

    assert isinstance(details, TimeDimensionDetails)
    assert details.sample_interval is not None
    assert details.sample_interval.to_token() == "5minute"


def test_catalog_strptime_time_dimension_details_include_sample_interval(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
                "@ms.time_dimension(\n"
                "    entity=orders,\n"
                "    granularity='minute',\n"
                "    parse=ms.strptime(\n"
                "        '%Y%m%d%H%M%S',\n"
                "        timezone='UTC',\n"
                "        sample_interval=(5, 'minute'),\n"
                "    ),\n"
                ")\n"
                "def sampled_at(table):\n"
                "    return table.created_at_key\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.get("time_dimension.sales.orders.sampled_at").details()

    assert isinstance(details, TimeDimensionDetails)
    assert details.parse_kind == "strptime"
    assert details.sample_interval is not None
    assert details.sample_interval.to_token() == "5minute"


def test_catalog_get_model_details_children_include_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("domain.sales")
    d = obj.details()
    assert isinstance(d, DomainDetails)
    child_refs = {r.id for r in d.children}
    assert "sales.revenue" in child_refs
    assert "sales.orders" in child_refs


def test_catalog_get_dataset_details_children_include_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("entity.sales.orders")
    d = obj.details()
    assert isinstance(d, EntityDetails)
    child_refs = {r.id for r in d.children}
    assert "sales.revenue" in child_refs
    assert "sales.orders.region" in child_refs or "sales.orders.created_at" in child_refs


# --- CatalogObject handoff to readiness/verify_object ---


def test_catalog_readiness_accepts_catalog_objects(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    report = catalog.readiness(refs=[catalog.get("metric.sales.revenue")])

    assert report.status in {"ready", "ready_with_warnings", "blocked"}


def test_catalog_verify_object_accepts_catalog_object(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)

    result = catalog.verify_object(catalog.get("domain.sales"))

    assert result.status == "passed"


# --- ms.load() ---


def test_ms_load_returns_semantic_catalog(tmp_path):
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    assert isinstance(catalog, SemanticCatalog)


def test_ms_load_defaults_to_cwd(tmp_path, monkeypatch):
    _write_minimal_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    catalog = ms.load()
    assert isinstance(catalog, SemanticCatalog)


def test_ms_load_failure_raises_semantic_load_error(tmp_path):
    semantic = tmp_path / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='wrong_name', owner='Mina Zhang')\n"
    )
    from marivo.semantic.errors import SemanticLoadFailed

    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


def test_ms_load_does_not_print(tmp_path, capsys):
    _write_minimal_project(tmp_path)
    ms.load(workspace_dir=tmp_path)
    assert capsys.readouterr().out == ""


def test_ms_load_catalog_can_browse(tmp_path):
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    result = catalog.domains
    assert len(result.items) >= 1


def test_ms_load_with_domains_filters_domains(tmp_path):
    """ms.load(domains=...) filters to the specified domain directories."""
    _write_multi_domain_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path, domains=["sales"])
    refs = {obj.ref.id for obj in catalog.domains.items}
    assert "sales" in refs
    assert "ops" not in refs


def test_ms_load_with_domains_string(tmp_path):
    """ms.load(domains='sales') accepts a single domain name as a string."""
    _write_multi_domain_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path, domains="sales")
    refs = {obj.ref.id for obj in catalog.domains.items}
    assert "sales" in refs
    assert "ops" not in refs


def test_catalog_lifecycle_properties_delegate_to_project(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    assert catalog.semantic_root == project.semantic_root
    assert catalog.workspace_dir == project.workspace_dir


def test_catalog_load_reloads_project(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)
    (project.semantic_root / "sales" / "datasets.py").write_text(
        textwrap.dedent("""\
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

            @ms.metric(
                entities=[orders],
                additivity="additive",
            )
            def profit(table):
                return table.profit.sum()
        """)
    )
    with pytest.raises(SemanticRuntimeError):
        catalog.get("metric.sales.profit")

    catalog.load()

    assert project.is_ready()
    assert catalog.get("metric.sales.profit").ref.id == "sales.profit"


def test_catalog_load_preserves_filtered_model_scope(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=md.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    catalog = SemanticCatalog(project)

    catalog.load()

    refs = {obj.ref.id for obj in catalog.domains.items}
    assert "sales" in refs
    assert "ops" not in refs


def test_catalog_load_with_models_changes_filter(semantic_project_factory):
    """catalog.load(domains=...) changes the active domain filter on reload."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=md.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    catalog = SemanticCatalog(project)

    # Switch to ops domain via catalog.load(domains=...)
    catalog.load(domains="ops")

    refs = {obj.ref.id for obj in catalog.domains.items}
    assert "ops" in refs
    assert "sales" not in refs


def test_catalog_access_after_failed_load_raises_semantic_load_failed(tmp_path):
    semantic = tmp_path / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='wrong_name', owner='Mina Zhang')\n"
    )

    from marivo.semantic.errors import SemanticLoadFailed
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    catalog = SemanticCatalog(project)

    with pytest.raises(SemanticLoadFailed):
        _ = catalog.domains


def _preview_backend(path: str):
    backend = ibis.duckdb.connect(path)
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, created_at TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    return backend


def _preview_catalog_and_snapshot(semantic_project_factory, tmp_path, monkeypatch):
    database_path = tmp_path / "warehouse.duckdb"
    backend = _preview_backend(str(database_path))
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    snapshot = md.inspect(md.ref("datasource.warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "created_at"),
    )
    return catalog, snapshot


def test_catalog_preview_field_preserves_context_columns(
    semantic_project_factory, tmp_path, monkeypatch
):
    catalog, snapshot = _preview_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    preview = catalog.preview(
        catalog.get("dimension.sales.orders.region").ref,
        using=snapshot,
        context_columns=("order_id",),
        limit=2,
    )

    assert preview.ref == "sales.orders.region"
    assert preview.columns[:2] == ("order_id", "region")


def test_catalog_preview_metric_preserves_approximate_warning(
    semantic_project_factory, tmp_path, monkeypatch
):
    catalog, snapshot = _preview_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    preview = catalog.preview(
        catalog.get("metric.sales.revenue").ref,
        using=snapshot,
        limit=2,
    )

    assert preview.ref == "sales.revenue"
    assert any(w.kind == "approximate_preview" for w in preview.warnings)


def test_catalog_preview_context_columns_rejected_for_metric(
    semantic_project_factory, tmp_path, monkeypatch
):
    catalog, snapshot = _preview_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(
            catalog.get("metric.sales.revenue").ref,
            using=snapshot,
            context_columns=("order_id",),
        )

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "context_columns" in str(exc_info.value)


def _write_minimal_project(tmp_path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    ds = tmp_path / "models" / "datasources"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def revenue(table):\n"
        "    return table.amount.sum()\n"
    )


def _write_multi_domain_project(tmp_path) -> None:
    """Write a project with both 'sales' and 'ops' domains."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    ds = tmp_path / "models" / "datasources"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    sales = tmp_path / "models" / "semantic" / "sales"
    sales.mkdir(parents=True, exist_ok=True)
    (sales / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (sales / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def revenue(table):\n"
        "    return table.amount.sum()\n"
    )
    ops = tmp_path / "models" / "semantic" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n"
    )
    (ops / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=md.table('events'))\n"
    )


# --- catalog.readiness() ---


def test_catalog_readiness_returns_readiness_report(semantic_project_factory):
    from marivo.semantic.readiness import ReadinessReport

    catalog = _make_catalog(semantic_project_factory)
    report = catalog.readiness()
    assert isinstance(report, ReadinessReport)


def test_catalog_readiness_accepts_semantic_ref_values(semantic_project_factory):
    from marivo.semantic.readiness import ReadinessReport

    catalog = _make_catalog(semantic_project_factory)
    revenue_ref = catalog.get("metric.sales.revenue").ref
    report = catalog.readiness(refs=[revenue_ref])
    assert isinstance(report, ReadinessReport)


def test_catalog_readiness_rejects_string_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.readiness(refs=["sales.revenue"])  # type: ignore[list-item]
    assert exc_info.value.kind == ErrorKind.INVALID_REF


def test_catalog_readiness_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.readiness()
    assert capsys.readouterr().out == ""


# --- catalog.verify_object() ---


def test_catalog_verify_object_static_domain_passes(semantic_project_factory):
    from marivo.semantic.dtos import VerifyResult

    catalog = _make_catalog(semantic_project_factory)
    result = catalog.verify_object(catalog.get("domain.sales").ref)
    assert isinstance(result, VerifyResult)
    assert result.status == "passed"
    assert result.ref == "sales"
    assert result.kind == "domain"


def test_catalog_verify_object_static_dimension_passes(semantic_project_factory):
    from marivo.semantic.dtos import VerifyResult

    catalog = _make_catalog(semantic_project_factory)
    result = catalog.verify_object(catalog.get("dimension.sales.orders.region").ref)
    assert isinstance(result, VerifyResult)
    assert result.status == "passed"
    assert result.kind == "dimension"


def test_catalog_verify_object_accepts_semantic_ref(semantic_project_factory):
    from marivo.semantic.dtos import VerifyResult

    catalog = _make_catalog(semantic_project_factory)
    ref = make_ref("sales", SemanticKind.DOMAIN)
    result = catalog.verify_object(ref)
    assert isinstance(result, VerifyResult)
    assert result.status == "passed"


def test_catalog_verify_object_entity_level_metric_ref_suggests_domain_level(
    semantic_project_factory,
):
    """verify_object with entity-level metric ref (domain.entity.metric) should
    suggest the correct domain-level ref (domain.metric)."""
    from marivo.semantic.dtos import VerifyResult

    catalog = _make_catalog(semantic_project_factory)
    # "sales.orders.revenue" is wrong — metrics are at domain level
    result = catalog.verify_object(make_ref("sales.orders.revenue", SemanticKind.METRIC))
    assert isinstance(result, VerifyResult)
    assert result.status == "failed"
    assert result.kind == "metric"
    msg = result.issues[0].message
    assert "sales.revenue" in msg
    assert "domain level" in msg


def test_catalog_verify_object_preserves_concrete_derived_kind_on_load_failure(
    tmp_path, semantic_project_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/metrics.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
                "revenue_ratio = ms.ratio(name='revenue_ratio', numerator=revenue, denominator=revenue)\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)
    revenue_ratio = catalog.get("metric.sales.revenue_ratio")
    (tmp_path / "models/semantic/sales/metrics.py").write_text(
        "raise RuntimeError('intentional post-catalog load failure')\n"
    )

    result = catalog.verify_object(revenue_ratio)

    assert result.status == "failed"
    assert result.kind == "derived_metric"
    assert result.issues[0].kind == "project_load_failed"
    assert "intentional post-catalog load failure" in result.issues[0].message


def test_catalog_verify_object_unknown_ref_without_suggestion(semantic_project_factory):
    """verify_object with a completely unknown ref returns a not-found message
    without a level suggestion."""
    from marivo.semantic.dtos import VerifyResult

    catalog = _make_catalog(semantic_project_factory)
    result = catalog.verify_object(make_ref("nonexistent.thing", SemanticKind.ENTITY))
    assert isinstance(result, VerifyResult)
    assert result.status == "failed"
    msg = result.issues[0].message
    assert "nonexistent.thing" in msg
    assert "domain level" not in msg


def test_catalog_get_entity_level_metric_ref_suggests_domain_level(
    semantic_project_factory,
):
    """catalog.get with typed entity-level metric id should suggest the domain-level id."""
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("metric.sales.orders.revenue")
    msg = str(exc_info.value)
    assert "sales.revenue" in msg
    assert "domain level" in msg


def test_catalog_get_domain_level_dimension_ref_suggests_entity_level(
    semantic_project_factory,
):
    """catalog.get with typed domain-level dimension id should suggest the entity-level id."""
    catalog = _make_catalog(semantic_project_factory)
    # "sales.region" is wrong — dimensions are at entity level
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("dimension.sales.region")
    msg = str(exc_info.value)
    assert "sales.orders.region" in msg
    assert "entity level" in msg


# --- metric unit passthrough ---


_UNIT_DATASETS_PY = (
    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    "import marivo.datasource as md\n"
    "\n"
    "warehouse = md.ref('datasource.warehouse')\n"
    "\n"
    "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
    "\n"
    "@ms.metric(entities=[orders], additivity='additive', name='revenue', "
    " unit='CNY')\n"
    "def revenue(orders):\n"
    "    return orders.amount.sum()\n"
)

_WAREHOUSE_PY = "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"


def test_catalog_metric_details_unit_passthrough(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _UNIT_DATASETS_PY,
            "datasources/warehouse.py": _WAREHOUSE_PY,
        }
    )
    catalog = SemanticCatalog(project)
    d = catalog.get("metric.sales.revenue").details()
    assert isinstance(d, MetricDetails)
    assert d.unit == "CNY"


def test_catalog_metric_details_unit_defaults_to_none(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    d = catalog.get("metric.sales.revenue").details()
    assert d.unit is None


def test_catalog_details_cover_all_public_ir_fields() -> None:
    from dataclasses import fields

    from marivo.datasource.ir import DatasourceIR
    from marivo.semantic.catalog import (
        DatasourceDetails,
        DerivedMetricDetails,
        DimensionDetails,
        DomainDetails,
        EntityDetails,
        MeasureDetails,
        RelationshipDetails,
        SimpleMetricDetails,
        TimeDimensionDetails,
    )
    from marivo.semantic.ir import (
        DimensionIR,
        DomainIR,
        EntityIR,
        MeasureIR,
        MetricIR,
        RelationshipIR,
    )

    coverage = {
        DatasourceIR: {field.name for field in fields(DatasourceDetails)}
        | {"ref", "source_location", "context"},
        DomainIR: {field.name for field in fields(DomainDetails)}
        | {"ref", "source_location", "context"},
        EntityIR: {field.name for field in fields(EntityDetails)}
        | {"ref", "source_location", "context"},
        DimensionIR: {field.name for field in fields(DimensionDetails)}
        | {field.name for field in fields(TimeDimensionDetails)}
        | {"ref", "source_location", "context"},
        MeasureIR: {field.name for field in fields(MeasureDetails)}
        | {"ref", "source_location", "context"},
        MetricIR: {field.name for field in fields(SimpleMetricDetails)}
        | {field.name for field in fields(DerivedMetricDetails)}
        | {"ref", "source_location", "context"},
        RelationshipIR: {field.name for field in fields(RelationshipDetails)}
        | {"ref", "source_location", "context", "keys"},
    }
    allowed_internal = {
        DatasourceIR: {"location", "ai_context", "semantic_id"},
        DomainIR: {"location", "ai_context"},
        EntityIR: {"location", "ai_context", "semantic_id"},
        DimensionIR: {
            "body_ast_hash",
            "location",
            "ai_context",
            "is_time_dimension",
            "kind",
            "semantic_id",
            "parse",
        },
        MeasureIR: {"location", "ai_context", "semantic_id", "kind", "body_ast_hash"},
        MetricIR: {
            "location",
            "ai_context",
            "body_ast_hash",
            "semantic_id",
            "fold_override",
            "metric_type",
            "unit_override",
        },
        RelationshipIR: {"location", "ai_context", "semantic_id"},
    }

    for ir_type, detail_fields in coverage.items():
        missing = {
            field.name
            for field in fields(ir_type)
            if field.name not in detail_fields and field.name not in allowed_internal[ir_type]
        }
        assert not missing, (
            f"{ir_type.__name__} fields missing from catalog details: {sorted(missing)}"
        )
