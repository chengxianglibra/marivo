"""Complete captured definitions, scope disclosure and stable result pages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES
from marivo.semantic.errors import SemanticError
from tests.shared_fixtures import write_minimal_catalog_project


def test_complete_context_and_json_survive_bounded_display(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_minimal_catalog_project(tmp_path)
    source = tmp_path / "models/semantic/sales/objects.py"
    text = source.read_text().replace(
        "@ms.metric()",
        """@ms.metric(ai_context=ms.ai_context(
    business_definition="Revenue details. " * 1000,
    guardrails=["Guardrail " + str(i) for i in range(49)],
    aliases=["销售额", "gross revenue"]))""",
    )
    # Authoring context arguments are ordinary Python data before declaration capture.
    source.write_text(text)
    catalog = ms.load(workspace_dir=tmp_path)
    entry = catalog.get("metric:sales.revenue")
    assert entry.kind == "metric"
    assert entry.definition.context.business_definition == "Revenue details. " * 1000
    assert len(entry.definition.context.guardrails) == 49
    assert len(entry.render().encode()) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert "to_json()" in entry.render()
    payload = json.loads(entry.to_json())
    assert payload["definition"]["context"]["guardrails"][-1] == "Guardrail 48"
    assert payload["definition"]["context"]["aliases"] == ["销售额", "gross revenue"]
    assert "DeclarationResolver" not in entry.to_json()
    entry.show(section="context", max_output_bytes=None)
    assert "Guardrail 48" in capsys.readouterr().out


def test_metric_definition_discloses_formula_effective_time_and_dependency_guardrails(
    tmp_path: Path,
) -> None:
    write_minimal_catalog_project(tmp_path)
    path = tmp_path / "models/semantic/sales/objects.py"
    text = path.read_text().replace(
        'unit="USD")',
        'unit="USD", ai_context=ms.ai_context(guardrails=["Gross amount includes refunds"]))',
    )
    text = text.replace(
        "amount_value = amount",
        """settled_at = ms.time_dimension_column(name="settled_at", entity=orders, column="settled_at",
    parse=ms.timestamp(timezone="UTC"))
settled_value = settled_at
amount_value = amount""",
    ).replace("return amount_value.sum()", "return amount_value.sum(time=settled_value)")
    path.write_text(text)
    catalog = ms.load(workspace_dir=tmp_path)
    metric = catalog.get("metric:sales.revenue")
    assert metric.kind == "metric"
    definition = metric.definition
    assert "sum(measure:sales.orders.amount" in definition.formula
    assert definition.time_bindings[0].field == "time_dimension:sales.orders.settled_at"
    assert definition.time_bindings[0].source == "explicit"
    assert definition.time_bindings[0].entity_default == "time_dimension:sales.orders.paid_at"
    assert definition.unit == "USD"
    assert any(
        context.id == "measure:sales.orders.amount"
        and context.guardrails == ("Gross amount includes refunds",)
        for context in definition.dependency_context
    )
    assert "Gross amount includes refunds" in metric.render(max_output_bytes=None)


def test_search_filters_are_separate_from_loaded_scope_and_exact_get(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    ops = tmp_path / "models/semantic/ops"
    ops.mkdir()
    (ops / "_domain.py").write_text(
        'import marivo.semantic as ms\nms.domain(name="ops", owner="Operations")\n'
    )
    catalog = ms.load(workspace_dir=tmp_path)
    page = catalog.search(domain="sales", kind="metric")
    assert page.filters.domain == "sales"
    assert page.retrieval.mode == "browse"
    assert not page.matches
    assert page.scope.domains == ("ops", "sales")
    assert catalog.get("domain:ops").domain == "ops"
    assert page.to_dict()["scope"] != page.to_dict()["filters"]
    assert catalog.search("unrelated spaceship propulsion").items == ()


def test_browse_pagination_is_complete_stable_and_snapshot_bound(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    objects = tmp_path / "models/semantic/sales/objects.py"
    objects.write_text(
        objects.read_text()
        + "".join(
            f"\n@ms.metric()\ndef revenue_{number:02d}():\n    return amount_value.sum() * {number + 1}\n"
            for number in range(12)
        )
    )
    catalog = ms.load(workspace_dir=tmp_path)
    first = catalog.search(kind="metric", limit=3)
    assert first.candidate_count == 13
    assert first.next_cursor is not None
    second = catalog.search(kind="metric", limit=3, cursor=first.next_cursor)
    assert second.candidate_count == 13
    assert not {item.id for item in first.items} & {item.id for item in second.items}
    reloaded = ms.load(workspace_dir=tmp_path)
    assert [
        item.id for item in reloaded.search(kind="metric", limit=3, cursor=first.next_cursor).items
    ] == [item.id for item in second.items]
    payload = json.loads(first.to_json())
    assert len(payload["items"]) == 3
    assert payload["matches"] == {}
    assert len(first.render(max_output_bytes=1600).encode()) <= 1600
    with pytest.raises(SemanticError):
        catalog.search(kind="entity", cursor=first.next_cursor)


def test_related_and_readiness_expand_cross_domain_dependencies(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    ops = tmp_path / "models/semantic/ops"
    ops.mkdir()
    (ops / "_domain.py").write_text(
        'import marivo.semantic as ms\nms.domain(name="ops", owner="Operations")\n'
    )
    (ops / "objects.py").write_text("""import marivo.datasource as md
