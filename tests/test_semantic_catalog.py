"""Tests for marivo.semantic.catalog — SemanticCatalog public API."""

from __future__ import annotations

import textwrap

import ibis
import pytest

import marivo.semantic as ms
from marivo.semantic.catalog import (
    AiContextView,
    DatasourceDetails,
    DerivedMetricDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
    MeasureDetails,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticObject,
    SemanticObjectList,
    SemanticRef,
    SimpleMetricDetails,
    SnapshotVersioning,
    TimeDimensionDetails,
    ValidityVersioning,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import ParityStatus, SourceLocation, SymbolKind
from marivo.semantic.refs import make_ref

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
        synonyms=("gross revenue",),
        examples=("Q3 total: $1.2M",),
        instructions="Always filter by status='complete'.",
        owner_notes="Finance team owns this.",
    )
    assert ctx.business_definition == "Revenue from completed orders."
    assert ctx.guardrails == ("Exclude refunds.",)
    assert ctx.synonyms == ("gross revenue",)
    assert ctx.examples == ("Q3 total: $1.2M",)
    assert ctx.instructions == "Always filter by status='complete'."
    assert ctx.owner_notes == "Finance team owns this."


def test_ai_context_view_defaults_to_empty():
    ctx = AiContextView(
        business_definition=None,
        guardrails=(),
        synonyms=(),
        examples=(),
        instructions=None,
        owner_notes=None,
    )
    assert ctx.guardrails == ()


def _make_ref(r: str, kind: SemanticKind) -> SemanticRef:
    return make_ref(r, kind)


def _make_ctx() -> AiContextView:
    return AiContextView(
        business_definition="Revenue from completed orders.",
        guardrails=("Exclude refunds.",),
        synonyms=("gross revenue",),
        examples=("Q3 total: $1.2M",),
        instructions="Always filter by status='complete'.",
        owner_notes="Finance team owns this.",
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
        default=True,
    )
    assert d.children[0].id == "sales.orders"
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
    assert d.datasource.id == "warehouse"
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


def _make_metric_obj() -> SemanticObject:
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    details = SimpleMetricDetails(
        ref=ref,
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
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        context=_make_ctx(),
        source_location=_make_loc(),
        python_symbol="revenue",
        _details=details,
    )


# --- SemanticObject ---


def test_semantic_object_fields():
    obj = _make_metric_obj()
    assert obj.ref.id == "sales.revenue"
    assert obj.kind == SemanticKind.METRIC
    assert obj.name == "revenue"
    assert obj.domain == "sales"


def test_semantic_object_details_returns_typed_details():
    obj = _make_metric_obj()
    d = obj.details()
    assert isinstance(d, MetricDetails)
    assert d.metric_type == "simple"


def test_semantic_object_details_no_stdout(capsys):
    obj = _make_metric_obj()
    obj.details()
    assert capsys.readouterr().out == ""


# --- SemanticObjectList ---


def _make_list() -> SemanticObjectList:
    return SemanticObjectList(
        items=(_make_metric_obj(),),
        parent_label="sales",
        kind_filter=None,
    )


def test_semantic_object_conforms_to_agent_result():
    from tests.test_agent_result_protocol import assert_conforms

    assert_conforms(_make_metric_obj())


def test_semantic_object_list_conforms_to_agent_result():
    from tests.test_agent_result_protocol import assert_conforms

    assert_conforms(_make_list())


def test_semantic_object_list_objects_property():
    lst = _make_list()
    assert len(lst.objects) == 1
    assert lst.objects[0].name == "revenue"


def test_semantic_object_list_refs_returns_tuple_of_refs():
    lst = _make_list()
    refs = lst.refs()
    assert len(refs) == 1
    assert refs[0].id == "sales.revenue"
    assert isinstance(refs[0], SemanticRef)


def test_semantic_object_list_render_returns_str(capsys):
    rendered = _make_list().render()
    assert isinstance(rendered, str)
    assert capsys.readouterr().out == ""


def test_semantic_object_list_render_no_trailing_newline():
    assert not _make_list().render().endswith("\n")


def test_semantic_object_list_render_contains_ref_and_kind():
    rendered = _make_list().render()
    assert "sales.revenue" in rendered
    assert "metric" in rendered


def test_semantic_object_list_show_prints_render(capsys):
    lst = _make_list()
    result = lst.show()
    assert result is None
    out = capsys.readouterr().out
    assert "sales.revenue" in out


def test_semantic_object_list_empty_renders_actionable_message():
    lst = SemanticObjectList(items=(), parent_label="sales.orders", kind_filter="metric")
    rendered = lst.render()
    assert "sales.orders" in rendered
    assert "metric" in rendered


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

