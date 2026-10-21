"""Named window Metrics retain closed formulas and explicit source-time roles."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo._definition.expressions import (
    DeclaredMetricRef,
    MetricExpression,
    MetricNode,
    MetricWindow,
    SourceAggregate,
)
from marivo._definition.windows import AllHistory
from marivo.semantic._deferred_semantics import consume_metric

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
import marivo.analysis as mv
warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
event_time_ref = ms.time_dimension_column(name="event_time", entity=orders, column="created_at", is_default=True)
paid_time_ref = ms.time_dimension_column(name="paid_time", entity=orders, column="paid_at")
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
quantity_ref = ms.measure_column(name="quantity", entity=orders, column="quantity", additivity=ms.additive_all())
user_ref = ms.dimension_column(name="user_id", entity=orders, column="user_id")
shipments = ms.entity(name="shipments", datasource=warehouse, source=md.table("shipments"), primary_key=["id"])
ship_time_ref = ms.time_dimension_column(name="ship_time", entity=shipments, column="shipped_at")
frozen = ms.finalize()
amount = frozen.resolve(amount_ref)
quantity = frozen.resolve(quantity_ref)
users = frozen.resolve(user_ref)
event_time = frozen.resolve(event_time_ref)
paid_time = frozen.resolve(paid_time_ref)
ship_time = frozen.resolve(ship_time_ref)
orders_value = frozen.resolve(orders)
rule = mv.all_history()
"""


def _write_project(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "project"
    semantic = root / "models/semantic/sales"
    datasource = root / "models/datasources"
    semantic.mkdir(parents=True)
    datasource.mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname="windows"\n')
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (semantic / "metrics.py").write_text(body)
    return root


def _metric_node(catalog: ms.SemanticCatalog, name: str) -> MetricNode:
    facts = consume_metric(catalog.metrics.get(f"sales.{name}"))
    node = facts.node
    definitions = dict(facts._declared)
    while isinstance(node, DeclaredMetricRef):
        node = definitions[node.ref]
    return node


def _window(catalog: ms.SemanticCatalog, name: str) -> MetricWindow:
    node = _metric_node(catalog, name)
    assert isinstance(node, MetricWindow)
    assert isinstance(node.rule, AllHistory)
    return node


def test_cumulative_load_retains_explicit_time_and_distinct_formula(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
@ms.metric()
def active_users():
    return users.count_distinct(time=event_time)
@ms.metric()
def cum_active_users():
    return active_users.window(rule)
""",
    )
    catalog = ms.load(workspace_dir=root)
    window = _window(catalog, "cum_active_users")
    base = _metric_node(catalog, "active_users")
    assert base == window.operand
    assert isinstance(base, SourceAggregate)
    assert base.reducer == "count_distinct"
    assert base.time is not None
    assert base.time.field.ref.path == "sales.orders.event_time"


def test_cumulative_accepts_complete_derived_formula(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
@ms.metric()
def aov():
    return amount.sum(time=event_time) / orders_value.count(time=event_time)
@ms.metric()
def cumulative_aov():
    return aov.window(rule)
""",
    )
    catalog = ms.load(workspace_dir=root)
    window = _window(catalog, "cumulative_aov")
    assert window.operand == _metric_node(catalog, "aov")
    assert isinstance(window.operand, MetricExpression)


def test_cumulative_accepts_original_mean_formula(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
@ms.metric()
def average():
    return amount.mean(time=event_time)
@ms.metric()
def cumulative_average():
    return average.window(rule)
""",
    )
    catalog = ms.load(workspace_dir=root)
    window = _window(catalog, "cumulative_average")
    base = _metric_node(catalog, "average")
    assert window.operand == base
    assert isinstance(base, SourceAggregate)
    assert base.reducer == "mean"


def test_cumulative_preserves_explicit_time_with_multiple_roles(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
@ms.metric()
def paid_revenue():
    return amount.sum(time=paid_time).window(rule)
""",
    )
    catalog = ms.load(workspace_dir=root)
    window = _window(catalog, "paid_revenue")
    assert isinstance(window.operand, SourceAggregate)
    assert window.operand.time is not None
    assert window.operand.time.field.ref.path == "sales.orders.paid_time"


def test_cumulative_accepts_closed_computed_measure_base(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
@ms.measure(additivity=ms.additive_all())
def net():
    return amount * quantity
@ms.metric()
def custom_revenue():
    return net.sum(time=event_time)
@ms.metric()
def cumulative_revenue():
    return custom_revenue.window(rule)
""",
    )
    catalog = ms.load(workspace_dir=root)
    window = _window(catalog, "cumulative_revenue")
    assert window.operand == _metric_node(catalog, "custom_revenue")
    assert isinstance(_metric_node(catalog, "custom_revenue"), SourceAggregate)


def test_cumulative_rejects_unknown_time_ref(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
missing = frozen.resolve(ms.ref.time_dimension("sales.orders.nonexistent"))
@ms.metric()
def bad():
    return amount.sum(time=missing).window(rule)
""",
    )
    with pytest.raises(ms.errors.SemanticLoadFailed) as caught:
        ms.load(workspace_dir=root)
    assert "sales.orders.nonexistent" in str(caught.value)


def test_cumulative_rejects_non_root_time_axis(tmp_path: Path) -> None:
    root = _write_project(
        tmp_path,
        _MODEL
        + """
@ms.metric()
def bad():
    return amount.sum(time=ship_time).window(rule)
""",
    )
    with pytest.raises(ms.errors.SemanticLoadFailed) as caught:
        ms.load(workspace_dir=root)
    message = str(caught.value)
    assert "Time field belongs to another root" in message
    assert "shipments" in message
    assert "orders" in message