import marivo.semantic as ms
usage = ms.entity(name="usage", datasource=ms.ref.datasource("warehouse"), source=md.table("usage"))
amount = ms.measure_column(name="amount", entity=usage, column="amount", additivity=ms.additive_all())
""")
    (tmp_path / "models/semantic/sales/cross.py").write_text("""import marivo.semantic as ms
usage_amount = ms.ref.measure("ops.usage.amount")
@ms.metric()
def cross_domain():
    return usage_amount.sum()
""")
    catalog = ms.load(workspace_dir=tmp_path)
    selected = catalog.search(kind="metric", domain="sales")
    metric = next(item for item in selected.items if item.name == "cross_domain")
    related = metric.related("dependencies")
    assert "measure:ops.usage.amount" in {item.id for item in related.items}
    assert related.filters.domain is None
    assert related.filters.origin == metric.id
    report = catalog.readiness(refs=[metric])
    assert report.requested_ids == ("metric:sales.cross_domain",)
    assert "measure:ops.usage.amount" in report.checked_ids
    assert "entity:ops.usage" in report.checked_ids
    assert not report.blockers
    assert report.analysis_ready_inputs == ()


def test_related_fields_and_reverse_users_stay_on_loaded_graph(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    entity = catalog.get("entity:sales.orders")
    assert {item.id for item in entity.related("fields").items} == {
        "dimension:sales.orders.status",
        "time_dimension:sales.orders.paid_at",
        "measure:sales.orders.amount",
    }
    amount = catalog.get("measure:sales.orders.amount")
    assert "metric:sales.revenue" in {item.id for item in amount.related("dependents").items}
    assert "metric:sales.revenue" in {item.id for item in entity.related("metrics").items}
    assert "relationship:sales.orders_self" in {
        item.id for item in entity.related("relationships").items
    }


def test_filtered_relationship_keeps_missing_endpoints_explicit(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    ops = tmp_path / "models/semantic/ops"
    ops.mkdir()
    (ops / "_domain.py").write_text(
        'import marivo.semantic as ms\nms.domain(name="ops", owner="Operations")\n'
    )
    (ops / "objects.py").write_text("""import marivo.datasource as md
import marivo.semantic as ms
users = ms.entity(name="users", datasource=ms.ref.datasource("warehouse"), source=md.table("users"))
id = ms.dimension_column(name="id", entity=users, column="id")
""")
    path = tmp_path / "models/semantic/sales/objects.py"
    path.write_text(
        path.read_text()
        + """
ms.relationship(name="external_users", from_entity=orders, to_entity=ms.ref.entity("ops.users"),
    keys=[ms.join_on(status, ms.ref.dimension("ops.users.id"))])
"""
    )
    catalog = ms.load(workspace_dir=tmp_path, domains="sales")
    entry = catalog.get("relationship:sales.external_users")
    assert entry.kind == "relationship"
    assert entry.definition.cardinality == "outside_scope"
    related = entry.related("dependencies")
    assert related.outside_scope == ("entity:ops.users",)
    assert related.retrieval.mode == "related"
    assert not related.matches
    with pytest.raises(SemanticError) as lookup:
        catalog.get(related.outside_scope[0])
    assert lookup.value.details["reason"] == "outside_scope"
    assert lookup.value.repair is not None and lookup.value.repair.kind == "reload"
    report = catalog.readiness(refs=[entry])
    assert report.status == "blocked"
    assert "entity:ops.users" in report.checked_ids
    with pytest.raises(SemanticError) as outside:
        _ = entry.value
    assert outside.value.details["reason"] == "outside_scope"


def test_entry_display_sections_and_json_schema_are_explicit(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    entry = catalog.get("metric:sales.revenue")
    assert ".value" in entry.render()
    assert entry.to_dict()["schema_version"] == 1
    assert catalog.search().to_dict()["schema_version"] == 1
    with pytest.raises(SemanticError):
        entry.show(section="bad")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("requested", "expected_ready"),
    (
        (("metric:sales.revenue",), ()),
        (("measure:sales.orders.doubled",), ()),
        (("measure:sales.orders.amount",), ("measure:sales.orders.amount",)),
        (("entity:sales.orders",), ("entity:sales.orders",)),
        (
            ("metric:sales.revenue", "measure:sales.orders.amount"),
            ("measure:sales.orders.amount",),
        ),
        (
            ("measure:sales.orders.doubled", "entity:sales.orders"),
            ("entity:sales.orders",),
        ),
    ),
)
def test_readiness_promotes_only_directly_requested_physical_inputs(
    tmp_path: Path, requested: tuple[str, ...], expected_ready: tuple[str, ...]
) -> None:
    from tests.support.refs import as_ref

    write_minimal_catalog_project(tmp_path)
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text() + "\n@ms.measure()\ndef doubled():\n    return amount_value * 2\n"
    )
    catalog = ms.load(workspace_dir=tmp_path)
    report = catalog.readiness(refs=[catalog.get(identity) for identity in requested])

    assert report.status == "ready"
    assert report.requested_ids == requested
    assert tuple(as_ref(value).key for value in report.analysis_ready_inputs) == expected_ready
    # Dependencies remain visible even when a derived request promotes no physical ref.
    assert "entity:sales.orders" in report.checked_ids
    assert "datasource:warehouse" in report.checked_ids
    if any(
        identity.startswith("metric:") or identity.endswith(".doubled") for identity in requested
    ):
        assert "measure:sales.orders.amount" in report.checked_ids


def test_sample_reduction_definitions_preserve_quantile_and_null_parameters(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    source = tmp_path / "models/semantic/sales/objects.py"
    source.write_text(
        source.read_text()
        + """
