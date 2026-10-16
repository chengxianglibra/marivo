"""Tests for marivo.semantic.catalog — SemanticCatalog public API."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable
from typing import cast, get_args, get_origin, get_type_hints

import ibis
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef
from marivo.refs import (
    DatasourceKind,
    DomainKind,
    PeriodCalendarKind,
    Ref,
    SemanticKindTag,
    TemporalSetKind,
    WorkScheduleKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES
from marivo.semantic.catalog import (
    AiContextView,
    CatalogEntry,
    DatasourceDetails,
    DatasourceEntry,
    DomainDetails,
    DomainEntry,
    PeriodCalendarEntry,
    SemanticCatalog,
    SemanticKind,
    SnapshotVersioning,
    TemporalSetEntry,
    ValidityVersioning,
    WorkScheduleEntry,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.expressions import Dimension, Entity, Measure, Metric, TimeField
from marivo.semantic.ir import SourceLocation

# --- SemanticKind ---


def test_semantic_kind_is_symbol_kind_alias():
    assert SemanticKind is ms.SemanticKind


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


# --- Unified Ref identity ---


def test_semantic_ref_str_returns_ref_string():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    assert str(ref) == "metric:sales.revenue"


def test_semantic_ref_repr_includes_ref_and_kind():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    assert repr(ref) == "Ref[metric](metric:sales.revenue)"


def test_semantic_ref_equality_by_value():
    a = make_ref("sales.revenue", SemanticKind.METRIC)
    b = make_ref("sales.revenue", SemanticKind.METRIC)
    assert a == b


def test_semantic_ref_is_frozen():
    ref = make_ref("sales.revenue", SemanticKind.METRIC)
    with pytest.raises((AttributeError, TypeError)):
        ref.path = "other"  # type: ignore[misc]


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


_FACTORY_BY_KIND: dict[SemanticKind, Callable[[str], Ref[SemanticKindTag]]] = {
    kind: cast("Callable[[str], Ref[SemanticKindTag]]", factory)
    for kind, factory in {
        SemanticKind.DOMAIN: ref_factory.domain,
        SemanticKind.DATASOURCE: ref_factory.datasource,
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
    }.items()
}


def make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    return _FACTORY_BY_KIND[kind](path)


def _make_ref(r: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    return make_ref(r, kind)


def _ref_from_typed_id_path(value: str) -> Ref[SemanticKindTag]:
    kind_value, separator, path = value.partition(".")
    assert separator
    return make_ref(path, SemanticKind(kind_value))


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


def test_datasource_details_fields() -> None:
    fields: dict[str, object] = {"path": ":memory:"}
    env_refs = {"password": "WAREHOUSE_PASSWORD"}
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
        fields=fields,
        env_refs=env_refs,
    )
    assert d.backend_type == "duckdb"
    assert d.domain is None
    assert d.fields == {"path": ":memory:"}
    assert d.env_refs == {"password": "WAREHOUSE_PASSWORD"}
    fields["path"] = "changed.duckdb"
    env_refs["password"] = "CHANGED_PASSWORD"
    assert d.fields == {"path": ":memory:"}
    assert d.env_refs == {"password": "WAREHOUSE_PASSWORD"}
    # Deliberately cross the read-only typing boundary to verify runtime immutability.
    with pytest.raises(TypeError):
        d.fields["path"] = "changed.duckdb"  # type: ignore[index]
    with pytest.raises(TypeError):
        d.env_refs["password"] = "CHANGED_PASSWORD"  # type: ignore[index]
    rendered = d.render()
    labels = (
        "business_definition",
        "guardrails",
        "source_location",
        "python_symbol",
        "parents",
        "children",
        "dependents",
        "backend_type",
        "fields",
        "env_refs",
    )
    lines = rendered.splitlines()
    positions = [
        next(i for i, line in enumerate(lines) if line.startswith(f"{label}:")) for label in labels
    ]
    assert positions == sorted(positions)
    assert all(sum(line.startswith(f"{label}:") for line in lines) == 1 for label in labels)
    assert "parents: (none)" in rendered
    assert "children: (none)" in rendered
    assert "dependents: (none)" in rendered
    assert d.render() == rendered


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
    assert d.children[0].path == "sales.orders"
    assert d.owner == "Mina Zhang"
    assert d.default is True


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


# --- Fixtures ---


_MINIMAL_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_DATASETS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
    )
    region = ms.dimension_column(name="region", entity=orders, column="region")
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
    )
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount", additivity=ms.additive_all()
    )

    finalized = ms.finalize()
    amount_value = finalized.resolve(amount)
    created_at_value = finalized.resolve(created_at)

    @ms.metric()
    def revenue():
        return amount_value.sum(time=created_at_value)
""")

