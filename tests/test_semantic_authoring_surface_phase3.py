from __future__ import annotations

import ibis
import pytest

import marivo.semantic as ms
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.semantic_inputs import normalize_dimension_input
from marivo.semantic.catalog import (
    DimensionDetails,
    EntityDetails,
    MeasureDetails,
    MetricDetails,
    SemanticCatalog,
    SemanticKind,
    TimeDimensionDetails,
)
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import SqlProvenance
from marivo.semantic.refs import make_ref

_DOMAIN_PY = """\
import marivo.datasource as md
import marivo.semantic as ms
ms.domain(name="sales", owner='Mina Zhang', default=True)
"""

_MODEL_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

@ms.dimension(entity=orders)
def region(orders):
    return orders.region

@ms.measure(entity=orders, additivity="additive", unit="USD")
def amount(orders):
    return orders.amount

@ms.time_dimension(entity=orders, granularity="day", parse=ms.timestamp(timezone="UTC"))
def order_date(orders):
    return orders.order_date

revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")

@ms.metric(
    entities=[orders],
    additivity="additive",
    provenance=ms.from_sql(sql="select sum(amount) from orders", dialect="duckdb"),
)
def native_revenue(orders):
    return orders.amount.sum()
"""


def _catalog(semantic_project_factory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODEL_PY,
        }
    )
    return SemanticCatalog(project)


def _preview_backend():
    backend = ibis.duckdb.connect(":memory:")
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, order_date TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    return backend


class _PreviewConnectionService:
    def __init__(self, backend):
        self._backend = backend

    def session_backend(self, name):
        return self._backend

    def close_all(self):
        pass


def _patch_preview_connections(project, backend):
    from unittest.mock import patch

    return patch.object(
        project,
        "_connection_service",
        return_value=_PreviewConnectionService(backend),
    )


def test_catalog_get_measure_returns_measure_details(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    obj = catalog.get("measure.sales.orders.amount")
    details = obj.details()

    assert obj.kind == SemanticKind.MEASURE
    assert obj.ref == make_ref("sales.orders.amount", SemanticKind.MEASURE)
    assert isinstance(details, MeasureDetails)
    assert details.ref.kind == SemanticKind.MEASURE
    assert details.entity == make_ref("sales.orders", SemanticKind.ENTITY)
    assert details.additivity == "additive"
    assert details.unit == "USD"
    assert repr(details) == "<MeasureDetails ref=sales.orders.amount; call .show() to inspect>"


def test_catalog_lists_measure_kind_at_top_level_and_under_entity(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert catalog.list(kind=SemanticKind.MEASURE).ids() == ["sales.orders.amount"]
    assert catalog.list("entity.sales.orders", kind=SemanticKind.MEASURE).ids() == [
        "sales.orders.amount"
    ]


def test_entity_children_include_dimension_measure_time_dimension_metrics_and_relationships(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    details = catalog.get("entity.sales.orders").details()

    assert isinstance(details, EntityDetails)
    child_refs = {(child.id, child.kind) for child in details.children}
    assert ("sales.orders.region", SemanticKind.DIMENSION) in child_refs
    assert ("sales.orders.amount", SemanticKind.MEASURE) in child_refs
    assert ("sales.orders.order_date", SemanticKind.TIME_DIMENSION) in child_refs
    assert ("sales.revenue", SemanticKind.METRIC) in child_refs
    assert ("sales.native_revenue", SemanticKind.METRIC) in child_refs


def test_dimension_and_time_dimension_details_do_not_expose_legacy_shape(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    dim = catalog.get("dimension.sales.orders.region").details()
    time_dim = catalog.get("time_dimension.sales.orders.order_date").details()

    assert isinstance(dim, DimensionDetails)
    assert dim.entity.id == "sales.orders"
    assert not hasattr(dim, "dimension_kind")

    assert isinstance(time_dim, TimeDimensionDetails)
    assert time_dim.parse_kind == "timestamp"
    assert time_dim.format is None
    assert time_dim.timezone == "UTC"
    assert time_dim.sample_interval is None
    assert not hasattr(time_dim, "required_prefix")


def test_metric_details_measure_ref_and_provenance_are_phase3_shape(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    aggregate_metric = catalog.get("metric.sales.revenue").details()
    native_metric = catalog.get("metric.sales.native_revenue").details()

    assert isinstance(aggregate_metric, MetricDetails)
    assert aggregate_metric.measure == make_ref("sales.orders.amount", SemanticKind.MEASURE)
    assert aggregate_metric.provenance is None

    assert isinstance(native_metric, MetricDetails)
    assert native_metric.provenance == SqlProvenance(
        sql="select sum(amount) from orders",
        dialect="duckdb",
    )


def test_measure_preview_uses_measure_expression_without_context_columns(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODEL_PY,
        }
    )
    catalog = SemanticCatalog(project)

    backend = _preview_backend()
    with _patch_preview_connections(project, backend):
        preview = catalog.preview(catalog.get("measure.sales.orders.amount").ref, limit=3)

    assert preview.ref == "sales.orders.amount"
    assert preview.kind == "semantic_measure"
    assert "amount" in preview.columns

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(catalog.get("measure.sales.orders.amount").ref, context_columns=("region",))

    assert "context_columns" in str(exc_info.value)
    assert "dimension" in str(exc_info.value)


def test_phase3_public_help_mentions_measure_details_and_current_metric_shape() -> None:
    assert hasattr(ms, "MeasureDetails")

    index = ms.help_text()
    assert "MeasureDetails" in index
    assert "measure" in index

    measure_topic = ms.help_text("measure")
    assert "row-level quantitative measure" in measure_topic
    assert "aggregate" in measure_topic

    metric_topic = ms.help_text("metric")
    assert "Metric constructor decision order" in metric_topic
    assert "@ms.metric" in metric_topic
    assert "ms.aggregate" in metric_topic


def test_analysis_axis_inputs_reject_loaded_measure_objects(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, catalog.get("measure.sales.orders.amount"))

    message = str(exc_info.value)
    assert "measure" in message
    assert "group-by axis" in message
    assert "categorical dimension" in message
