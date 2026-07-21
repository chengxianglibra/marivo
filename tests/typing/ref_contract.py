"""Static acceptance fixture for the sealed generic Ref contract."""

from typing import Never, assert_type

from ibis.expr.types import Table, Value

from marivo.refs import (
    DatasourceKind,
    DimensionKind,
    DomainKind,
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
    DimensionEntry,
    DomainEntry,
    SemanticCatalog,
)

assert_type(ref_factory.datasource("warehouse"), Ref[DatasourceKind])
assert_type(ref_factory.entity("sales.orders"), Ref[EntityKind])
assert_type(ref_factory.dimension("sales.orders.region"), Ref[DimensionKind])
assert_type(ref_factory.time_dimension("sales.orders.ordered_at"), Ref[TimeDimensionKind])
assert_type(ref_factory.measure("sales.orders.revenue"), Ref[MeasureKind])
assert_type(ref_factory.metric("sales.revenue"), Ref[MetricKind])


def _requires_never_ref(_value: Ref[Never]) -> None:
    pass


_requires_never_ref(ref_factory.metric("sales.revenue"))  # type: ignore[arg-type]
Ref[Never]()  # type: ignore[call-arg]


def _field_call_contract(table: Table) -> None:
    assert_type(bind(ref_factory.dimension("sales.orders.region"), table), Value)
    assert_type(bind(ref_factory.time_dimension("sales.orders.ordered_at"), table), Value)
    assert_type(bind(ref_factory.measure("sales.orders.amount"), table), Value)
    bind(ref_factory.metric("sales.revenue"), table)  # type: ignore[arg-type]


def _catalog_collection_contract(catalog: SemanticCatalog) -> None:
    assert_type(catalog.domains, CatalogCollection[DomainKind])
    assert_type(catalog.domains.get("sales"), DomainEntry)
    assert_type(catalog.dimensions, CatalogCollection[DimensionKind])
    assert_type(catalog.dimensions.get("region"), DimensionEntry)