_RICH_DETAILS_DATASETS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
        ai_context=ms.ai_context(
            business_definition="One row per completed order.",
            guardrails=["Exclude test orders."],
        ),
    )
    region = ms.dimension_column(
        name="region",
        entity=orders,
        column="region",
        ai_context=ms.ai_context(
            business_definition="Region assigned to the completed order.",
            guardrails=["Do not infer sales ownership from region alone."],
        ),
    )
    amount = ms.measure_column(
        name="amount",
        entity=orders,
        column="amount",
        additivity=ms.additive_all(),
        unit="USD",
        ai_context=ms.ai_context(
            business_definition="Gross order amount before refunds.",
            guardrails=["Does not net out refunds."],
        ),
    )
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
        ai_context=ms.ai_context(
            business_definition="Order creation timestamp.",
            guardrails=["Do not use as payment settlement time."],
        ),
    )

    finalized = ms.finalize()
    amount_value = finalized.resolve(amount)
    created_at_value = finalized.resolve(created_at)

    @ms.metric(
        ai_context=ms.ai_context(
            business_definition="Total gross order amount before refunds.",
            guardrails=["Do not use as net revenue."],
        ),
    )
    def revenue():
        return amount_value.sum(time=created_at_value)
""")


def _make_catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def _make_rich_catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


# --- catalog.require() returns entries or exact values ---


def test_catalog_require_returns_entries_for_containers_and_values_for_members(
    semantic_project_factory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    entry_cases: tuple[tuple[str, type[CatalogEntry[SemanticKindTag]], str], ...] = (
        ("domain.sales", DomainEntry, "sales"),
        ("datasource.warehouse", DatasourceEntry, "warehouse"),
    )
    for typed_id, expected_type, expected_name in entry_cases:
        ref = _ref_from_typed_id_path(typed_id)
        obj = catalog.require(ref)

        assert type(obj) is expected_type
        assert obj.ref == ref
        assert obj.ref.path == typed_id.partition(".")[2]
        assert obj.ref.name == expected_name
        assert obj.key == typed_id.replace(".", ":", 1)
        assert obj.name == expected_name
        assert obj.ref.kind.value == typed_id.partition(".")[0]

    value_cases: tuple[tuple[str, type[object]], ...] = (
        ("entity.sales.orders", Entity),
        ("dimension.sales.orders.region", Dimension),
        ("time_dimension.sales.orders.created_at", TimeField),
        ("measure.sales.orders.amount", Measure),
    )
    for typed_id, expected_value_type in value_cases:
        ref = _ref_from_typed_id_path(typed_id)
        value = catalog.require(ref)

        assert type(value) is expected_value_type
        exact = cast("Entity | Dimension | TimeField | Measure", value).ref
        assert exact.ref == ref
        assert exact.version.startswith("sha256:")

    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    assert type(revenue) is Metric
    assert isinstance(revenue.node, DeclaredMetricRef)
    assert revenue.node.ref.ref == ms.ref.metric("sales.revenue")
    assert revenue.node.ref.version.startswith("sha256:")


def test_catalog_entry_and_value_equality_follow_identity(
    semantic_project_factory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    first = catalog.require(ms.ref.domain("sales"))
    second = catalog.require(ms.ref.domain("sales"))

    assert first == second
    assert hash(first) == hash(second)
    assert first != catalog.require(ms.ref.datasource("warehouse"))
    rendered = first.render(max_output_bytes=None)
    assert "kind: domain" in rendered
    assert "path: sales" in rendered
    assert "ref: domain:sales" in rendered
    assert "business_definition:" in rendered
    assert "- .ref" in rendered

    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    assert revenue == catalog.require(ms.ref.metric("sales.revenue"))
    assert hash(revenue) == hash(catalog.require(ms.ref.metric("sales.revenue")))
    assert revenue != first


# --- Agent result protocol ---


def test_catalog_result_protocol_and_collection_accessors(semantic_project_factory, capsys):
    from tests.support.result_protocol import assert_conforms

    catalog = _make_catalog(semantic_project_factory)
    entry = catalog.require(ms.ref.domain("sales"))
    collection = catalog.metrics

    assert_conforms(entry)
    assert_conforms(collection)
    assert_conforms(collection.items[0].contract())
    assert len(collection.items) == 1
    assert collection.items[0].contract().name == "revenue"
    assert collection.refs == (ms.ref.metric("sales.revenue"),)
    assert type(collection.refs[0]) is Ref
    capsys.readouterr()
    rendered = collection.render()

    assert isinstance(rendered, str)
    assert not rendered.endswith("\n")
    assert "sales.revenue" in rendered
    assert "metric" in rendered
    assert "available:" in rendered
    assert "- .refs" in rendered
    assert "selection: catalog.metrics.get(<displayed ref>) -> Metric" in rendered
    assert rendered.count("selection:") == 1
    assert capsys.readouterr().out == ""

    result = collection.show()
    assert result is None
    assert capsys.readouterr().out == f"{rendered}\n"


def test_filtered_metric_definition_is_distinguishable_from_unfiltered(
    semantic_project_factory,
) -> None:
    """A filtered count must be distinguishable from an unfiltered one: the
    filter is part of the published computation, so its definition identity differs.
    """
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(
                    name="orders", datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                )
                region = ms.dimension_column(name="region", entity=orders, column="region")

                finalized = ms.finalize()
                orders_value = finalized.resolve(orders)
                region_value = finalized.resolve(region)

                @ms.metric()
                def all_count():
                    return orders_value.count()

                @ms.metric()
                def failed_count():
                    return orders_value.count().where(region_value == "FAILED")
            """),
        }
    )
    catalog = SemanticCatalog(project)
    all_contract = catalog.metrics.get("sales.all_count").contract()
    failed_contract = catalog.metrics.get("sales.failed_count").contract()

    assert all_contract.stage == failed_contract.stage == "published_metric"
    assert all_contract.computation is not None
    assert failed_contract.computation is not None
    assert all_contract.computation != failed_contract.computation
    assert all_contract.ref is not None and failed_contract.ref is not None
    assert all_contract.ref.version != failed_contract.ref.version
    assert all_contract.unit == failed_contract.unit == "1"


