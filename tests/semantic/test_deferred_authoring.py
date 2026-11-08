"""Native Deferred declarations and model collection share one authoring protocol."""

from collections.abc import Callable
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.deferred import (
    NamedResolver,
    inspect_deferred,
    resolver_of,
    root_declaration,
)
from marivo._definition.expressions import MetricExpression, PerReduce, SourceAggregate
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic._expression_values import _as_deferred, _ExpressionCarrier
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value

ProjectFactory = Callable[..., SemanticProject]


def _recipe(value: Deferred | _ExpressionCarrier) -> Deferred:
    deferred = _as_deferred(value) if isinstance(value, _ExpressionCarrier) else value
    recipe = resolver_of(deferred)
    return Deferred(recipe.recipe) if isinstance(recipe, NamedResolver) else deferred


_MODEL = '''import ibis
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", unit="CNY", additivity=ms.additive_all())
cost_ref = ms.measure_column(name="cost", entity=orders, column="cost", unit="CNY", additivity=ms.additive_all())
status_ref = ms.dimension_column(name="status", entity=orders, column="status")
amount = amount_ref
cost = cost_ref
status = status_ref
@ms.measure(additivity=ms.additive_all())
def margin():
    """Positive margin with explicit business authorization."""
    positive = (amount > cost).ifelse(amount - cost, 0)
    return positive
@ms.metric()
def revenue():
    return amount.sum()
@ms.metric()
def margin_total():
    return margin.sum()
alias = revenue
trial = revenue + 1
'''


def _catalog(factory: ProjectFactory, source: str = _MODEL) -> SemanticCatalog:
    project = factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": source,
        }
    )
    assert not project.errors()
    return SemanticCatalog(project)


