"""Symbol capture, complete-scope binding, and immutable template boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.deferred import inspect_capture, inspect_deferred, root_declaration
from marivo._definition.expressions import SourceAggregate
from marivo._symbolic import native_symbol_ref
from marivo.refs import SemanticKind
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.loader import LoaderContext, loader_context
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import captured_catalog, catalog_value, in_memory_datasource_ir

ProjectFactory = Callable[..., SemanticProject]
_DOMAIN = 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n'
_PHYSICAL = """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])
amount = ms.measure_column(name="amount", entity=orders, column="amount", unit="CNY", additivity=ms.additive_all())
cost = ms.measure_column(name="cost", entity=orders, column="cost", unit="CNY", additivity=ms.additive_all())
"""


def _project(factory: ProjectFactory, source: str) -> SemanticProject:
    return factory({"sales/_domain.py": _DOMAIN, "sales/objects.py": source})


def _catalog(factory: ProjectFactory, source: str) -> SemanticCatalog:
    project = _project(factory, source)
    assert project.errors() == (), tuple(str(error) for error in project.errors())
    return SemanticCatalog(project)


def test_native_factories_preserve_private_identity_and_expression_protocol() -> None:
    paths = {
        "domain": "sales",
        "datasource": "warehouse",
        "entity": "sales.orders",
        "dimension": "sales.orders.status",
        "time_dimension": "sales.orders.paid_at",
        "measure": "sales.orders.amount",
        "metric": "sales.revenue",
        "relationship": "sales.buyer",
        "period_calendar": "sales.fiscal",
        "temporal_set": "sales.campaigns",
    }
    for kind, path in paths.items():
        value = getattr(ms.ref, kind)(path)
        assert type(value) is Deferred
        reference = native_symbol_ref(value)
        assert reference is not None and reference.kind is SemanticKind(kind)
        assert reference.path == path
        assert {reference: "captured"}[reference] == "captured"
        assert isinstance(value == getattr(ms.ref, kind)(path), Deferred)
    assert native_symbol_ref(ms.ref.measure("sales.orders.amount") + 1) is None
    with pytest.raises(AttributeError):
        ms.ref.metric = lambda path: path
    with pytest.raises(ValueError):
        ms.ref.domain("sales.orders")


_BAD_RECIPES: tuple[Deferred, ...] = (
    ms.ref.measure("sales.orders.amount").summ(),
    ms.ref.entity("sales.orders").amount,
    ms.ref.measure("sales.orders.amount").ref.path,
    ms.ref.measure("sales.orders.amount").sum(3),
    ms.ref.measure("sales.orders.amount").sum(unknown="ignore"),
    ms.ref.measure("sales.orders.amount").sum(where={"mutable": True}),
    ms.ref.domain("sales") + 1,
    ms.ref.entity("sales.orders") + 1,
    ms.ref.measure("sales.orders.amount").sum(q=0.5),
    ms.ref.measure("sales.orders.amount").quantile(),
    ms.ref.measure("sales.orders.amount").quantile(True),
    ms.ref.measure("sales.orders.amount").clip(1, 2, 3),
    ms.ref.measure("sales.orders.amount").round(digits=[]),
    ms.ref.measure("sales.orders.amount").sum(nulls="skip"),
    ms.ref.time_dimension("sales.orders.paid_at").via((ms.ref.entity("sales.orders"),)),
    ms.ref.measure("sales.orders.amount").sum(time=ms.ref.measure("sales.orders.cost")),
    ms.ref.measure("sales.orders.amount").sum().per(ms.ref.metric("sales.revenue")).mean(),
    ms.ref.measure("sales.orders.amount").round(1, digits=2),
    ms.ref.measure("sales.orders.amount").clip(1, lower=2),
    ms.ref.measure("sales.orders.amount").log(2, base=3),
    ms.ref.measure("sales.orders.amount").sum().per(ms.ref.measure("sales.orders.cost")).mean(),
    ms.ref.measure("sales.orders.amount").sum().per(()).mean(),
    ms.ref.measure("sales.orders.amount").weighted_mean(weight=ms.ref.entity("sales.orders")),
    ms.ref.time_dimension("sales.orders.paid_at").at("2026-01-01"),
    ms.ref.time_dimension("sales.orders.paid_at").within("2026-01-01"),
    ms.ref.measure("sales.orders.amount").sum().window("all_history"),
)


def _assert_invalid_recipe_fails_at_declaration_capture(bad: Deferred) -> None:
    with pytest.raises(SemanticExpressionError) as failure:

        @ms.metric(domain=ms.ref.domain("sales"))
        def invalid_recipe() -> Deferred:
            return bad

    error = failure.value
    assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert "invalid_recipe" in error.message
    assert error.expected and error.received and error.repair is not None
    assert error.location is not None


def test_invalid_complete_recipes_fail_at_declaration_capture() -> None:
    for bad in _BAD_RECIPES:
        _assert_invalid_recipe_fails_at_declaration_capture(bad)


def test_forward_fields_named_dependencies_and_late_default_time_bind_once(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    marker = tmp_path / "imports.txt"
    source = (
        f"""from pathlib import Path
