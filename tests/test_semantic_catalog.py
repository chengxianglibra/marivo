"""Tests for marivo.semantic.catalog — SemanticCatalog public API."""

from __future__ import annotations

import textwrap

import ibis
import pytest

import marivo.semantic as ms
from marivo.semantic.catalog import (
    AiContextView,
    DatasourceDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticObject,
    SemanticObjectList,
    SemanticRef,
    SnapshotVersioning,
    TimeDimensionDetails,
    ValidityVersioning,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import ParityStatus, SourceLocation, SymbolKind

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
    ref = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    assert str(ref) == "sales.revenue"


def test_semantic_ref_repr_includes_ref_and_kind():
    ref = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    r = repr(ref)
    assert "sales.revenue" in r
    assert "metric" in r


def test_semantic_ref_equality_by_value():
    a = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    b = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    assert a == b


def test_semantic_ref_is_frozen():
    ref = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    with pytest.raises((AttributeError, TypeError)):
        ref.ref = "other"  # type: ignore[misc]


def test_reader_project_no_longer_exposes_catalog_read_surface() -> None:
    from marivo.semantic.reader import SemanticProject

    removed = {
        "list_domains",
        "list_datasources",
        "list_entities",
        "list_dimensions",
        "list_time_dimensions",
        "list_metrics",
        "list_relationships",
        "get_entity",
        "get_metric",
        "materialize_dataset",
        "materialize_field",
        "materialize_metric",
        "preview_dataset",
        "preview_field",
        "preview_metric",
    }
    for name in removed:
        assert not hasattr(SemanticProject, name), name


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
    return SemanticRef(ref=r, kind=kind)


def _make_ctx() -> AiContextView:
    return AiContextView(
        business_definition=None,
        guardrails=(),
        synonyms=(),
        examples=(),
        instructions=None,
        owner_notes=None,
    )


def _make_loc() -> SourceLocation:
    return SourceLocation(file=".marivo/semantic/sales/_domain.py", line=5)


# --- Kind-specific details ---


def test_datasource_details_fields():
    d = DatasourceDetails(
        ref=_make_ref("warehouse", SemanticKind.DATASOURCE),
        kind=SemanticKind.DATASOURCE,
        name="warehouse",
        domain=None,
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(),
        children=(),
        dependents=(),
        backend_type="duckdb",
    )
    assert d.backend_type == "duckdb"
    assert d.domain is None


def test_domain_details_fields():
    d = DomainDetails(
        ref=_make_ref("sales", SemanticKind.DOMAIN),
        kind=SemanticKind.DOMAIN,
        name="sales",
        domain="sales",
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(),
        children=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        dependents=(),
    )
    assert d.children[0].ref == "sales.orders"


def test_entity_details_fields():
    from marivo.semantic.dtos import TableSource

    d = EntityDetails(
        ref=_make_ref("sales.orders", SemanticKind.ENTITY),
        kind=SemanticKind.ENTITY,
        name="orders",
        domain="sales",
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("warehouse", SemanticKind.DATASOURCE),),
        children=(),
        dependents=(),
        datasource=_make_ref("warehouse", SemanticKind.DATASOURCE),
        source=TableSource(table="orders", database=None),
        primary_key=("order_id",),
        versioning=None,
    )
    assert d.datasource.ref == "warehouse"
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
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        dimension_kind="categorical",
    )
    assert d.dimension_kind == "categorical"
    assert d.entity.ref == "sales.orders"


def test_time_dimension_details_fields():
    d = TimeDimensionDetails(
        ref=_make_ref("sales.orders.created_at", SemanticKind.TIME_DIMENSION),
        kind=SemanticKind.TIME_DIMENSION,
        name="created_at",
        domain="sales",
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        data_type="timestamp",
        granularity="day",
        format=None,
        timezone=None,
        required_prefix=None,
        is_default=True,
        sample_interval=None,
    )
    assert d.granularity == "day"
    assert d.is_default is True
    assert d.sample_interval is None


