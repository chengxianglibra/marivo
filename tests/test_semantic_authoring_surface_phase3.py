from __future__ import annotations

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.semantic_inputs import normalize_dimension_input
from marivo.semantic.catalog import (
    EntityDetails,
    MeasureDetails,
    MeasureEntry,
    MetricDetails,
    SemanticCatalog,
    SemanticKind,
)
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import SqlProvenance
from tests.ref_helpers import make_ref

_DOMAIN_PY = """\
import marivo.datasource as md
import marivo.semantic as ms
ms.domain(name="sales", owner='Mina Zhang', default=True)
"""

_MODEL_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))

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


def _preview_backend(path: str):
    backend = ibis.duckdb.connect(path)
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, order_date TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    return backend


def test_catalog_get_measure_returns_measure_details(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    obj = catalog.require(ms.ref.measure("sales.orders.amount"))
    details = obj.details()

    assert type(obj) is MeasureEntry
    assert obj.key == "measure:sales.orders.amount"
    assert obj.ref.kind == SemanticKind.MEASURE
    assert obj.ref == make_ref("sales.orders.amount", SemanticKind.MEASURE)
    assert isinstance(details, MeasureDetails)
    assert details.ref.kind == SemanticKind.MEASURE
    assert details.entity == make_ref("sales.orders", SemanticKind.ENTITY)
    assert details.additivity == "additive"
    assert details.unit == "USD"
    assert (
        repr(details) == "<MeasureDetails ref=measure:sales.orders.amount; call .show() to inspect>"
    )


def test_catalog_measures_collection_returns_measures(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    assert [ref.key for ref in catalog.measures.refs] == ["measure:sales.orders.amount"]


def test_entity_children_include_dimension_measure_time_dimension_metrics_and_relationships(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    details = catalog.require(ms.ref.entity("sales.orders")).details()

    assert isinstance(details, EntityDetails)
    child_refs = {(child.path, child.kind) for child in details.children}
    assert ("sales.orders.region", SemanticKind.DIMENSION) in child_refs
    assert ("sales.orders.amount", SemanticKind.MEASURE) in child_refs
    assert ("sales.orders.order_date", SemanticKind.TIME_DIMENSION) in child_refs
    assert ("sales.revenue", SemanticKind.METRIC) in child_refs
    assert ("sales.native_revenue", SemanticKind.METRIC) in child_refs


def test_metric_details_measure_ref_and_provenance_are_phase3_shape(
    semantic_project_factory,
) -> None:
    catalog = _catalog(semantic_project_factory)

    aggregate_metric = catalog.require(ms.ref.metric("sales.revenue")).details()
    native_metric = catalog.require(ms.ref.metric("sales.native_revenue")).details()

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
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "warehouse.duckdb"
    backend = _preview_backend(str(database_path))
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": _DOMAIN_PY,
            "sales/models.py": _MODEL_PY,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    snapshot = md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "order_date"),
    )

    preview = catalog.preview(
        catalog.require(ms.ref.measure("sales.orders.amount")).ref,
        using=snapshot,
        limit=3,
    )

    assert preview.ref == "sales.orders.amount"
    assert preview.kind == "semantic_measure"
    assert "amount" in preview.columns

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(
            catalog.require(ms.ref.measure("sales.orders.amount")).ref,
            using=snapshot,
            context_columns=("region",),
        )

    assert "context_columns" in str(exc_info.value)
    assert "dimension" in str(exc_info.value)


def test_phase3_public_help_mentions_measure_details_and_current_metric_shape() -> None:
    assert hasattr(ms, "MeasureDetails")

    index = ms.help_text()
    assert "MeasureDetails" in index
    assert "measure" in index

    measure_topic = ms.help_text("measure")
    assert "Declare a calculated measure" in measure_topic
    assert "additivity" in measure_topic

    metric_topic = ms.help_text("metric")
    assert "ms.metric" in metric_topic
    assert "Signature:" in metric_topic


def test_phase3_cumulative_constructor_is_describable() -> None:
    assert hasattr(ms, "cumulative")

    cumulative_topic = ms.help_text("cumulative")
    assert "ms.cumulative" in cumulative_topic
    assert "cumulative" in cumulative_topic.lower()
    assert "anchor" in cumulative_topic.lower()


def test_analysis_axis_inputs_reject_loaded_measure_objects(semantic_project_factory) -> None:
    catalog = _catalog(semantic_project_factory)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        normalize_dimension_input(catalog, catalog.require(ms.ref.measure("sales.orders.amount")))

    message = str(exc_info.value)
    assert "measure" in message
    assert "exact Ref[dimension | time_dimension]" in message