revenue_value = revenue
paid_value = paid_at
@ms.metric()
def median_revenue():
    return revenue_value.per(paid_value).quantile(0.5)
@ms.metric()
def tail_revenue():
    return revenue_value.per(paid_value).quantile(0.95)
"""
    )
    catalog = ms.load(workspace_dir=tmp_path)
    median = catalog.get("metric:sales.median_revenue")
    tail = catalog.get("metric:sales.tail_revenue")
    assert median.kind == tail.kind == "metric"

    assert "q=0.5" in median.definition.formula
    assert "q=0.95" in tail.definition.formula
    assert median.definition != tail.definition
    assert median.to_dict()["definition"] != tail.to_dict()["definition"]
    assert "include_null=False" in tail.definition.formula
    from dataclasses import replace

    from marivo._definition.expressions import PerReduce, PerWeightedMean
    from marivo.semantic._catalog_definition import formula
    from marivo.semantic._deferred_semantics import consume_field, consume_metric

    node = consume_metric(catalog._reg.declared_metrics["sales.tail_revenue"]).node
    assert isinstance(node, PerReduce)
    axis = catalog.get("time_dimension:sales.orders.paid_at")
    assert axis.kind == "time_dimension"
    ordering = consume_field(axis.value).reference
    with_nulls = formula(replace(node, include_null=True, order_by=ordering))
    assert (
        "include_null=True" in with_nulls
        and "order_by=time_dimension:sales.orders.paid_at" in with_nulls
    )
    assert with_nulls != formula(node)
    weighted = PerWeightedMean(node.operand, node.operand, node.samples, nulls="error")
    assert "nulls=error" in formula(weighted)
    assert formula(weighted) != formula(replace(weighted, nulls="ignore"))
    assert "rule_version=" in tail.definition.formula
    assert "Resolver" not in tail.to_json()
    assert "recipe=" not in tail.to_json()


def test_entity_definition_retains_captured_filter_body(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    source = tmp_path / "models/semantic/sales/filtered.py"
    source.write_text("""import marivo.datasource as md
import marivo.semantic as ms
@ms.entity(datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["order_id"])
def paid_orders(raw):
    return raw.filter(raw.status == "paid")
@ms.entity(datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["order_id"])
def unpaid_orders(raw):
    return raw.filter(raw.status == "unpaid")
""")
    catalog = ms.load(workspace_dir=tmp_path)
    paid = catalog.get("entity:sales.paid_orders")
    unpaid = catalog.get("entity:sales.unpaid_orders")
    assert paid.kind == unpaid.kind == "entity"
    assert paid.definition.source == unpaid.definition.source
    assert "filter" in paid.definition.expression and "paid" in paid.definition.expression
    assert "unpaid" in unpaid.definition.expression
    assert paid.definition != unpaid.definition
    assert paid.to_dict()["definition"] != unpaid.to_dict()["definition"]
    # The public projection stays captured after the authored source changes.
    source.write_text("raise RuntimeError('must not replay entity code')\n")
    assert "paid" in paid.definition.expression
    assert "filter" in paid.render(max_output_bytes=None)


def test_reducer_null_disclosure_distinguishes_empty_and_asserted_nulls(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    model = tmp_path / "models/semantic/sales/objects.py"
    model.write_text(
        model.read_text()
        + """
@ms.metric()
def strict_revenue():
    return amount_value.sum(nulls="error")
@ms.metric()
def query_count():
    return orders.count()
"""
    )
    catalog = ms.load(workspace_dir=tmp_path)
    ordinary = catalog.get("metric:sales.revenue")
    strict = catalog.get("metric:sales.strict_revenue")
    count = catalog.get("metric:sales.query_count")
    assert ordinary.kind == strict.kind == count.kind == "metric"
    assert any("empty input returns typed zero" in rule for rule in ordinary.definition.null_rules)
    assert any(
        "nonempty all-NULL input returns NULL" in rule for rule in ordinary.definition.null_rules
    )
    assert any("demanded NULL raises an error" in rule for rule in strict.definition.null_rules)
    assert all("all-NULL input returns NULL" not in rule for rule in strict.definition.null_rules)
    assert any(
        "count counts source rows; empty input returns zero" in rule
        for rule in count.definition.null_rules
    )
