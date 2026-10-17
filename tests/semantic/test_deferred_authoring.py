"""Native Deferred declarations and model collection share one authoring protocol."""

from collections.abc import Callable
from pathlib import Path

import ibis
import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo._definition.deferred import inspect_deferred, root_declaration
from marivo._definition.expressions import MetricExpression, PerReduce, SourceAggregate
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject

ProjectFactory = Callable[..., SemanticProject]

_MODEL = '''import ibis
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", unit="CNY", additivity=ms.additive_all())
cost_ref = ms.measure_column(name="cost", entity=orders, column="cost", unit="CNY", additivity=ms.additive_all())
status_ref = ms.dimension_column(name="status", entity=orders, column="status")
frozen = ms.finalize()
amount = frozen.resolve(amount_ref)
cost = frozen.resolve(cost_ref)
status = frozen.resolve(status_ref)
@ms.measure(additivity=ms.additive_all())
def margin():
    """Positive margin with explicit business authorization."""
    positive = ibis.ifelse(amount > cost, amount - cost, 0)
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
    amount = catalog.require(ms.ref.measure("sales.orders.amount"))
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    margin = catalog.require(ms.ref.measure("sales.orders.margin"))
    assert isinstance(amount, Deferred)
    assert isinstance(revenue, Deferred)
    assert isinstance(margin, Deferred)
    assert tuple(ref.path for ref in catalog.metrics.refs) == (
        "sales.margin_total",
        "sales.revenue",
    )
    assert root_declaration(revenue) is not None
    assert root_declaration(revenue + 1) is None
    assert inspect_deferred(revenue).fingerprint == inspect_deferred(amount.sum()).fingerprint
    assert consume_row(margin).unit == "CNY"
    index = (revenue / amount.sum()).sqrt()
    assert isinstance(consume_metric(index).node, MetricExpression)
    assert consume_metric(index).unit == "1"


def test_native_branches_retain_dependencies_and_require_sum_authorization(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog.require(ms.ref.measure("sales.orders.amount"))
    cost = catalog.require(ms.ref.measure("sales.orders.cost"))
    positive = ibis.ifelse(amount > cost, amount - cost, 0)
    assert consume_row(positive).unit == "CNY"
    with pytest.raises(SemanticExpressionError):
        consume_metric(positive.sum())
    assert isinstance(consume_metric(positive.mean()).node, SourceAggregate)
    assert {use.input.reference.ref.path for use in inspect_deferred(positive).uses} == {
        "sales.orders.amount",
        "sales.orders.cost",
    }
    with pytest.raises(SemanticExpressionError):
        consume_metric(amount.sum() + amount)
    status = catalog.require(ms.ref.dimension("sales.orders.status"))
    with pytest.raises(SemanticExpressionError):
        consume_row(status.cast("float64"))


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
    amount = catalog.require(ms.ref.measure("sales.orders.amount"))
    cost = catalog.require(ms.ref.measure("sales.orders.cost"))
    for expression in (amount > cost, ibis.least(amount, cost), amount.clip(lower=cost)):
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
    revenue = catalog.describe(ms.ref.metric("sales.revenue"))
    margin = catalog.describe(ms.ref.measure("sales.orders.margin"))
    assert revenue.stage == "published_metric"
    assert revenue.unit == margin.unit == "CNY"
    assert revenue.ref is not None and revenue.ref.ref.path == "sales.revenue"
    assert ".show()" in repr(revenue)
    assert margin.computation is not None


def test_published_metric_rebinds_in_the_consumers_frozen_scope(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    from marivo.semantic._deferred_semantics import consume_metric_in_state

    original = _catalog(semantic_project_factory)
    revenue = original.require(ms.ref.metric("sales.revenue"))
    old_reference = root_declaration(revenue).reference
    changed_root = tmp_path / "changed"
    changed_root.mkdir()
    project = semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": _MODEL.replace("return amount.sum()", "return amount.sum() * 2"),
        },
        workspace_dir=changed_root,
    )
    changed = SemanticCatalog(project)
    current = changed.require(ms.ref.metric("sales.revenue"))
    rebound = consume_metric_in_state(revenue, changed._state)
    assert rebound.node == consume_metric(current).node
    assert rebound._declared == consume_metric(current)._declared
    assert root_declaration(revenue).reference == old_reference
    assert root_declaration(current).reference != old_reference


def test_sum_defaults_are_normalized_at_each_reduction(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog.require(ms.ref.measure("sales.orders.amount"))
    status = catalog.require(ms.ref.dimension("sales.orders.status"))
    for expression in (amount.sum(), amount.sum().per(status).sum()):
        node = consume_metric(expression).node
        assert isinstance(node, (SourceAggregate, PerReduce)) and node.empty == "null"
    for expression in (amount.sum(empty="zero"), amount.sum().per(status).sum(empty="zero")):
        node = consume_metric(expression).node
        assert isinstance(node, (SourceAggregate, PerReduce)) and node.empty == "zero"
    count = consume_metric(amount.count()).node
    assert isinstance(count, SourceAggregate) and count.empty == "zero"