_RICH_DETAILS_DATASETS_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource="warehouse",
        source=ms.table("orders"),
        ai_context=ms.ai_context(
            business_definition="One row per completed order.",
            guardrails=["Exclude test orders."],
            synonyms=["transactions"],
            examples=["completed order count"],
            instructions="Use created_at for reporting windows.",
            owner_notes="Finance analytics owns this entity.",
        ),
    )

    @ms.dimension(
        entity=orders,
        ai_context=ms.ai_context(
            business_definition="Region assigned to the completed order.",
            guardrails=["Do not infer sales ownership from region alone."],
            synonyms=["market"],
            examples=["APAC"],
            instructions="Use for geographic slicing.",
            owner_notes="Maintained by sales ops.",
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
            synonyms=["gross sales"],
            examples=["order amount"],
            instructions="Aggregate with sum for revenue.",
            owner_notes="Finance validates monthly.",
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
            synonyms=["created time"],
            examples=["2026-01-01T00:00:00Z"],
            instructions="Use as the default time window.",
            owner_notes="UTC normalized upstream.",
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
            synonyms=["gross revenue"],
            examples=["Q1 gross revenue"],
            instructions="Use created_at for reporting windows.",
            owner_notes="Owned by finance analytics.",
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


# --- Top-level listing ---


def test_catalog_list_top_level_returns_models_and_datasources(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list()
    kinds = {str(obj.kind) for obj in result.objects}
    assert "domain" in kinds
    assert "datasource" in kinds


def test_catalog_list_top_level_includes_sales_model(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list()
    refs = {obj.ref.id for obj in result.objects}
    assert "sales" in refs


def test_catalog_list_top_level_includes_warehouse_datasource(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list()
    refs = {obj.ref.id for obj in result.objects}
    assert "warehouse" in refs


def test_catalog_list_no_stdout_during_call(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.list()
    assert capsys.readouterr().out == ""


def test_catalog_list_returns_semantic_object_list(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list()
    assert isinstance(result, SemanticObjectList)


def test_catalog_list_refs_returns_semantic_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    refs = catalog.list().refs()
    assert all(isinstance(r, SemanticRef) for r in refs)


# --- Model-level listing ---


def test_catalog_list_domain_returns_entities(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref)
    kinds = {str(obj.kind) for obj in result.objects}
    assert "entity" in kinds


def test_catalog_list_domain_returns_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref)
    kinds = {str(obj.kind) for obj in result.objects}
    assert "metric" in kinds


def test_catalog_list_domain_includes_orders_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref)
    refs = {obj.ref.id for obj in result.objects}
    assert "sales.orders" in refs


def test_catalog_list_domain_includes_revenue_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref)
    refs = {obj.ref.id for obj in result.objects}
    assert "sales.revenue" in refs


def test_catalog_list_domain_relationships(semantic_project_factory):
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

    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.RELATIONSHIP)

    assert result.ids() == ["sales.orders_to_users"]
    assert all(str(obj.kind) == "relationship" for obj in result.objects)


def test_catalog_list_accepts_semantic_ref_scope(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    domain_ref = make_ref("sales", SemanticKind.DOMAIN)
    result = catalog.list(domain_ref)
    refs = {obj.ref.id for obj in result.objects}
    assert "sales.orders" in refs


def test_catalog_list_rejects_string_scope(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list("sales")  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "SemanticRef" in str(exc_info.value)


# --- Dataset-level listing ---


def test_catalog_list_entity_returns_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref)
    kinds = {str(obj.kind) for obj in result.objects}
    assert "dimension" in kinds


def test_catalog_list_entity_returns_time_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref)
    kinds = {str(obj.kind) for obj in result.objects}
    assert "time_dimension" in kinds


def test_catalog_list_entity_returns_filtered_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref)
    kinds = {str(obj.kind) for obj in result.objects}
    assert "metric" in kinds


def test_catalog_list_entity_filtered_metric_has_canonical_domain_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref)
    metric_objs = [obj for obj in result.objects if str(obj.kind) == "metric"]
    assert len(metric_objs) == 1
    assert metric_objs[0].ref.id == "sales.revenue"


def test_catalog_list_entity_dimension_has_correct_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref)
    field_refs = {obj.ref.id for obj in result.objects if str(obj.kind) == "dimension"}
    assert "sales.orders.region" in field_refs


def test_catalog_list_entity_time_dimension_has_correct_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref)
    tf_refs = {obj.ref.id for obj in result.objects if str(obj.kind) == "time_dimension"}
    assert "sales.orders.created_at" in tf_refs


# --- Kind filter ---