def test_metric_details_fields():
    d = MetricDetails(
        ref=_make_ref("sales.revenue", SemanticKind.METRIC),
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        entities=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        root_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        is_derived=False,
        component_metrics=(),
        components=(),
        required_relationships=(),
        decomposition="sum",
        additivity="additive",
        fanout_policy="block",
        unit=None,
        verification_mode="python_native",
        parity_status=ParityStatus.UNVERIFIED,
        source_sql=None,
        source_dialect=None,
        python_symbol="revenue",
        time_fold=None,
        fold_time_dimension=None,
    )
    assert d.decomposition == "sum"
    assert d.is_derived is False
    assert d.component_metrics == ()
    assert d.components == ()
    assert d.time_fold is None
    assert d.fold_time_dimension is None


def test_relationship_details_fields():
    d = RelationshipDetails(
        ref=_make_ref("sales.orders_customers", SemanticKind.RELATIONSHIP),
        kind=SemanticKind.RELATIONSHIP,
        name="orders_customers",
        domain="sales",
        description=None,
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(
            _make_ref("sales.orders", SemanticKind.ENTITY),
            _make_ref("sales.customers", SemanticKind.ENTITY),
        ),
        children=(),
        dependents=(),
        from_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        to_entity=_make_ref("sales.customers", SemanticKind.ENTITY),
        from_dimensions=("customer_id",),
        to_dimensions=("id",),
    )
    assert d.from_dimensions == ("customer_id",)
    assert d.to_dimensions == ("id",)


def _make_metric_obj() -> SemanticObject:
    ref = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    details = MetricDetails(
        ref=ref,
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        description="Gross revenue.",
        context=_make_ctx(),
        source_location=_make_loc(),
        parents=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        children=(),
        dependents=(),
        entities=(_make_ref("sales.orders", SemanticKind.ENTITY),),
        root_entity=_make_ref("sales.orders", SemanticKind.ENTITY),
        is_derived=False,
        component_metrics=(),
        components=(),
        required_relationships=(),
        decomposition="sum",
        additivity="additive",
        fanout_policy="block",
        unit=None,
        verification_mode="python_native",
        parity_status=ParityStatus.UNVERIFIED,
        source_sql=None,
        source_dialect=None,
        python_symbol="revenue",
        time_fold=None,
        fold_time_dimension=None,
    )
    return SemanticObject(
        ref=ref,
        kind=SemanticKind.METRIC,
        name="revenue",
        domain="sales",
        description="Gross revenue.",
        context=_make_ctx(),
        source_location=_make_loc(),
        python_symbol="revenue",
        _details=details,
    )


# --- SemanticObject ---


def test_semantic_object_fields():
    obj = _make_metric_obj()
    assert obj.ref.ref == "sales.revenue"
    assert obj.kind == SemanticKind.METRIC
    assert obj.name == "revenue"
    assert obj.domain == "sales"
    assert obj.description == "Gross revenue."


def test_semantic_object_details_returns_typed_details():
    obj = _make_metric_obj()
    d = obj.details()
    assert isinstance(d, MetricDetails)
    assert d.decomposition == "sum"


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
    assert refs[0].ref == "sales.revenue"
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
    ms.domain(name="sales", default=True, description="Sales model.")
