"""Loaded simple and derived metric details and rendering."""

from __future__ import annotations

import marivo.semantic as ms
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SemanticCatalog,
    SimpleMetricDetails,
)

# ---------------------------------------------------------------------------
# _build_metric_object populates split fields
# ---------------------------------------------------------------------------

_DOMAIN_PY = """\
import marivo.datasource as md
import marivo.semantic as ms
ms.domain(name="sales", owner='Mina Zhang', default=True)
"""

_MODELS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))

@ms.measure(entity=orders, additivity=ms.additive_all())
def amount(orders): return orders.amount

@ms.time_dimension(entity=orders, granularity="day", parse=ms.timestamp(timezone="UTC"))
def order_date(orders): return orders.order_date

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
order_rows = ms.count(entity=orders, name="order_rows")
aov = ms.ratio(name="aov", numerator=revenue, denominator=order_count)
"""


def _make_catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODELS_PY,
        }
    )
    return SemanticCatalog(project)


def test_build_metric_object_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    rev = catalog.require(ms.ref.metric("sales.revenue")).details()
    assert isinstance(rev, SimpleMetricDetails)
    assert rev.metric_type == "simple"
    assert rev.aggregation == "sum"
    assert rev.measure is not None
    assert rev.measure.path == "sales.orders.amount"
    assert rev.measure.kind == "measure"
    assert rev.additivity == "additive"
    assert rev.fold is None


def test_build_metric_object_count_target(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    order_rows = catalog.require(ms.ref.metric("sales.order_rows")).details()
    assert isinstance(order_rows, SimpleMetricDetails)
    assert order_rows.aggregation == "count"
    assert order_rows.measure is None
    assert order_rows.aggregation_target is not None
    assert order_rows.aggregation_target.path == "sales.orders"
    assert order_rows.aggregation_target.kind == "entity"
    assert order_rows.aggregation_target_kind == "entity"
    assert order_rows.additivity == "additive"
    assert order_rows.unit == "1"


def test_build_metric_object_derived_ratio(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    aov = catalog.require(ms.ref.metric("sales.aov")).details()
    assert isinstance(aov, DerivedMetricDetails)
    assert aov.metric_type == "derived"
    assert aov.composition == "ratio"
    assert dict(aov.components).keys() == {"numerator", "denominator"}
    assert aov.additivity == "non_additive"


def test_derived_metric_details_render_includes_composition(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    aov = catalog.require(ms.ref.metric("sales.aov")).details()
    assert isinstance(aov, DerivedMetricDetails)
    rendered = aov.render()
    assert "composition: ratio" in rendered
    assert "type: derived" in rendered


def test_simple_metric_details_render_includes_additivity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    rev = catalog.require(ms.ref.metric("sales.revenue")).details()
    assert isinstance(rev, SimpleMetricDetails)
    rendered = rev.render()
    assert "additivity: additive" in rendered
    assert "type: simple" in rendered


def test_build_indexes_preserve_reverse_edges_and_sorted_multi_root_axes(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODELS_PY
            + """\
z_region = ms.dimension_column(name="z_region", entity=orders, column="region")
a_region = ms.dimension_column(name="a_region", entity=orders, column="region")
weighted = ms.weighted_mean(name="weighted", value=amount, weight=amount)
repeated = ms.linear(name="repeated", add=[revenue, revenue], subtract=[order_count])
users = ms.entity(name="users", datasource=ms.ref.datasource("warehouse"), source=md.table("users"))
user_region = ms.dimension_column(name="region", entity=users, column="region")
user_count = ms.count(name="user_count", entity=users)
multi_root = ms.ratio(name="multi_root", numerator=user_count, denominator=repeated)
""",
        }
    )
    catalog = SemanticCatalog(project)
    assert catalog.measures.get("sales.orders.amount").details().dependents == (
        ms.ref.metric("sales.revenue"),
        ms.ref.metric("sales.order_count"),
        ms.ref.metric("sales.weighted"),
    )
    assert catalog.metrics.get("sales.revenue").details().dependents == (
        ms.ref.metric("sales.aov"),
        ms.ref.metric("sales.repeated"),
    )
    assert catalog.metrics.get("sales.weighted").details().measure_lineage == (
        ("value", ms.ref.measure("sales.orders.amount")),
        ("weight", ms.ref.measure("sales.orders.amount")),
    )
    details = catalog.metrics.get("sales.multi_root").details()
    assert details.effective_entities == (
        ms.ref.entity("sales.users"),
        ms.ref.entity("sales.orders"),
    )
    assert details.candidate_dimensions == (
        ms.ref.dimension("sales.orders.a_region"),
        ms.ref.dimension("sales.orders.z_region"),
        ms.ref.dimension("sales.users.region"),
    )
    assert details.candidate_time_dimensions == (ms.ref.time_dimension("sales.orders.order_date"),)
    assert details.measure_lineage == (
        ("denominator.term0", ms.ref.measure("sales.orders.amount")),
        ("denominator.term1", ms.ref.measure("sales.orders.amount")),
        ("denominator.term2", ms.ref.measure("sales.orders.amount")),
    )
