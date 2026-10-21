"""Native Deferred declarations and model collection share one authoring protocol."""

from collections.abc import Callable
from pathlib import Path

import ibis
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
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
    amount = catalog.fields.get("sales.orders.amount")
    revenue = catalog.metrics.get("sales.revenue")
    margin = catalog.fields.get("sales.orders.margin")
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
    sample_argument = "samples" if kind == "field" else "status"
    argument = f"({sample_argument},)" if as_tuple else sample_argument
    options = "" if kind == "field" else ", memberships=(samples,)"
    source = (
        _MODEL
        + f"""
import marivo.analysis as mv
samples = {grouping}
@ms.metric()
def average_sample():
    return revenue.per({argument}{options}).mean()
"""
    )
    catalog = _catalog(semantic_project_factory, source)
    amount = catalog.fields.get("sales.orders.amount")
    status = catalog.fields.get("sales.orders.status")
    samples = (
        mv.coordinates(by=(status,), keys=(("paid",),)).axes[0]
        if kind == "field"
        else mv.membership(status, via=(), unmatched="exclude")
    )
    sample = samples if kind == "field" else status
    argument_value = (sample,) if as_tuple else sample
    expected = (
        amount.sum().per(argument_value).mean()
        if kind == "field"
        else amount.sum().per(argument_value, memberships=(samples,)).mean()
    )
    declared = catalog.metrics.get("sales.average_sample")
    body = next(
        body
        for reference, body in consume_metric(declared)._declared
        if reference.ref.path == "sales.average_sample"
    )
    assert body == consume_metric(expected).node
    assert inspect_deferred(declared).fingerprint == inspect_deferred(expected).fingerprint
    declaration = root_declaration(declared)
    assert declaration is not None and declaration.reference is not None
    assert set(inspect_deferred(declared).dependencies) == {
        *inspect_deferred(expected).dependencies,
        declaration.reference,
    }


def test_native_branches_retain_dependencies_and_require_sum_authorization(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog.fields.get("sales.orders.amount")
    cost = catalog.fields.get("sales.orders.cost")
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
    status = catalog.fields.get("sales.orders.status")
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


@pytest.mark.parametrize("calendar", ["sales.missing", "sales.orders"])
def test_published_window_requires_calendar_in_completed_scope(
    semantic_project_factory: ProjectFactory, calendar: str
) -> None:
    source = (
        _MODEL
        + f"""
import marivo.analysis as mv
quarter = ms.calendar_grain(calendar=ms.ref.period_calendar({calendar!r}), level="quarter")
qtd_rule = mv.to_date(grain=quarter)
@ms.metric()
def qtd():
    return revenue.window(qtd_rule)
"""
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/objects.py": source,
        }
    )
    assert project.errors()
    assert any(f"period_calendar:{calendar}" in str(error) for error in project.errors()), [
        str(error) for error in project.errors()
    ]


def test_native_selection_units_and_reciprocal_authorization(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = _MODEL[: _MODEL.index("@ms.measure")].replace(
        'column="cost", unit="CNY"', 'column="cost", unit="USD"'
    )
    catalog = _catalog(semantic_project_factory, source)
    amount = catalog.fields.get("sales.orders.amount")
    cost = catalog.fields.get("sales.orders.cost")
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
    revenue = original.metrics.get("sales.revenue")
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
    current = changed.metrics.get("sales.revenue")
    rebound = consume_metric_in_state(revenue, changed._state)
    assert rebound.node == consume_metric(current).node
    assert rebound._declared == consume_metric(current)._declared
    assert root_declaration(revenue).reference == old_reference
    assert root_declaration(current).reference != old_reference


def test_sum_defaults_are_normalized_at_each_reduction(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    amount = catalog.fields.get("sales.orders.amount")
    status = catalog.fields.get("sales.orders.status")
    for expression in (amount.sum(), amount.sum().per(status).sum()):
        node = consume_metric(expression).node
        assert isinstance(node, (SourceAggregate, PerReduce)) and node.empty == "null"
    for expression in (amount.sum(empty="zero"), amount.sum().per(status).sum(empty="zero")):
        node = consume_metric(expression).node
        assert isinstance(node, (SourceAggregate, PerReduce)) and node.empty == "zero"
    count = consume_metric(amount.count()).node
    assert isinstance(count, SourceAggregate) and count.empty == "zero"