""")

_DATASETS_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.dimension(entity=orders, description="Sales region.")
    def region(table):
        return table.region

    @ms.time_dimension(entity=orders, data_type="timestamp", granularity="day")
    def created_at(table):
        return table.created_at

    @ms.metric(
        entities=[orders],
        additivity="additive",
        decomposition=ms.sum(),
        verification_mode="python_native",
        description="Gross revenue.",
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
    refs = {obj.ref.ref for obj in result.objects}
    assert "sales" in refs


def test_catalog_list_top_level_includes_warehouse_datasource(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list()
    refs = {obj.ref.ref for obj in result.objects}
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
    result = catalog.list("sales")
    kinds = {str(obj.kind) for obj in result.objects}
    assert "entity" in kinds


def test_catalog_list_domain_returns_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales")
    kinds = {str(obj.kind) for obj in result.objects}
    assert "metric" in kinds


def test_catalog_list_domain_includes_orders_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales")
    refs = {obj.ref.ref for obj in result.objects}
    assert "sales.orders" in refs


def test_catalog_list_domain_includes_revenue_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales")
    refs = {obj.ref.ref for obj in result.objects}
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
                "    from_dimensions=[user_id],\n"
                "    to_dimensions=[id],\n"
                ")\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    result = catalog.list("sales", kind="relationship")

    assert result.ids() == ["sales.orders_to_users"]
    assert all(str(obj.kind) == "relationship" for obj in result.objects)


def test_catalog_list_accepts_semantic_ref_as_parent(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    domain_ref = SemanticRef(ref="sales", kind=SemanticKind.DOMAIN)
    result = catalog.list(domain_ref)
    refs = {obj.ref.ref for obj in result.objects}
    assert "sales.orders" in refs


# --- Dataset-level listing ---


def test_catalog_list_entity_returns_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders")
    kinds = {str(obj.kind) for obj in result.objects}
    assert "dimension" in kinds


def test_catalog_list_entity_returns_time_dimensions(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders")
    kinds = {str(obj.kind) for obj in result.objects}
    assert "time_dimension" in kinds


def test_catalog_list_entity_returns_filtered_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders")
    kinds = {str(obj.kind) for obj in result.objects}
    assert "metric" in kinds


def test_catalog_list_entity_filtered_metric_has_canonical_domain_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders")
    metric_objs = [obj for obj in result.objects if str(obj.kind) == "metric"]
    assert len(metric_objs) == 1
    assert metric_objs[0].ref.ref == "sales.revenue"


def test_catalog_list_entity_dimension_has_correct_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders")
    field_refs = {obj.ref.ref for obj in result.objects if str(obj.kind) == "dimension"}
    assert "sales.orders.region" in field_refs


def test_catalog_list_entity_time_dimension_has_correct_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders")
    tf_refs = {obj.ref.ref for obj in result.objects if str(obj.kind) == "time_dimension"}
    assert "sales.orders.created_at" in tf_refs


# --- Kind filter ---


def test_catalog_list_kind_filter_metric_returns_only_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales", kind="metric")
    assert all(str(obj.kind) == "metric" for obj in result.objects)
    assert len(result.objects) >= 1


def test_catalog_list_kind_filter_entity_under_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales", kind="entity")
    assert all(str(obj.kind) == "entity" for obj in result.objects)


def test_catalog_list_kind_filter_metric_under_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.list("sales.orders", kind="metric")
    assert all(str(obj.kind) == "metric" for obj in result.objects)
    assert any(obj.ref.ref == "sales.revenue" for obj in result.objects)


# --- Error cases ---


def test_catalog_list_unsupported_kind_raises_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(kind="datasets")  # typo: "datasets" is not valid
    assert exc_info.value.kind == ErrorKind.UNSUPPORTED_KIND


def test_catalog_list_unsupported_kind_error_lists_valid_values(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list(kind="datasets")
    msg = str(exc_info.value)
    assert "metric" in msg
    assert "entity" in msg


def test_catalog_list_metric_as_parent_raises_unsupported_parent(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list("sales.revenue")
    assert exc_info.value.kind == ErrorKind.UNSUPPORTED_LIST_PARENT


def test_catalog_list_unsupported_parent_error_suggests_get_details(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list("sales.revenue")
    assert "catalog.get" in str(exc_info.value)
    assert "details()" in str(exc_info.value)


def test_catalog_list_field_as_parent_raises_unsupported_parent(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list("sales.orders.region")
    assert exc_info.value.kind == ErrorKind.UNSUPPORTED_LIST_PARENT


def test_catalog_list_unknown_ref_raises_not_found(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.list("nonexistent.thing")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


# --- catalog.get() ---


def test_catalog_get_returns_semantic_object_for_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales")
    assert obj.ref.ref == "sales"
    assert str(obj.kind) == "domain"


def test_catalog_get_returns_semantic_object_for_datasource(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("warehouse")
    assert obj.ref.ref == "warehouse"
    assert str(obj.kind) == "datasource"


def test_catalog_get_returns_semantic_object_for_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.orders")
    assert obj.ref.ref == "sales.orders"
    assert str(obj.kind) == "entity"
    assert obj.domain == "sales"


def test_catalog_get_returns_semantic_object_for_dimension(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.orders.region")
    assert obj.ref.ref == "sales.orders.region"
    assert str(obj.kind) == "dimension"


def test_catalog_get_returns_semantic_object_for_time_dimension(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.orders.created_at")
    assert str(obj.kind) == "time_dimension"


def test_catalog_get_returns_semantic_object_for_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.revenue")
    assert obj.ref.ref == "sales.revenue"
    assert str(obj.kind) == "metric"


def test_catalog_get_accepts_semantic_ref_input(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    ref = SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)
    obj = catalog.get(ref)
    assert obj.ref.ref == "sales.revenue"


def test_catalog_get_not_found_raises_typed_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("sales.nonexistent")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


def test_catalog_get_not_found_error_mentions_browse_hint(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.get("revenue")  # short name
    msg = str(exc_info.value)
    assert "catalog.list" in msg


def test_catalog_get_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.get("sales.revenue")
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
                    decomposition=ms.sum(),
                    verification_mode="python_native",
                    description="Gross revenue.",
                    ai_context={"business_definition": "All completed order amounts."},
                )
                def revenue(table):
                    return table.amount.sum()
            """),
        }
    )
    catalog = SemanticCatalog(project)
    obj = catalog.get("sales.revenue")
    assert obj.context.business_definition == "All completed order amounts."


