"""Tests for metric-split Plan 3: catalog / DTO / reader / help."""

from __future__ import annotations

import marivo.semantic as ms
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SemanticCatalog,
    SimpleMetricDetails,
)

# ---------------------------------------------------------------------------
# Task 2: _build_metric_object populates split fields
# ---------------------------------------------------------------------------

_DOMAIN_PY = """\
import marivo.datasource as md
import marivo.semantic as ms
ms.domain(name="sales", owner='Mina Zhang', default=True)
"""

_MODELS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.Ref.datasource("warehouse"), source=md.table("orders"))

@ms.measure(entity=orders, additivity="additive")
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
    rev = catalog.require(ms.Ref.metric("sales.revenue")).details()
    assert isinstance(rev, SimpleMetricDetails)
    assert rev.metric_type == "simple"
    assert rev.aggregation == "sum"
    assert rev.measure is not None
    assert rev.measure.path == "sales.orders.amount"
    assert rev.measure.kind == "measure"
    assert rev.provenance is None
    assert rev.additivity == "additive"
    assert rev.fold is None


def test_build_metric_object_count_target(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    order_rows = catalog.require(ms.Ref.metric("sales.order_rows")).details()
    assert isinstance(order_rows, SimpleMetricDetails)
    assert order_rows.aggregation == "count"
    assert order_rows.measure is None
    assert order_rows.aggregation_target is not None
    assert order_rows.aggregation_target.path == "sales.orders"
    assert order_rows.aggregation_target.kind == "entity"
    assert order_rows.aggregation_target_kind == "entity"
    assert order_rows.additivity == "additive"
    assert order_rows.unit is None


def test_build_metric_object_derived_ratio(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    aov = catalog.require(ms.Ref.metric("sales.aov")).details()
    assert isinstance(aov, DerivedMetricDetails)
    assert aov.metric_type == "derived"
    assert aov.composition == "ratio"
    assert dict(aov.components).keys() == {"numerator", "denominator"}
    assert aov.additivity == "non_additive"


def test_derived_metric_details_render_includes_composition(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    aov = catalog.require(ms.Ref.metric("sales.aov")).details()
    assert isinstance(aov, DerivedMetricDetails)
    rendered = aov.render()
    assert "composition: ratio" in rendered
    assert "type: derived" in rendered


def test_simple_metric_details_render_includes_additivity(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    rev = catalog.require(ms.Ref.metric("sales.revenue")).details()
    assert isinstance(rev, SimpleMetricDetails)
    rendered = rev.render()
    assert "additivity: additive" in rendered
    assert "type: simple" in rendered


# ---------------------------------------------------------------------------
# Task 5: Help topics reflect split model
# ---------------------------------------------------------------------------


def test_help_topics_reflect_split():
    import marivo.semantic as ms

    # The help index text must mention metric-related capabilities
    index = ms.help_text()
    assert "metric" in index
    assert "ratio" in index
    assert "linear" in index
    assert "cumulative" in index
    assert "weighted_average" in index

    # The metric capability help must reference the constructor family
    metric_help = ms.help_text("metric")
    assert "ms.metric" in metric_help
