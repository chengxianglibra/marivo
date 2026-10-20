"""Static acceptance fixture for the sealed generic Ref contract."""

from ibis.common.deferred import Deferred
from ibis.expr.types import Table, Value
from typing_extensions import Never, assert_type

import marivo.datasource as md
import marivo.semantic as ms
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
    CatalogCollection,
    DomainEntry,
    SemanticCatalog,
)
from marivo.semantic.expressions import Entity, FinalizedDeclarations

assert_type(ref_factory.datasource("warehouse"), Ref[DatasourceKind])
assert_type(ref_factory.entity("sales.orders"), Ref[EntityKind])
assert_type(ref_factory.dimension("sales.orders.region"), Ref[DimensionKind])
assert_type(ref_factory.time_dimension("sales.orders.ordered_at"), Ref[TimeDimensionKind])
assert_type(ref_factory.measure("sales.orders.revenue"), Ref[MeasureKind])
assert_type(ref_factory.metric("sales.revenue"), Ref[MetricKind])
assert_type(ms.ref.period_calendar("sales.calendar"), ms.Ref[ms.PeriodCalendarKind])
assert_type(ms.ref.temporal_set("sales.holidays"), ms.Ref[ms.TemporalSetKind])
assert_type(ms.ref.work_schedule("sales.business_hours"), ms.Ref[ms.WorkScheduleKind])
assert_type(ms.ai_context(business_definition="Sales context"), ms.typing.AiContextValue)


def _requires_never_ref(_value: Ref[Never]) -> None:
    pass


_requires_never_ref(ref_factory.metric("sales.revenue"))  # type: ignore[arg-type]
Ref[Never]()  # type: ignore[call-arg]


def _field_call_contract(table: Table) -> None:
    assert_type(bind(ref_factory.dimension("sales.orders.region"), table), Value)
    assert_type(bind(ref_factory.time_dimension("sales.orders.ordered_at"), table), Value)
    assert_type(bind(ref_factory.measure("sales.orders.amount"), table), Value)


def _captured_field_call_contract(table: Table) -> None:
    orders = ms.entity(
        name="orders", datasource=ref_factory.datasource("warehouse"), source=md.table("orders")
    )
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount", additivity=ms.additive_all()
    )
    assert_type(orders, Ref[EntityKind])
    assert_type(amount, Ref[MeasureKind])
    finalized = ms.finalize()
    assert_type(finalized, FinalizedDeclarations)
    assert_type(finalized.resolve(orders), Entity)
    resolved = finalized.resolve(amount)
    assert_type(resolved, Deferred)
    assert_type(bind(resolved, table), Value)


def _metric_is_not_a_row_field_contract(table: Table) -> None:
    # The exact ignore must become unused if bind ever accepts Metric lookup keys.
    bind(ref_factory.metric("sales.revenue"), table)  # type: ignore[arg-type]


def _catalog_collection_contract(catalog: SemanticCatalog) -> None:
    assert_type(catalog.domains, CatalogCollection[DomainEntry])
    assert_type(catalog.domains.get("sales"), DomainEntry)
    assert_type(catalog.entities.get("sales.orders"), Entity)
    assert_type(catalog.fields.get("sales.orders.region"), Deferred)
    assert_type(catalog.metrics.get("sales.revenue"), Deferred)