marker = Path({str(marker)!r})
marker.write_text(marker.read_text() + "x" if marker.exists() else "x")
"""
        + _PHYSICAL
        + """
future_margin = ms.ref.measure("sales.orders.margin")
future_orders = ms.ref.entity("sales.orders")
@ms.metric()
def margin_per_order():
    return future_margin.sum() / future_orders.count()
@ms.measure(additivity=ms.additive_all())
def margin():
    return amount - cost
alias = margin_per_order
paid_at = ms.time_dimension_column(name="paid_at", entity=orders, column="paid_at", parse=ms.timestamp(timezone="UTC"), is_default=True)
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    assert marker.read_text() == "x"
    margin = catalog_value(catalog, "sales.orders.margin", kind="field")
    assert consume_row(margin).unit == "CNY"
    assert consume_row(margin).root.ref.path == "sales.orders"
    assert consume_row(margin).default_time is not None
    assert consume_row(margin).default_time.ref.path == "sales.orders.paid_at"
    assert set(catalog._state.registry.declared_metrics) == {"sales.margin_per_order"}
    assert not inspect_deferred(catalog.get("metric:sales.margin_per_order").value).symbols


def test_complete_lookup_graph_rejects_missing_wrong_kind_and_self_cycles(
    semantic_project_factory: ProjectFactory,
) -> None:
    cases = (
        ('ms.ref.metric("sales.missing") + 1', "consumer", ErrorKind.NOT_FOUND),
        ('ms.ref.metric("sales.loop") + 1', "loop", ErrorKind.DEFINITION_CYCLE),
        ('ms.ref.dimension("sales.orders.amount").count()', "consumer", ErrorKind.INVALID_REF),
    )
    for expression, name, kind in cases:
        source = (
            _PHYSICAL
            + f"""
recipe = {expression}
@ms.metric()
def {name}():
    return recipe
"""
        )
        project = _project(semantic_project_factory, source)
        assert project._status == "errored"
        assert project._compiled_state is None
        assert project.errors()[0].kind == kind.value
        assert project.errors()[0].expected and project.errors()[0].received


def _physical_scope(unit: str, declarations: tuple[Deferred, ...]) -> SemanticCatalog:
    context = LoaderContext(datasource_irs=(in_memory_datasource_ir("warehouse"),))
    with loader_context(context):
        ms.domain(name="sales", owner="Analytics")
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        ms.measure_column(
            name="amount", entity=orders, column="amount", unit=unit, additivity=ms.additive_all()
        )
    context.declarations.extend(declarations)
    return captured_catalog((context,))