# --- Top-level typed collections ---


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


def test_catalog_entries_publish_exact_ref_annotations():
    expectations = (
        (DomainEntry, DomainKind),
        (DatasourceEntry, DatasourceKind),
        (PeriodCalendarEntry, PeriodCalendarKind),
        (TemporalSetEntry, TemporalSetKind),
        (WorkScheduleEntry, WorkScheduleKind),
    )
    for entry_type, marker in expectations:
        annotation = get_type_hints(entry_type)["ref"]
        assert get_origin(annotation) is Ref
        assert get_args(annotation) == (marker,)


def test_catalog_domains_access_and_render_are_silent_and_bounded(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    domains = catalog.domains
    assert capsys.readouterr().out == ""

    rendered = domains.render()

    assert "- .refs" in rendered
    assert "available:" in rendered
    assert capsys.readouterr().out == ""


def test_catalog_collection_render_uses_card_listing(semantic_project_factory):
    catalog = _make_rich_catalog(semantic_project_factory)
    cases = (
        (
            "fields",
            (
                "dimension:sales.orders.region",
                "time_dimension:sales.orders.created_at",
                "measure:sales.orders.amount",
            ),
            "Dimension | TimeField | Measure",
        ),
        ("entities", ("entity:sales.orders",), "Entity"),
        ("metrics", ("metric:sales.revenue",), "Metric"),
    )
    for collection_name, expected_ids, item_type in cases:
        collection = getattr(catalog, collection_name)
        rendered = collection.render()

        for expected_id in expected_ids:
            assert expected_id in rendered
        assert "- .refs" in rendered
        assert (
            f"selection: catalog.{collection_name}.get(<displayed ref>) -> {item_type}" in rendered
        )
        assert rendered.count("selection:") == 1
        assert repr(collection) == (
            f"<CatalogCollection {collection_name} scope=catalog "
            f"count={len(expected_ids)}; call .show() to inspect>"
        )


def test_catalog_collection_lookup_accepts_every_key_shape_and_rejects_misuse(
    semantic_project_factory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    fields = catalog.fields
    by_local = fields.get("amount")
    by_path = fields.get("sales.orders.amount")
    by_typed_key = fields.get("measure:sales.orders.amount")
    by_ref = fields.get(ms.ref.measure("sales.orders.amount"))

    assert isinstance(by_local, Measure)
    for other in (by_path, by_typed_key, by_ref):
        assert isinstance(other, Measure)
        assert other.ref == by_local.ref

    with pytest.raises(SemanticRuntimeError) as missing:
        fields.get("missing")
    assert missing.value.kind == ErrorKind.NOT_FOUND

    with pytest.raises(SemanticRuntimeError) as wrong_kind:
        fields.get(ms.ref.metric("sales.revenue"))  # type: ignore[arg-type]
    assert wrong_kind.value.kind == ErrorKind.INVALID_REF
    assert "catalog.metrics" in str(wrong_kind.value)


def test_catalog_unknown_attribute_raises_plain_attribute_error(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(AttributeError):
        _ = catalog.totally_unknown_attribute  # type: ignore[attr-defined]


# --- catalog.require() ---


def test_catalog_require_returns_semantic_object_for_domain(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.require(ms.ref.domain("sales"))
    assert obj.ref.path == "sales"
    assert str(obj.ref.kind) == "domain"
    assert obj.details().owner == "Mina Zhang"


def test_catalog_require_returns_semantic_value_for_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    obj = catalog.require(ms.ref.entity("sales.orders"))
    assert obj.ref.ref.path == "sales.orders"
    assert str(obj.ref.ref.kind) == "entity"
    assert obj.ref.ref in catalog.domains.get("sales").entities.refs


def test_catalog_require_rejects_untyped_string_inputs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    cases = (
        *((raw, "exact Ref") for raw in ("sales.orders", "sales.revenue", "sales.orders.region")),
        *((raw, "ms.ref.<kind>(path)") for raw in ("sales", "warehouse")),
    )
    for raw, hint in cases:
        with pytest.raises(SemanticRuntimeError) as exc_info:
            catalog.require(raw)  # type: ignore[arg-type]
        assert exc_info.value.kind == ErrorKind.INVALID_REF
        assert hint in str(exc_info.value)


def test_catalog_require_kind_mismatch_raises_not_found(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.require(ms.ref.metric("sales.orders"))
    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert "metric" in str(exc_info.value)


def test_catalog_require_not_found_lists_same_kind_candidates(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.require(ms.ref.metric("sales.missing"))
    assert exc_info.value.kind == ErrorKind.NOT_FOUND
    assert exc_info.value.details["candidates"] == ("metric:sales.revenue",)
    assert "ms.ref.metric('sales.revenue')" in str(exc_info.value)


def test_catalog_get_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.require(ms.ref.metric("sales.revenue"))
    catalog.fields.get("sales.orders.region").contract()
    assert capsys.readouterr().out == ""


def test_catalog_contracts_expose_declared_ai_context(semantic_project_factory):
    catalog = _make_rich_catalog(semantic_project_factory)

    cases = {
        "entity.sales.orders": "orders",
        "dimension.sales.orders.region": "region",
        "measure.sales.orders.amount": "amount",
        "time_dimension.sales.orders.created_at": "created_at",
        "metric.sales.revenue": "revenue",
    }
    for typed_id, declared_name in cases.items():
        value = catalog.require(_ref_from_typed_id_path(typed_id))
        contract = value.contract()
        assert contract.business_definition
        assert contract.guardrails
        assert contract.name == declared_name
        assert contract.location is not None
        file, _, line = contract.location.rpartition(":")
        assert file.endswith("sales/datasets.py")
        assert int(line) > 0

    metric_contract = catalog.require(ms.ref.metric("sales.revenue")).contract()
    assert metric_contract.business_definition == "Total gross order amount before refunds."
    assert metric_contract.guardrails == ("Do not use as net revenue.",)
    metric_rendered = metric_contract.render()
    assert "business_definition: Total gross order amount before refunds." in metric_rendered
    assert "guardrails: Do not use as net revenue." in metric_rendered
    assert "declared_at:" in metric_rendered
    assert "name: revenue" in metric_rendered
    assert "roots: entity:sales.orders" in metric_rendered
    assert "unit: USD" in metric_rendered

    entity_rendered = catalog.require(ms.ref.entity("sales.orders")).contract().render()
    assert "reads: datasource:warehouse" in entity_rendered
    assert "source:" in entity_rendered


def test_catalog_contract_render_bounds_long_business_definition(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(
                    name="orders", datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                )
                amount = ms.measure_column(
                    name="amount", entity=orders, column="amount", additivity=ms.additive_all()
                )

                finalized = ms.finalize()
                amount_value = finalized.resolve(amount)

                @ms.metric(
                    ai_context=ms.ai_context(
                        business_definition="Revenue detail. " * 1000, guardrails=[]
                    ),
                )
                def revenue():
                    return amount_value.sum()
            """),
        }
    )
    contract = SemanticCatalog(project).metrics.get("sales.revenue").contract()

    rendered = contract.render()

    assert contract.business_definition == "Revenue detail. " * 1000
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
                "    name='warehouse', host='h', catalog='c', user_env='TRINO_USER', "
                "auth_env='TRINO_AUTH')\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    details = catalog.require(ms.ref.datasource("warehouse")).details()
    assert isinstance(details, DatasourceDetails)
    assert details.fields == {"host": "h", "catalog": "c"}
    assert details.env_refs == {"user": "TRINO_USER", "auth": "TRINO_AUTH"}
    rendered = details.render()
    assert "TRINO_AUTH" in rendered
    assert "TRINO_USER" in rendered
    assert "plaintext-secret" not in rendered
    assert "auth: TRINO_AUTH" in rendered


def test_catalog_entity_value_reads_its_datasource_and_typed_source(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)

    orders = catalog.require(ms.ref.entity("sales.orders"))
    contract = orders.contract()

    assert contract.stage == "entity"
    assert contract.reads == (ms.ref.datasource("warehouse"),)
    assert type(contract.reads[0]) is Ref
    facts = dict(contract.facts)
    assert "'kind': 'table'" in facts["source"]
    assert "'table': 'orders'" in facts["source"]
    assert [value.ref.ref.path for value in catalog.entities.items] == ["sales.orders"]
    assert catalog.datasources.get("warehouse").entities.refs == (ms.ref.entity("sales.orders"),)


def test_catalog_metric_value_roots_on_its_entity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    orders = catalog.require(ms.ref.entity("sales.orders"))
    assert revenue.roots == (orders.ref,)
    assert any(root.ref.path == "sales.orders" for root in revenue.contract().roots)


def test_catalog_timestamp_and_strptime_fields_disclose_sample_interval(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(
                    name="orders", datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                )
                timestamp_sampled_at = ms.time_dimension_column(
                    name="timestamp_sampled_at",
                    entity=orders,
                    column="created_at",
                    granularity="minute",
                    parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),
                )
                strptime_sampled_at = ms.time_dimension_column(
                    name="strptime_sampled_at",
                    entity=orders,
                    column="created_at_key",
                    granularity="minute",
                    parse=ms.strptime(
                        "%Y%m%d%H%M%S", timezone="UTC", sample_interval=(5, "minute")
                    ),
                )
            """),
        }
    )
    catalog = SemanticCatalog(project)

    timestamp_field = catalog.require(ms.ref.time_dimension("sales.orders.timestamp_sampled_at"))
    strptime_field = catalog.require(ms.ref.time_dimension("sales.orders.strptime_sampled_at"))

    assert isinstance(timestamp_field, TimeField)
    timestamp_facts = dict(timestamp_field.contract().facts)
    assert timestamp_facts["granularity"] == "minute"
    assert "kind='timestamp'" in timestamp_facts["parse"]
    assert "sample_interval=SampleIntervalIR(count=5, unit='minute')" in timestamp_facts["parse"]
    assert isinstance(strptime_field, TimeField)
    strptime_facts = dict(strptime_field.contract().facts)
    assert "kind='strptime'" in strptime_facts["parse"]
    assert "sample_interval=SampleIntervalIR(count=5, unit='minute')" in strptime_facts["parse"]


def test_catalog_domain_details_include_children(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    domain_details = catalog.require(ms.ref.domain("sales")).details()
    assert isinstance(domain_details, DomainDetails)
    domain_child_refs = {ref.path for ref in domain_details.children}
    assert "sales.revenue" in domain_child_refs
    assert "sales.orders" in domain_child_refs


# --- ms.load() ---


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
    assert "sales.revenue" in {ref.path for ref in catalog.metrics.refs}


def test_ms_load_with_domains_filters_domains_and_accepts_string(tmp_path):
    """ms.load(domains=...) filters to the specified domain directories."""
    _write_multi_domain_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path, domains=["sales"])
    refs = {obj.ref.path for obj in catalog.domains.items}
    assert "sales" in refs
    assert "ops" not in refs

    string_catalog = ms.load(workspace_dir=tmp_path, domains="sales")
    string_refs = {obj.ref.path for obj in string_catalog.domains.items}
    assert "sales" in string_refs
    assert "ops" not in string_refs


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


def test_ms_load_returns_fresh_catalog_without_mutating_existing_catalog(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)
    (project.semantic_root / "sales" / "profit.py").write_text(
        textwrap.dedent("""\
            import marivo.semantic as ms
            from .datasets import amount_value, created_at_value

            @ms.metric()
            def profit():
                return amount_value.sum(time=created_at_value) * 2
        """)
    )
    with pytest.raises(SemanticRuntimeError):
        catalog.require(ms.ref.metric("sales.profit"))

    reloaded = ms.load(workspace_dir=project.workspace_dir)

    with pytest.raises(SemanticRuntimeError):
        catalog.require(ms.ref.metric("sales.profit"))
    profit = reloaded.require(ms.ref.metric("sales.profit"))
    assert isinstance(profit.node, DeclaredMetricRef)
    assert profit.node.ref.ref.path == "sales.profit"
    assert reloaded is not catalog


def test_ms_load_can_recreate_filtered_domain_scope(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    SemanticCatalog(project)

    reloaded = ms.load(workspace_dir=project.workspace_dir, domains="sales")

    refs = {obj.ref.path for obj in reloaded.domains.items}
    assert "sales" in refs
    assert "ops" not in refs


def test_ms_load_with_domains_returns_different_filtered_catalog(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    catalog = SemanticCatalog(project)

    reloaded = ms.load(workspace_dir=project.workspace_dir, domains="ops")

    refs = {obj.ref.path for obj in reloaded.domains.items}
    assert "ops" in refs
    assert "sales" not in refs
    assert {obj.ref.path for obj in catalog.domains.items} == {"sales"}


# --- catalog.preview() ---


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
    snapshot = md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "created_at"),
    )
    return catalog, snapshot


def test_catalog_preview_field_published_metric_and_context_boundaries(
    semantic_project_factory, tmp_path, monkeypatch
):
    catalog, snapshot = _preview_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    field_preview = catalog.preview(
        catalog.require(ms.ref.dimension("sales.orders.region")).ref.ref,
        scope=snapshot.scope,
        context_columns=("order_id",),
        limit=2,
    )

    assert field_preview.ref == "sales.orders.region"
    assert field_preview.columns[:2] == ("order_id", "region")

    with pytest.raises(SemanticRuntimeError) as published:
        catalog.preview(
            catalog.require(ms.ref.metric("sales.revenue")),
            scope=snapshot.scope,
            limit=2,
        )

    assert published.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    assert published.value.semantic_refs == ("metric:sales.revenue",)
    assert "published expression declaration" in str(published.value)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(
            ms.ref.entity("sales.orders"),
            scope=snapshot.scope,
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
    (semantic / "datasets.py").write_text(_SALES_REVENUE_PY)


_SALES_REVENUE_PY = (
    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
    "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
    "\n"
    "finalized = ms.finalize()\n"
    "amount_value = finalized.resolve(amount)\n"
    "\n"
    "@ms.metric()\n"
    "def revenue():\n"
    "    return amount_value.sum()\n"
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
    (sales / "datasets.py").write_text(_SALES_REVENUE_PY)
    ops = tmp_path / "models" / "semantic" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n"
    )
    (ops / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
    )


# --- catalog.readiness() ---


def test_catalog_readiness_returns_readiness_report(semantic_project_factory):
    from marivo.semantic.readiness import ReadinessReport

    catalog = _make_catalog(semantic_project_factory)
    report = catalog.readiness()
    assert isinstance(report, ReadinessReport)


def test_catalog_readiness_normalizes_entry_value_and_ref_to_same_public_payload(
    semantic_project_factory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    sales = catalog.domains.get("sales")
    region = catalog.fields.get("sales.orders.region")

    by_entry = catalog.readiness(refs=[sales])
    by_ref = catalog.readiness(refs=[sales.ref])
    by_value = catalog.readiness(refs=[region])
    by_value_ref = catalog.readiness(refs=[region.ref.ref])

    assert by_entry.analysis_ready_inputs == by_ref.analysis_ready_inputs == (sales.ref,)
    assert by_entry.input_summary == by_ref.input_summary
    assert by_value.analysis_ready_inputs == by_value_ref.analysis_ready_inputs
    assert by_value.input_summary == by_value_ref.input_summary
    assert all(type(value) is Ref for value in by_entry.analysis_ready_inputs)

    with pytest.raises(SemanticRuntimeError, match="unique exact refs"):
        catalog.readiness(refs=[sales, sales.ref])


def test_catalog_readiness_rejects_published_metric_inputs(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)

    for value in (ms.ref.metric("sales.revenue"), catalog.metrics.get("sales.revenue")):
        with pytest.raises(SemanticRuntimeError) as exc_info:
            catalog.readiness(refs=[value])
        assert exc_info.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
        assert exc_info.value.semantic_refs == ("metric:sales.revenue",)


def test_catalog_readiness_accepts_runtime_expression_and_mixed_roots(
    semantic_project_factory,
) -> None:
    catalog = _make_rich_catalog(semantic_project_factory)
    region_value = catalog.fields.get("sales.orders.region")
    region = region_value.ref.ref
    amount = ms.ref.measure("sales.orders.amount")
    runtime_total = mv.runtime_metric.aggregate(
        amount,
        agg="sum",
        label="Runtime total",
    )
    runtime_count = mv.runtime_metric.aggregate(
        amount,
        agg="count",
        label="Runtime count",
    )
    runtime_ratio = mv.runtime_metric.ratio(
        runtime_total,
        runtime_count,
        label="Runtime ratio",
    )
    expression = mv.runtime_metric.slice(
        runtime_ratio,
        by={region: "north"},
        label="Runtime north ratio",
    )

    report = catalog.readiness(refs=[region_value, expression])

    assert report.status == "ready"
    assert report.analysis_ready_inputs == (region, expression)
    assert report.input_summary.refs == (
        "sales.orders.region",
        "sales.orders.amount",
        "sales.orders",
        "warehouse",
    )
    assert report.to_dict()["analysis_ready_inputs"][1]["schema"] == (
        "marivo.runtime_metric_expr/v1"
    )


def test_catalog_readiness_deduplicates_shared_runtime_leaves(
    semantic_project_factory,
) -> None:
    catalog = _make_rich_catalog(semantic_project_factory)
    amount = catalog.fields.get("sales.orders.amount").ref.ref
    total = mv.runtime_metric.aggregate(amount, agg="sum", label="Runtime total")
    count = mv.runtime_metric.aggregate(amount, agg="count", label="Runtime count")

    report = catalog.readiness(refs=[total, count])

    assert report.analysis_ready_inputs == (total, count)
    assert report.input_summary.refs.count("sales.orders.amount") == 1
    with pytest.raises(SemanticRuntimeError, match="unique direct inputs"):
        catalog.readiness(refs=[total, total])


def test_catalog_readiness_keeps_unrelated_ref_ready_when_runtime_leaf_is_missing(
    semantic_project_factory,
) -> None:
    catalog = _make_rich_catalog(semantic_project_factory)
    region = catalog.fields.get("sales.orders.region").ref.ref
    missing = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.orders.missing_amount"),
        agg="sum",
        label="Missing runtime total",
    )

    report = catalog.readiness(refs=[region, missing])

    assert report.status == "blocked"
    assert report.analysis_ready_inputs == (region,)
    assert {issue.kind for issue in report.blockers} >= {"unknown_ref", "metric_graph_invalid"}
    graph_issue = next(issue for issue in report.blockers if issue.kind == "metric_graph_invalid")
    assert graph_issue.repair is not None
    assert graph_issue.repair.help_target.surface == "analysis"
    assert graph_issue.repair.help_target.canonical_id == "runtime_metric"


def test_catalog_readiness_blocks_invalid_runtime_weighted_mean(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(
                    name="orders",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                    ai_context=ms.ai_context(
                        business_definition="One row per completed order.",
                        guardrails=["Exclude tests."],
                    ),
                )
                value = ms.measure_column(
                    name="value", entity=orders, column="value",
                    additivity=ms.non_additive(),
                    ai_context=ms.ai_context(
                        business_definition="Observed row value.", guardrails=["Same grain only."]
                    ),
                )
                invalid_weight = ms.measure_column(
                    name="invalid_weight", entity=orders, column="weight",
                    additivity=ms.non_additive(),
                    ai_context=ms.ai_context(
                        business_definition="Non-additive row weight.",
                        guardrails=["Do not aggregate."],
                    ),
                )
            """),
        }
    )
    catalog = SemanticCatalog(project)
    value = catalog.fields.get("sales.orders.value").ref.ref
    invalid_weight = catalog.fields.get("sales.orders.invalid_weight").ref.ref
    expression = mv.runtime_metric.weighted_mean(
        value,
        invalid_weight,
        label="Invalid runtime weighted mean",
    )

    report = catalog.readiness(refs=[expression])

    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    issue = next(item for item in report.blockers if item.kind == "metric_graph_invalid")
    assert issue.details["lowering_error_kind"] == ("runtime-weighted-mean-weight-non-additive")


def test_catalog_readiness_blocks_cross_entity_and_cross_datasource_runtime_graphs(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(
                    name="orders", datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders")
                )
                shipments = ms.entity(
                    name="shipments", datasource=ms.ref.datasource("warehouse"),
                    source=md.table("shipments")
                )
                returns = ms.entity(
                    name="returns", datasource=ms.ref.datasource("lake"),
                    source=md.table("returns")
                )
                order_value = ms.measure_column(
                    name="order_value", entity=orders, column="amount",
                    additivity=ms.non_additive()
                )
                shipment_weight = ms.measure_column(
                    name="shipment_weight", entity=shipments, column="weight",
                    additivity=ms.additive_all()
                )
                order_amount = ms.measure_column(
                    name="order_amount", entity=orders, column="amount",
                    additivity=ms.additive_all()
                )
                return_amount = ms.measure_column(
                    name="return_amount", entity=returns, column="amount",
                    additivity=ms.additive_all()
                )
            """),
        }
    )
    catalog = SemanticCatalog(project)
    cross_entity = mv.runtime_metric.weighted_mean(
        catalog.fields.get("sales.orders.order_value").ref.ref,
        catalog.fields.get("sales.shipments.shipment_weight").ref.ref,
        label="Cross entity weighted mean",
    )
    cross_entity_report = catalog.readiness(refs=[cross_entity])
    cross_entity_issue = next(
        item for item in cross_entity_report.blockers if item.kind == "metric_graph_invalid"
    )
    assert cross_entity_issue.details["lowering_error_kind"] == (
        "runtime-weighted-mean-grain-mismatch"
    )

    cross_datasource = mv.runtime_metric.ratio(
        mv.runtime_metric.aggregate(
            catalog.fields.get("sales.orders.order_amount").ref.ref,
            agg="sum",
            label="Orders total",
        ),
        mv.runtime_metric.aggregate(
            catalog.fields.get("sales.returns.return_amount").ref.ref,
            agg="sum",
            label="Returns total",
        ),
        label="Cross datasource ratio",
    )
    cross_datasource_report = catalog.readiness(refs=[cross_datasource])
    cross_datasource_issue = next(
        item for item in cross_datasource_report.blockers if item.kind == "metric_graph_invalid"
    )
    assert cross_datasource_issue.details["lowering_error_kind"] == (
        "metric-graph-source-domain-mismatch"
    )


def test_catalog_readiness_blocks_runtime_depth_and_forest_occurrence_limits(
    semantic_project_factory,
) -> None:
    catalog = _make_rich_catalog(semantic_project_factory)
    amount = catalog.fields.get("sales.orders.amount").ref.ref
    total = mv.runtime_metric.aggregate(amount, agg="sum", label="Runtime total")
    deep = mv.runtime_metric.slice(
        total,
        by={catalog.fields.get("sales.orders.region").ref.ref: "north"},
        label="Depth 1",
    )
    for index in range(10):
        deep = mv.runtime_metric.ratio(
            deep,
            total,
            label=f"Depth {index + 2}",
        )

    report = catalog.readiness(refs=[deep])

    assert report.status == "blocked"
    issue = next(item for item in report.blockers if item.kind == "metric_graph_invalid")
    assert issue.details["lowering_error_kind"] == "depth_limit_exceeded"

    wide = mv.runtime_metric.aggregate(
        amount,
        agg="sum",
        label="Occurrence leaf",
    )
    for index in range(8):
        wide = mv.runtime_metric.ratio(
            wide,
            wide,
            label=f"Occurrence level {index + 1}",
        )
    occurrence_report = catalog.readiness(refs=[wide])
    occurrence_issue = next(
        item for item in occurrence_report.blockers if item.kind == "metric_graph_invalid"
    )
    assert occurrence_issue.details["lowering_error_kind"] == "occurrence_limit_exceeded"


def test_catalog_readiness_returns_json_safe_blocker_for_nonfinite_runtime_slice(
    semantic_project_factory,
) -> None:
    catalog = _make_rich_catalog(semantic_project_factory)
    amount = catalog.fields.get("sales.orders.amount").ref.ref
    region = catalog.fields.get("sales.orders.region").ref.ref
    expression = mv.runtime_metric.aggregate(
        amount,
        agg="sum",
        slice_by={region: float("nan")},
        label="Invalid slice",
    )

    report = catalog.readiness(refs=[expression])

    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    issue = next(item for item in report.blockers if item.kind == "metric_graph_invalid")
    assert issue.refs == ("runtime:root[0]",)
    assert issue.details["lowering_error_kind"] == "graph_contract"
    json.dumps(report.to_dict(), allow_nan=False)


def test_catalog_readiness_preserves_exact_kind_when_paths_collide(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "datasources/sales.py": (
                "import marivo.datasource as md\nmd.duckdb(name='sales', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    domain_report = catalog.readiness(refs=[ms.ref.domain("sales")])
    datasource_report = catalog.readiness(refs=[ms.ref.datasource("sales")])
    all_report = catalog.readiness()

    assert domain_report.analysis_ready_inputs == (ms.ref.domain("sales"),)
    assert datasource_report.analysis_ready_inputs == (ms.ref.datasource("sales"),)
    assert set(all_report.analysis_ready_inputs) == {
        ms.ref.datasource("default"),
        ms.ref.domain("sales"),
        ms.ref.datasource("sales"),
    }


def test_catalog_readiness_rejects_string_refs(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.readiness(refs=["sales.revenue"])  # type: ignore[list-item]
    error = exc_info.value
    assert error.kind == ErrorKind.INVALID_REF
    assert error.expected == (
        "current catalog value or entry, exact Ref[kind], or closed RuntimeMetricExpr"
    )
    assert error.received == "str"
    assert error.repair is not None
    assert error.repair.help_target.surface == "semantic"
    assert error.repair.help_target.canonical_id == "readiness"
    assert error.repair.candidates == ("metric:sales.revenue",)
    assert error.repair.snippet == (
        "member = catalog.metrics.get('sales.revenue')\nreport = catalog.readiness(refs=[member])"
    )
    rendered = str(error)
    assert "bare strings are not accepted" in rendered
    assert "expected: current catalog value or entry" in rendered
    assert "received: str" in rendered

    with pytest.raises(SemanticRuntimeError) as unknown_exc_info:
        catalog.readiness(refs=["sales.missing"])  # type: ignore[list-item]
    unknown_repair = unknown_exc_info.value.repair
    assert unknown_repair is not None
    assert unknown_repair.candidates == ()
    assert unknown_repair.snippet is None


def test_catalog_readiness_non_string_error_does_not_claim_string_input(
    semantic_project_factory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.readiness(refs=[42])  # type: ignore[list-item]

    error = exc_info.value
    assert error.received == "int"
    assert "received int" in str(error)
    assert "bare strings are not accepted" not in str(error)
    assert error.repair is not None
    assert error.repair.candidates == ()
    assert error.repair.snippet is None


def test_catalog_readiness_rejects_empty_or_duplicate_explicit_scope(
    semantic_project_factory,
):
    catalog = _make_catalog(semantic_project_factory)
    orders = ms.ref.entity("sales.orders")
    with pytest.raises(SemanticRuntimeError, match="non-empty"):
        catalog.readiness(refs=[])
    with pytest.raises(SemanticRuntimeError, match="unique exact refs") as exc_info:
        catalog.readiness(refs=[orders, orders])
    assert exc_info.value.semantic_refs == (orders.key,)


def test_catalog_readiness_no_stdout(semantic_project_factory, capsys):
    catalog = _make_catalog(semantic_project_factory)
    catalog.readiness()
    assert capsys.readouterr().out == ""


def test_metric_ref_rejects_entity_level_path(
    semantic_project_factory,
):
    del semantic_project_factory
    with pytest.raises(ValueError, match="exactly 2 segments"):
        ms.ref.metric("sales.orders.revenue")


def test_dimension_ref_rejects_domain_level_path(
    semantic_project_factory,
):
    del semantic_project_factory
    with pytest.raises(ValueError, match="exactly 3 segments"):
        ms.ref.dimension("sales.region")


# --- metric unit passthrough ---


_UNIT_DATASETS_PY = (
    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    "\n"
    "warehouse = ms.ref.datasource('warehouse')\n"
    "\n"
    "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
    "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
    "additivity=ms.additive_all(), unit='CNY')\n"
    "margin = ms.measure_column(name='margin', entity=orders, column='margin', "
    "additivity=ms.additive_all())\n"
    "\n"
    "finalized = ms.finalize()\n"
    "amount_value = finalized.resolve(amount)\n"
    "margin_value = finalized.resolve(margin)\n"
    "\n"
    "@ms.metric()\n"
    "def revenue():\n"
    "    return amount_value.sum()\n"
    "\n"
    "@ms.metric()\n"
    "def gross_margin():\n"
    "    return margin_value.sum()\n"
)

_WAREHOUSE_PY = "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"


def test_catalog_metric_values_preserve_declared_and_absent_units(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _UNIT_DATASETS_PY,
            "datasources/warehouse.py": _WAREHOUSE_PY,
        }
    )
    catalog = SemanticCatalog(project)
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    margin = catalog.require(ms.ref.metric("sales.gross_margin"))
    assert isinstance(revenue, Metric)
    assert revenue.unit == "CNY"
    assert revenue.contract().unit == "CNY"
    assert isinstance(margin, Metric)
    assert margin.unit is None
    assert margin.contract().unit is None


def test_catalog_details_cover_all_public_ir_fields() -> None:
    from dataclasses import fields

    from marivo.datasource.ir import DatasourceIR
    from marivo.semantic.ir import DomainIR

    coverage = {
        DatasourceIR: {field.name for field in fields(DatasourceDetails)}
        | {"ref", "source_location", "context"},
        DomainIR: {field.name for field in fields(DomainDetails)}
        | {"ref", "source_location", "context"},
    }
    allowed_internal = {
        DatasourceIR: {"location", "ai_context", "semantic_id"},
        DomainIR: {"location", "ai_context"},
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