def test_catalog_list_kind_filter_metric_returns_only_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.METRIC)
    assert all(str(obj.kind) == "metric" for obj in result.objects)
    assert len(result.objects) >= 1


def test_catalog_list_kind_filter_entity_under_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.ENTITY)
    assert all(str(obj.kind) == "entity" for obj in result.objects)


def test_catalog_list_kind_filter_dimension_under_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.DIMENSION)
    assert result.ids() == ["sales.orders.region"]
    assert all(str(obj.kind) == "dimension" for obj in result.objects)


def test_catalog_list_kind_filter_time_dimension_under_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("domain.sales").ref, kind=SemanticKind.TIME_DIMENSION)
    assert result.ids() == ["sales.orders.created_at"]
    assert all(str(obj.kind) == "time_dimension" for obj in result.objects)


def test_catalog_list_kind_filter_metric_under_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list(catalog.get("entity.sales.orders").ref, kind=SemanticKind.METRIC)
    assert all(str(obj.kind) == "metric" for obj in result.objects)
    assert any(obj.ref.id == "sales.revenue" for obj in result.objects)


# --- Error cases ---


def test_catalog_list_rejects_string_kind(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(kind="dimension")  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.UNSUPPORTED_KIND
    assert "SemanticKind.DIMENSION" in str(exc_info.value)


def test_catalog_list_unsupported_kind_error_lists_valid_values(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(kind="datasets")  # type: ignore[arg-type]
    msg = str(exc_info.value)
    assert "SemanticKind.METRIC" in msg
    assert "SemanticKind.ENTITY" in msg


def test_catalog_list_metric_as_parent_raises_unsupported_parent(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(catalog.get("metric.sales.revenue").ref)
    assert exc_info.value.kind == ErrorKind.UNSUPPORTED_LIST_PARENT


def test_catalog_list_unsupported_parent_error_suggests_get_details(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(catalog.get("metric.sales.revenue").ref)
    assert "catalog.get" in str(exc_info.value)
    assert "details()" in str(exc_info.value)


def test_catalog_list_field_as_parent_raises_unsupported_parent(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(catalog.get("dimension.sales.orders.region").ref)
    assert exc_info.value.kind == ErrorKind.UNSUPPORTED_LIST_PARENT


def test_catalog_list_unknown_ref_raises_not_found(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    unknown_ref = make_ref("nonexistent.thing", SemanticKind.ENTITY)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(unknown_ref)
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


# --- catalog.get() ---


def test_catalog_get_returns_semantic_object_for_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("domain.sales")
    assert obj.ref.id == "sales"
    assert str(obj.kind) == "domain"


def test_catalog_get_returns_semantic_object_for_datasource(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("datasource.warehouse")
    assert obj.ref.id == "warehouse"
    assert str(obj.kind) == "datasource"


def test_catalog_get_returns_semantic_object_for_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("entity.sales.orders")
    assert obj.ref.id == "sales.orders"
    assert str(obj.kind) == "entity"
    assert obj.domain == "sales"


def test_catalog_get_returns_semantic_object_for_dimension(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("dimension.sales.orders.region")
    assert obj.ref.id == "sales.orders.region"
    assert str(obj.kind) == "dimension"


def test_catalog_get_returns_semantic_object_for_time_dimension(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("time_dimension.sales.orders.created_at")
    assert str(obj.kind) == "time_dimension"


def test_catalog_get_returns_semantic_object_for_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("metric.sales.revenue")
    assert obj.ref.id == "sales.revenue"
    assert str(obj.kind) == "metric"


def test_catalog_get_rejects_semantic_ref_input(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get(ref)  # type: ignore[arg-type]
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "<kind>.<semantic_id>" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    ["sales", "warehouse", "sales.orders", "sales.revenue", "sales.orders.region"],
)
def test_catalog_get_rejects_bare_semantic_ids(semantic_project_factory, raw):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get(raw)
    assert exc_info.value.kind == ErrorKind.INVALID_REF
    assert "<kind>.<semantic_id>" in str(exc_info.value)


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
    assert "catalog.list" in msg


def test_catalog_get_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.get("metric.sales.revenue")
    assert capsys.readouterr().out == ""


def test_catalog_get_context_matches_authored_ai_context(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

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
    assert obj.context.business_definition == "All completed order amounts."


def test_catalog_get_business_definition_matches_authored_context(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)
    obj = catalog.get("metric.sales.revenue")
    assert obj.context.business_definition == "Total gross order amount before refunds."


def test_catalog_get_source_location_is_populated(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("metric.sales.revenue")
    loc = obj.source_location
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
        assert details.context.synonyms
        assert details.context.examples
        assert details.context.instructions
        assert details.context.owner_notes
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
    assert "datasource: warehouse" in entity_rendered
    assert "source:" in entity_rendered
    assert "children:" in entity_rendered
    assert "sales.orders.region" in entity_rendered


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
    assert d.datasource.id == "warehouse"


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
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
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


def test_catalog_time_dimension_details_include_sample_interval(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
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
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
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
        "import marivo.semantic as ms\nms.domain(name='wrong_name')\n"
    )
    from marivo.semantic.errors import SemanticLoadFailed

    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


def test_ms_load_does_not_print(tmp_path, capsys):
    _write_minimal_project(tmp_path)
    ms.load(workspace_dir=tmp_path)
    assert capsys.readouterr().out == ""


def test_ms_load_catalog_can_list(tmp_path):
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    result = catalog.list()
    assert len(result.objects) >= 1


def test_ms_load_with_domains_filters_domains(tmp_path):
    """ms.load(domains=...) filters to the specified domain directories."""
    _write_multi_domain_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path, domains=["sales"])
    refs = {obj.ref.id for obj in catalog.list().objects}
    assert "sales" in refs
    assert "ops" not in refs


def test_ms_load_with_domains_string(tmp_path):
    """ms.load(domains='sales') accepts a single domain name as a string."""
    _write_multi_domain_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path, domains="sales")
    refs = {obj.ref.id for obj in catalog.list().objects}
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
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    catalog = SemanticCatalog(project)

    catalog.load()

    refs = {obj.ref.id for obj in catalog.list().objects}
    assert "sales" in refs
    assert "ops" not in refs


def test_catalog_load_with_models_changes_filter(semantic_project_factory):
    """catalog.load(domains=...) changes the active domain filter on reload."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    catalog = SemanticCatalog(project)

    # Switch to ops domain via catalog.load(domains=...)
    catalog.load(domains="ops")

    refs = {obj.ref.id for obj in catalog.list().objects}
    assert "ops" in refs
    assert "sales" not in refs


def test_catalog_access_after_failed_load_raises_semantic_load_failed(tmp_path):
    semantic = tmp_path / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='wrong_name')\n"
    )

    from marivo.semantic.errors import SemanticLoadFailed
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    catalog = SemanticCatalog(project)

    with pytest.raises(SemanticLoadFailed):
        catalog.list()


def _preview_backend():
    backend = ibis.duckdb.connect(":memory:")
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, created_at TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    return backend


class _PreviewConnectionService:
    def __init__(self, backend):
        self._backend = backend

    def session_backend(self, name):
        return self._backend

    def close_all(self):
        pass


def _patch_preview_connections(project, backend):
    from unittest.mock import patch

    return patch.object(
        project,
        "_connection_service",
        return_value=_PreviewConnectionService(backend),
    )


def test_catalog_preview_field_preserves_context_columns(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    backend = _preview_backend()
    with _patch_preview_connections(project, backend):
        preview = catalog.preview(
            catalog.get("dimension.sales.orders.region").ref,
            context_columns=("order_id",),
            limit=2,
        )

    assert preview.ref == "sales.orders.region"
    assert preview.columns[:2] == ("order_id", "region")


def test_catalog_preview_metric_preserves_approximate_warning(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    backend = _preview_backend()
    with _patch_preview_connections(project, backend):
        preview = catalog.preview(catalog.get("metric.sales.revenue").ref, limit=2)

    assert preview.ref == "sales.revenue"
    assert any(w.kind == "approximate_preview" for w in preview.warnings)


def test_catalog_preview_context_columns_rejected_for_metric(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(catalog.get("metric.sales.revenue").ref, context_columns=("order_id",))

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
        "import marivo.semantic as ms\nms.domain(name='sales', default=True)\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
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
        "import marivo.semantic as ms\nms.domain(name='sales', default=True)\n"
    )
    (sales / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
        "def revenue(table):\n"
        "    return table.amount.sum()\n"
    )
    ops = tmp_path / "models" / "semantic" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "_domain.py").write_text("import marivo.semantic as ms\nms.domain(name='ops')\n")
    (ops / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
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
    assert result.kind == "entity"
    msg = result.issues[0].message
    assert "sales.revenue" in msg
    assert "domain level" in msg


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
    "import marivo.semantic as ms\n"
    "import marivo.datasource as md\n"
    "\n"
    "warehouse = md.ref('warehouse')\n"
    "\n"
    "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
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
            "location",
            "ai_context",
            "is_time_dimension",
            "kind",
            "semantic_id",
            "parse",
        },
        MeasureIR: {"location", "ai_context", "semantic_id", "kind"},
        MetricIR: {
            "location",
            "ai_context",
            "body_ast_hash",
            "semantic_id",
            "fold_override",
            "metric_type",
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
