"""Tests for metric-split Plan 3: catalog / DTO / reader / help."""

from __future__ import annotations

import dataclasses

from marivo.semantic import dtos
from marivo.semantic.catalog import MetricDetails, SemanticCatalog

# ---------------------------------------------------------------------------
# Task 1: MetricDetails new shape
# ---------------------------------------------------------------------------


def test_metric_details_has_split_fields_not_legacy():
    names = {f.name for f in dataclasses.fields(MetricDetails)}
    # New split fields must be present
    assert {"metric_type", "aggregation", "measure", "composition", "additivity", "fold"} <= names
    # Legacy fields must be gone
    assert "is_derived" not in names
    assert "decomposition" not in names
    assert "component_metrics" not in names


# ---------------------------------------------------------------------------
# Task 2: _build_metric_object populates split fields
# ---------------------------------------------------------------------------

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", default=True, description="Sales model.")
"""

_MODELS_PY = """\
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive")
def amount(orders): return orders.amount

@ms.time_dimension(entity=orders, data_type="timestamp", granularity="day")
def order_date(orders): return orders.order_date

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
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


def test_build_metric_object_simple_metric(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    rev = catalog.get("sales.revenue").details()
    assert rev.metric_type == "simple"
    assert rev.aggregation == "sum"
    assert rev.measure is not None
    assert rev.measure.ref == "sales.orders.amount"
    assert rev.additivity == "additive"
    assert rev.composition is None
    assert rev.components == ()
    assert rev.fold is None


def test_build_metric_object_derived_ratio(semantic_project_factory):
    catalog = _make_catalog(semantic_project_factory)
    aov = catalog.get("sales.aov").details()
    assert aov.metric_type == "derived"
    assert aov.composition == "ratio"
    assert dict(aov.components).keys() == {"numerator", "denominator"}
    assert aov.additivity == "non_additive"


# ---------------------------------------------------------------------------
# Task 3: ComponentFact / DerivedMetricBrief composition_kind
# ---------------------------------------------------------------------------


def test_component_and_brief_use_composition_kind():
    cf_fields = {f.name for f in dataclasses.fields(dtos.ComponentFact)}
    assert "composition_kind" in cf_fields and "decomposition_kind" not in cf_fields
    brief_fields = {f.name for f in dataclasses.fields(dtos.DerivedMetricBrief)}
    assert "composition_kind" in brief_fields and "decomposition_kind" not in brief_fields


# ---------------------------------------------------------------------------
# Task 5: Help topics reflect split model
# ---------------------------------------------------------------------------


def test_help_topics_reflect_split():
    import marivo.semantic as ms

    # The help index text must mention the new topics
    index = ms.help_text()
    assert "simple_metric" in index
    assert "composition" in index
    comp = ms.help_text("composition")
    assert "ms.sum()" not in comp
    assert "ratio" in comp
    assert "linear" in comp