def test_same_template_binds_independently_without_calling_or_rereading_author() -> None:
    dependency = ms.ref.measure("sales.orders.amount")

    def authored():
        return dependency.sum()

    template = ms.metric(domain=ms.ref.domain("sales"))(authored)
    captured = inspect_capture(template).fingerprint

    def forbidden():
        raise AssertionError(f"author callback executed: {dependency}")

    authored.__code__ = forbidden.__code__
    dependency = ms.ref.measure("sales.orders.missing")
    first = _physical_scope("CNY", (template,))
    first_facts = consume_metric(first.get("metric:sales.authored").value)
    second = _physical_scope("USD", (template,))
    assert first_facts.unit == "CNY"
    assert consume_metric(second.get("metric:sales.authored").value).unit == "USD"
    assert consume_metric(first.get("metric:sales.authored").value) == first_facts
    assert inspect_capture(template).fingerprint == captured
    assert inspect_capture(template).symbols == (ref_factory.measure("sales.orders.amount"),)
    assert root_declaration(template).reference is None


def test_old_closed_leaf_keeps_captured_facts_when_published_in_new_scope() -> None:
    first = _physical_scope("CNY", ())
    old_amount = catalog_value(first, "sales.orders.amount", kind="field")

    @ms.metric(domain=ms.ref.domain("sales"))
    def old_revenue():
        return old_amount.sum()

    second = _physical_scope("USD", (old_revenue,))
    reopened = consume_metric(second.get("metric:sales.old_revenue").value)
    assert reopened.unit == "CNY"
    assert isinstance(reopened._declared[-1][1], SourceAggregate)
    assert consume_row(catalog_value(second, "sales.orders.amount", kind="field")).unit == "USD"
    assert consume_row(old_amount).unit == "CNY"


def test_measure_identity_uses_effective_relationship_root_and_entity_qualified_names(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL
        + """
customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"), source=md.table("customers"), primary_key=["id"])
customer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
id = ms.dimension_column(name="id", entity=customers, column="id")
credit = ms.measure_column(name="credit", entity=customers, column="credit", unit="CNY", additivity=ms.additive_all())
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(customer_id, id)])
@ms.measure()
def adjusted():
    return credit.via((buyer,))
@ms.measure()
def margin():
    return amount - cost
order_margin = margin
@ms.measure()
def margin():
    return credit + 1
"""
    )
    # Keep both same-name declarations reachable under distinct Python names.
    catalog = _catalog(semantic_project_factory, source)
    assert (
        consume_row(catalog_value(catalog, "sales.orders.adjusted", kind="field")).root.ref.path
        == "sales.orders"
    )
    assert (
        consume_row(catalog_value(catalog, "sales.orders.margin", kind="field")).root.ref.path
        == "sales.orders"
    )
    assert (
        consume_row(catalog_value(catalog, "sales.customers.margin", kind="field")).root.ref.path
        == "sales.customers"
    )


def test_cross_declaration_cycle_has_a_complete_stable_chain(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL
        + """
left = ms.ref.measure("sales.orders.left")
right = ms.ref.measure("sales.orders.right")
@ms.measure()
def left():
    return right + 1
@ms.measure()
def right():
    return left + 1
"""
    )
    project = _project(semantic_project_factory, source)
    error = project.errors()[0]
    assert error.kind == ErrorKind.DEFINITION_CYCLE.value
    assert error.semantic_refs == (
        "measure:sales.orders.left",
        "measure:sales.orders.right",
        "measure:sales.orders.left",
    )
    assert project._compiled_state is None