def test_deferred_capture_declaration_collection_and_native_composition(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    revenue = catalog.get("metric:sales.revenue")
    margin = catalog_value(catalog, "sales.orders.margin", kind="measure")
    assert isinstance(amount, ms.RowExpression)
    assert isinstance(revenue, ms.MetricExpression)
    assert isinstance(margin, ms.RowExpression)
    assert tuple(entry.path for entry in catalog.search(kind="metric").items) == (
        "sales.margin_total",
        "sales.revenue",
    )
    assert root_declaration(_recipe(revenue)) is not None
    assert root_declaration(_recipe(revenue + 1)) is None
    assert (
        inspect_deferred(_recipe(revenue)).fingerprint
        == inspect_deferred(_recipe(amount.sum())).fingerprint
    )
    assert consume_row(margin).unit == "CNY"
    index = (revenue / amount.sum()).sqrt()
    assert isinstance(consume_metric(index).node, MetricExpression)
    assert consume_metric(index).unit == "1"


@pytest.mark.parametrize("kind", ("field", "membership"))
@pytest.mark.parametrize("as_tuple", (False, True))
def test_declared_metric_accepts_public_grouping_samples(
    semantic_project_factory: ProjectFactory, kind: str, as_tuple: bool
) -> None:
    grouping = (
        'mv.coordinates(by=(status,), keys=(("paid",),)).axes[0]'
        if kind == "field"
        else 'mv.membership(status, via=(), unmatched="exclude")'
    )
    sample_argument = "samples"
    argument = f"({sample_argument},)" if as_tuple else sample_argument
    source = (
        _MODEL
        + f"""
import marivo.analysis as mv
import marivo.semantic as ms
samples = {grouping}
@ms.metric()
def average_sample():
    return revenue.per({argument}).mean()
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    status = catalog_value(catalog, "sales.orders.status", kind="dimension")
    samples = (
        mv.coordinates(by=(status,), keys=(("paid",),)).axes[0]
        if kind == "field"
        else mv.membership(status, via=(), unmatched="exclude")
    )
    sample = samples
    argument_value = (sample,) if as_tuple else sample
    revenue = catalog.get("metric:sales.revenue")
    declared = catalog.get("metric:sales.average_sample")
    assert revenue.kind == "metric" and declared.kind == "metric"
    expected = revenue.per(argument_value).mean()
    body = next(
        body
        for reference, body in consume_metric(declared)._declared
        if reference.ref.path == "sales.average_sample"
    )
    assert body == consume_metric(expected).node
    assert (
        inspect_deferred(_recipe(declared)).fingerprint
        == inspect_deferred(_recipe(expected)).fingerprint
    )
    declaration = root_declaration(_recipe(declared))
    assert declaration is not None and declaration.reference is not None
    assert set(inspect_deferred(_recipe(declared)).dependencies) == {
        *inspect_deferred(_recipe(expected)).dependencies,
        declaration.reference,
    }


def test_native_branches_retain_dependencies_and_require_sum_authorization(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    cost = catalog_value(catalog, "sales.orders.cost", kind="measure")
    positive = (amount > cost).ifelse(amount - cost, 0)
    assert consume_row(positive).unit == "CNY"
    with pytest.raises(SemanticExpressionError):
        consume_metric(positive.sum())
    assert isinstance(consume_metric(positive.mean()).node, SourceAggregate)
    assert {use.input.reference.ref.path for use in inspect_deferred(_recipe(positive)).uses} == {
        "sales.orders.amount",
        "sales.orders.cost",
    }
    with pytest.raises(SemanticExpressionError):
        consume_metric(amount.sum() + amount)  # type: ignore[operator]  # Reject mixed stages.
    status = catalog_value(catalog, "sales.orders.status", kind="dimension")
    with pytest.raises(SemanticExpressionError):
        consume_row(status.cast("float64"))  # type: ignore[misc]  # Reject nonnumeric casts.


def test_declaration_rejects_helper_before_invocation(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _MODEL
        + """
def helper(value):
    raise AssertionError("author callback was executed")
@ms.metric()
def rejected():
    return helper(amount.sum())
"""
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": source,
        }
    )
    errors = project.errors()
    assert errors
    assert all("author callback was executed" not in str(error) for error in errors)
    assert any("admitted Ibis constructor" in str(error) for error in errors)


def test_native_selection_units_and_reciprocal_authorization(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = _MODEL[: _MODEL.index("@ms.measure")].replace(
        'column="cost", unit="CNY"', 'column="cost", unit="USD"'
    )
    catalog = _catalog(semantic_project_factory, source)
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    cost = catalog_value(catalog, "sales.orders.cost", kind="measure")
    for expression in (amount > cost, amount.least(cost), amount.clip(lower=cost)):
        with pytest.raises(SemanticExpressionError, match="incompatible units"):
            consume_row(expression)
    with pytest.raises(SemanticExpressionError, match="sum authorization"):
        consume_metric((1 / amount).sum())
    assert consume_metric((amount / 2).sum()).unit == "CNY"
    assert consume_row((amount * amount).sqrt()).unit == "CNY"


def test_catalog_description_owns_definition_facts(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = catalog.get("metric:sales.revenue")
    margin = catalog.get("measure:sales.orders.margin")
    assert revenue.kind == "metric"
    assert margin.kind == "measure"
    assert revenue.definition.unit == margin.definition.unit == "CNY"
    assert revenue.id == "metric:sales.revenue"
    assert ".show()" in repr(revenue)
    assert margin.definition.expression


def test_published_metric_rebinds_in_the_consumers_frozen_scope(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    from marivo.semantic._consumption import bind_metric

    original = _catalog(semantic_project_factory)
    revenue = original.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    old_declaration = root_declaration(_recipe(revenue))
    assert old_declaration is not None
    old_reference = old_declaration.reference
    changed_root = original.workspace_dir
    project = semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": _MODEL.replace("return amount.sum()", "return amount.sum() * 2"),
        },
        workspace_dir=changed_root,
    )
    changed = SemanticCatalog(project)
    current = changed.get("metric:sales.revenue")
    assert current.kind == "metric"
    rebound = consume_metric(bind_metric(revenue, changed))
    assert rebound.node == consume_metric(current).node
    assert rebound._declared == consume_metric(current)._declared
    revenue_declaration = root_declaration(_recipe(revenue))
    current_declaration = root_declaration(_recipe(current))
    assert revenue_declaration is not None and current_declaration is not None
    assert revenue_declaration.reference == old_reference
    assert current_declaration.reference != old_reference


def test_sum_has_one_fixed_rule_and_rejects_removed_empty_parameter(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    status = catalog_value(catalog, "sales.orders.status", kind="dimension")
    for expression in (amount.sum(), amount.sum().per(status).sum()):
        node = consume_metric(expression).node
        assert isinstance(node, (SourceAggregate, PerReduce))
        assert not hasattr(node, "empty")
    operations: tuple[Callable[[], object], ...] = (
        lambda: amount.sum(empty="zero"),  # type: ignore[call-arg]  # Reject removed parameter.
        lambda: amount.sum().per(status).sum(empty="null"),  # type: ignore[call-arg]  # Reject removed parameter.
    )
    for operation in operations:
        with pytest.raises(TypeError, match="empty"):
            operation()
    count = consume_metric(amount.count()).node
    assert isinstance(count, SourceAggregate)