def test_catalog_get_description_matches_authored_description(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.revenue")
    assert obj.description == "Gross revenue."


def test_catalog_get_source_location_is_populated(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.revenue")
    loc = obj.source_location
    assert loc.file != ""
    assert loc.line > 0


def test_catalog_get_dataset_details_correct_datasource_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.orders")
    d = obj.details()
    assert isinstance(d, EntityDetails)
    assert d.datasource.ref == "warehouse"


def test_catalog_entity_details_source_uses_shared_ir_type(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    from marivo.datasource.ir import TableSourceIR

    details = catalog.get("sales.orders").details()
    assert isinstance(details, EntityDetails)
    assert isinstance(details.source, TableSourceIR)
    assert details.source.to_dict()["table"] == "orders"


def test_catalog_get_metric_details_correct_dataset_ref(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.revenue")
    d = obj.details()
    assert isinstance(d, MetricDetails)
    assert any(r.ref == "sales.orders" for r in d.entities)


def test_catalog_metric_details_components_are_role_keyed(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native')\n"
                "def revenue(table):\n"
                "    return table.amount.sum()\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native')\n"
                "def order_count(table):\n"
                "    return table.order_id.nunique()\n"
                "conversion = ms.derived_metric(\n"
                "    name='conversion',\n"
                "    decomposition=ms.ratio(numerator=revenue, denominator=order_count),\n"
                ")\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.get("sales.conversion").details()

    assert isinstance(details, MetricDetails)
    assert details.components == (
        ("numerator", SemanticRef(ref="sales.revenue", kind=SemanticKind.METRIC)),
        ("denominator", SemanticRef(ref="sales.order_count", kind=SemanticKind.METRIC)),
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
                "    data_type='timestamp',\n"
                "    granularity='minute',\n"
                "    sample_interval=(5, 'minute'),\n"
                ")\n"
                "def sampled_at(table):\n"
                "    return table.created_at\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.get("sales.orders.sampled_at").details()

    assert isinstance(details, TimeDimensionDetails)
    assert details.sample_interval is not None
    assert details.sample_interval.to_token() == "5minute"


def test_catalog_get_model_details_children_include_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales")
    d = obj.details()
    assert isinstance(d, DomainDetails)
    child_refs = {r.ref for r in d.children}
    assert "sales.revenue" in child_refs
    assert "sales.orders" in child_refs


def test_catalog_get_dataset_details_children_do_not_include_metrics(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.get("sales.orders")
    d = obj.details()
    assert isinstance(d, EntityDetails)
    child_refs = {r.ref for r in d.children}
    assert "sales.revenue" not in child_refs
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
    semantic = tmp_path / ".marivo" / "semantic" / "sales"
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

            @ms.dimension(entity=orders, description="Sales region.")
            def region(table):
                return table.region

            @ms.time_dimension(entity=orders, data_type="timestamp", granularity="day")
            def created_at(table):
                return table.created_at

            @ms.metric(
                entities=[orders],
                additivity="additive",
                decomposition=ms.sum(),
                verification_mode="python_native",
                description="Gross revenue.",
            )
            def revenue(table):
                return table.amount.sum()

            @ms.metric(
                entities=[orders],
                additivity="additive",
                decomposition=ms.sum(),
                verification_mode="python_native",
                description="Gross profit.",
            )
            def profit(table):
                return table.profit.sum()
        """)
    )
    with pytest.raises(SemanticRuntimeError):
        catalog.get("sales.profit")

    catalog.load()

    assert project.is_ready()
    assert catalog.get("sales.profit").ref.ref == "sales.profit"


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

    refs = {obj.ref.ref for obj in catalog.list().objects}
    assert "sales" in refs
    assert "ops" not in refs


def test_catalog_access_after_failed_load_raises_semantic_load_failed(tmp_path):
    semantic = tmp_path / ".marivo" / "semantic" / "sales"
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
            "sales.orders.region",
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
        preview = catalog.preview("sales.revenue", limit=2)

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
        catalog.preview("sales.revenue", context_columns=("order_id",))

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "context_columns" in str(exc_info.value)


def _write_minimal_project(tmp_path) -> None:
    semantic = tmp_path / ".marivo" / "semantic" / "sales"
    ds = tmp_path / ".marivo" / "datasource"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', default=True)\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native')\n"
        "def revenue(table):\n"
        "    return table.amount.sum()\n"
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
    revenue_ref = catalog.get("sales.revenue").ref
    report = catalog.readiness(refs=[revenue_ref])
    assert isinstance(report, ReadinessReport)


def test_catalog_readiness_accepts_string_refs(semantic_project_factory):
    from marivo.semantic.readiness import ReadinessReport

    catalog = _make_catalog(semantic_project_factory)
    report = catalog.readiness(refs=["sales.revenue"])
    assert isinstance(report, ReadinessReport)


def test_catalog_readiness_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.readiness()
    assert capsys.readouterr().out == ""


# --- metric unit passthrough ---


_UNIT_DATASETS_PY = (
    "import marivo.semantic as ms\n"
    "import marivo.datasource as md\n"
    "\n"
    "warehouse = md.ref('warehouse')\n"
    "\n"
    "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
    "\n"
    "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', "
    "verification_mode='python_native', unit='CNY')\n"
    "def revenue(orders):\n"
    "    return orders.amount.sum()\n"
)

_WAREHOUSE_PY = (
    "import marivo.datasource as md\n"
    "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    "md.datasource(warehouse)\n"
)


def test_catalog_metric_details_unit_passthrough(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _UNIT_DATASETS_PY,
            "datasource/warehouse.py": _WAREHOUSE_PY,
        }
    )
    catalog = SemanticCatalog(project)
    d = catalog.get("sales.revenue").details()
    assert isinstance(d, MetricDetails)
    assert d.unit == "CNY"


def test_catalog_metric_details_unit_defaults_to_none(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    d = catalog.get("sales.revenue").details()
    assert d.unit is None


def test_catalog_details_cover_required_ir_fields() -> None:
    from dataclasses import fields

    from marivo.semantic.catalog import (
        DimensionDetails,
        EntityDetails,
        MetricDetails,
        TimeDimensionDetails,
    )
    from marivo.semantic.ir import DimensionIR, EntityIR, MetricIR

    coverage = {
        EntityIR: {field.name for field in fields(EntityDetails)}
        | {"ref", "kind", "parents", "children", "dependents", "source_location", "context"},
        MetricIR: {field.name for field in fields(MetricDetails)}
        | {"ref", "kind", "parents", "children", "dependents", "source_location", "context"},
        DimensionIR: {field.name for field in fields(DimensionDetails)}
        | {field.name for field in fields(TimeDimensionDetails)}
        | {"ref", "kind", "parents", "children", "dependents", "source_location", "context"},
    }
    allowed_internal = {
        EntityIR: {"location", "ai_context", "python_symbol", "semantic_id"},
        MetricIR: {"location", "ai_context", "body_ast_hash", "provenance", "semantic_id"},
        DimensionIR: {
            "location",
            "ai_context",
            "is_time_dimension",
            "kind",
            "python_symbol",
            "semantic_id",
        },
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