def test_direct_and_symbolic_named_metric_dependencies_bind_to_one_definition(
    semantic_project_factory: ProjectFactory,
) -> None:
    from marivo._definition.graph import computation_fingerprint

    source = (
        _PHYSICAL
        + """
@ms.metric()
def revenue():
    return amount.sum()
lookup_recipe = ms.ref.metric("sales.revenue") + 1
@ms.metric()
def lookup():
    return lookup_recipe
@ms.metric()
def direct():
    return revenue + 1
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    direct = consume_metric(catalog.get("metric:sales.direct").value)
    lookup = consume_metric(catalog.get("metric:sales.lookup").value)
    assert direct._declared[0][0] == lookup._declared[0][0]
    assert direct._declared[0][0].ref.path == "sales.revenue"
    assert computation_fingerprint(direct._declared[-1][1]) == computation_fingerprint(
        lookup._declared[-1][1]
    )


@pytest.mark.parametrize("closed", (False, True))
def test_malformed_measure_additivity_rejects_equally_for_symbols_and_closed_rows(
    closed: bool,
) -> None:
    dependency = (
        catalog_value(_physical_scope("CNY", ()), "sales.orders.amount", kind="field")
        if closed
        else ms.ref.measure("sales.orders.amount")
    )
    for bad in ("additive_all", ms.additive_all, object()):
        case = f"closed={closed}, additivity={type(bad).__name__}"
        with pytest.raises(SemanticExpressionError) as failure:

            @ms.measure(additivity=bad)
            def malformed_policy():
                return dependency * 2

        assert failure.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value, case
        assert "closed additivity policy" in failure.value.message, case
        assert "ms.additive_all(...)" in failure.value.expected, case
        assert failure.value.received == type(bad).__name__, case
        assert failure.value.repair is not None, case


def test_invalid_duplicate_argument_stops_the_public_model_at_capture(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    marker = tmp_path / "after-decorator"
    source = (
        _PHYSICAL
        + f"""
recipe = amount.round(1, digits=2).sum()
@ms.metric()
def invalid_duplicate():
    return recipe
from pathlib import Path
Path({str(marker)!r}).write_text("unexpected")
"""
    )
    project = _project(semantic_project_factory, source)
    assert not marker.exists()
    assert project._compiled_state is None
    error = project.errors()[0]
    assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert "invalid_duplicate" in error.message
    assert "more than once" in error.received


def test_valid_forward_parameter_roles_bind_without_early_lookup(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL
        + """
paid_at = ms.ref.time_dimension("sales.orders.paid_at")
quantity = ms.ref.measure("sales.orders.quantity")
region = ms.ref.dimension("sales.orders.region")
recipe = amount.weighted_mean(weight=quantity, time=paid_at).per(region).quantile(q=0.5)
@ms.metric()
def selected_median():
    return recipe
ms.time_dimension_column(name="paid_at", entity=orders, column="paid_at", parse=ms.timestamp(timezone="UTC"))
ms.measure_column(name="quantity", entity=orders, column="quantity", additivity=ms.additive_all(), unit="1")
ms.dimension_column(name="region", entity=orders, column="region")
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    assert consume_metric(catalog.get("metric:sales.selected_median").value).unit == "CNY"
    assert not inspect_deferred(catalog.get("metric:sales.selected_median").value).symbols


_MALFORMED_CASE_CALLS = (
    "ibis.cases(amount.sum())",
    "ibis.cases((amount > 0,))",
    "ibis.cases((amount > 0, amount, 0))",
    "ibis.cases(branch=(amount > 0,))",
)


def test_top_cases_malformed_pairs_stop_public_model_at_capture(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    for index, case_call in enumerate(_MALFORMED_CASE_CALLS):
        marker = tmp_path / f"after-cases-decorator-{index}"
        source = (
            _PHYSICAL
            + f"""
import ibis
recipe = {case_call}
@ms.metric()
def malformed_cases():
    return recipe
from pathlib import Path
Path({str(marker)!r}).write_text("unexpected")
"""
        )
        project = _project(semantic_project_factory, source)
        assert not marker.exists()
        assert project._compiled_state is None
        error = project.errors()[0]
        assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert "malformed_cases" in error.message
        assert "branch pairs" in error.received


_MALFORMED_TOP_CALLS = (
    "ibis.ifelse(amount > 0, amount)",
    "ibis.ifelse(amount > 0, amount, 0, 1)",
    "ibis.ifelse(amount > 0, amount, false_expr=0)",
    "ibis.coalesce(arg=amount)",
    "ibis.least(amount, unknown=1)",
    "ibis.greatest()",
    "ibis.cases(branch=(amount > 0, amount), unknown=1)",
    "ibis.literal(1, 'int64', type='float64') + amount",
    "ibis.literal(value=1, unknown=amount) + amount",
    "ibis.literal(amount)",
    "ibis.literal('invalid-int', type='int64') + amount",
)


def test_top_constructor_signature_errors_are_owned_by_public_capture(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    for index, top_call in enumerate(_MALFORMED_TOP_CALLS):
        marker = tmp_path / f"after-top-decorator-{index}"
        source = (
            _PHYSICAL
            + f"""
import ibis
@ms.measure()
def malformed_top():
    return {top_call}
from pathlib import Path
Path({str(marker)!r}).write_text("unexpected")
"""
        )
        project = _project(semantic_project_factory, source)
        assert not marker.exists()
        assert project._compiled_state is None
        assert len(project.errors()) == 1
        error = project.errors()[0]
        assert isinstance(error, SemanticExpressionError)
        assert error.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert "malformed_top" in error.message or error.semantic_refs == ("malformed_top",)
        assert error.expected and error.received and error.repair is not None


def test_all_admitted_top_constructors_keep_valid_native_spellings(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL
        + """
import ibis
from ibis import cases as choose
positional_cases = ibis.cases((amount > cost, amount), else_=cost)
keyword_cases = choose(branch=(amount > cost, amount), else_=cost)
list_cases = ibis.cases([amount > cost, amount], else_=cost)
@ms.measure()
def native_top_calls():
    zero = ibis.literal(value=0, type="float64")
    positive = ibis.ifelse(amount > 0, amount, zero)
    available = ibis.coalesce(positive, cost)
    bounded = ibis.greatest(ibis.least(available, amount), cost)
    return bounded
@ms.metric()
def all_valid_top_calls():
    return native_top_calls.mean() + positional_cases.mean() + keyword_cases.mean() + list_cases.mean()
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    assert (
        consume_row(catalog_value(catalog, "sales.orders.native_top_calls", kind="field")).unit
        == "CNY"
    )
    assert consume_metric(catalog.get("metric:sales.all_valid_top_calls").value).unit == "CNY"


def test_publication_graph_matches_full_reconstruction_and_survives_failed_reload(
    semantic_project_factory: ProjectFactory,
) -> None:
    from marivo.semantic._compiled_state import definition_graph
    from marivo.semantic.expressions import _Resolver

    metrics = """
@ms.measure()
def margin():
    return amount - cost

@ms.metric()
def a_revenue():
    return margin.sum()

@ms.metric()
def z_total():
    return a_revenue * 2
"""
    project = _project(semantic_project_factory, _PHYSICAL + metrics)
    first = project.catalog()
    state = first._state
    original_version = _Resolver(state).exact(ref_factory.metric("sales.z_total"))
    assert (dict(state.definitions), dict(state.dependencies)) == definition_graph(
        state.registry, state.sidecar
    )
    model = project.semantic_root / "sales" / "objects.py"
    model.write_text(_PHYSICAL + metrics.replace("a_revenue * 2", "a_revenue + orders.count()"))
    failed = project.load()
    assert failed.status == "errored"
    assert failed.registry is None and failed.compiled_state is None

    model.write_text(_PHYSICAL.replace('column="amount"', 'column="net_amount"') + metrics)
    assert project.load().status == "ready"
    second = project.catalog()._state
    assert _Resolver(second).exact(ref_factory.metric("sales.z_total")) != original_version
    assert _Resolver(first._state).exact(ref_factory.metric("sales.z_total")) == original_version
    assert (dict(second.definitions), dict(second.dependencies)) == definition_graph(
        second.registry, second.sidecar
    )
