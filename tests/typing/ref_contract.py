"""Static acceptance fixture for the sealed generic Ref contract."""

from ibis.common.deferred import Deferred
from ibis.expr.types import Table, Value
from typing_extensions import Never, assert_type

import marivo.datasource as md
import marivo.semantic as ms
from marivo._symbolic import (
    EntitySymbol,
    MeasureSymbol,
    PeriodCalendarSymbol,
    TemporalSetSymbol,
)
from marivo.refs import (
    DatasourceKind,
    DimensionKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    TimeDimensionKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.semantic import bind
from marivo.semantic.catalog import (
    Domain,
    Entity,
    Measure,
    Metric,
    SemanticCatalog,
)
from marivo.semantic.catalog_entries import CatalogPage

assert_type(ref_factory.datasource("warehouse"), Ref[DatasourceKind])
assert_type(ref_factory.entity("sales.orders"), Ref[EntityKind])
assert_type(ref_factory.dimension("sales.orders.region"), Ref[DimensionKind])
assert_type(ref_factory.time_dimension("sales.orders.ordered_at"), Ref[TimeDimensionKind])
assert_type(ref_factory.measure("sales.orders.revenue"), Ref[MeasureKind])
assert_type(ref_factory.metric("sales.revenue"), Ref[MetricKind])
assert_type(ms.ref.period_calendar("sales.calendar"), PeriodCalendarSymbol)
assert_type(ms.ref.temporal_set("sales.holidays"), TemporalSetSymbol)
assert_type(ms.ai_context(business_definition="Sales context"), ms.typing.AiContextValue)


def _requires_never_ref(_value: Ref[Never]) -> None:
    pass


_requires_never_ref(ref_factory.metric("sales.revenue"))  # type: ignore[arg-type]
Ref[Never]()  # type: ignore[call-arg]


def _field_call_contract(table: Table) -> None:
    assert_type(bind(ms.ref.dimension("sales.orders.region"), table), Value)
    assert_type(bind(ms.ref.time_dimension("sales.orders.ordered_at"), table), Value)
    assert_type(bind(ms.ref.measure("sales.orders.amount"), table), Value)


def _captured_field_call_contract(table: Table) -> None:
    orders = ms.entity(
        name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
    )
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount", additivity=ms.additive_all()
    )
    assert_type(orders, EntitySymbol)
    assert_type(amount, MeasureSymbol)
    assert_type(bind(amount, table), Value)
    ms.dimension_column(name="wrong", entity=ms.ref.datasource("warehouse"), column="x")  # type: ignore[arg-type]
    ms.dimension_column(name="wrong", entity=amount + 1, column="x")  # type: ignore[arg-type]


def _metric_is_not_a_row_field_contract(table: Table) -> None:
    # The exact ignore must become unused if bind ever accepts Metric lookup keys.
    bind(ref_factory.metric("sales.revenue"), table)  # type: ignore[arg-type]


def _catalog_entry_contract(catalog: SemanticCatalog) -> None:
    assert_type(catalog.search(kind="domain"), CatalogPage[Domain])
    entity = catalog.get("entity:sales.orders")
    if isinstance(entity, Entity):
        assert_type(entity, Entity)
    metric = catalog.get("metric:sales.revenue")
    if isinstance(metric, Metric):
        assert_type(metric, Metric)


def _native_selector_roundtrip(catalog: SemanticCatalog, ordinary: Deferred) -> None:
    for entry in catalog.search().items:
        assert_type(entry, ms.SemanticObject)
        catalog.get(entry.id)
    amount = catalog.get("measure:sales.orders.amount")
    if isinstance(amount, Measure):
        assert_type(amount, Measure)
        for identity in amount.definition.dependencies:
            catalog.get(identity)
    entity = catalog.get("entity:sales.orders")
    assert entity.kind == "entity"
    report = catalog.readiness(refs=[entity])
    for selector in report.analysis_ready_inputs:
        assert_type(selector, ms.SemanticObject)
    catalog.get(ordinary)  # type: ignore[arg-type]  # Exact lookup requires a canonical identity string.
    catalog.get(ms.ref.measure("sales.orders.amount") + 1)  # type: ignore[arg-type]
