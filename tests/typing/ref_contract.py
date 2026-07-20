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
from marivo.semantic.catalog import (
    CatalogCollection,
    DimensionEntry,
    DomainEntry,
    SemanticCatalog,
)

assert_type(Ref.datasource("warehouse"), Ref[DatasourceKind])
assert_type(Ref.entity("sales.orders"), Ref[EntityKind])
assert_type(Ref.dimension("sales.orders.region"), Ref[DimensionKind])
assert_type(Ref.time_dimension("sales.orders.ordered_at"), Ref[TimeDimensionKind])
assert_type(Ref.measure("sales.orders.revenue"), Ref[MeasureKind])
assert_type(Ref.metric("sales.revenue"), Ref[MetricKind])


def _requires_never_ref(_value: Ref[Never]) -> None:
    pass


_requires_never_ref(Ref.metric("sales.revenue"))  # type: ignore[arg-type]
Ref[Never]()  # type: ignore[call-arg]


def _field_call_contract(table: Table) -> None:
    assert_type(Ref.dimension("sales.orders.region")(table), Value)
    assert_type(Ref.time_dimension("sales.orders.ordered_at")(table), Value)
    assert_type(Ref.measure("sales.orders.amount")(table), Value)
    Ref.metric("sales.revenue")(table)  # type: ignore[misc]


def _catalog_collection_contract(catalog: SemanticCatalog) -> None:
    assert_type(catalog.domains, CatalogCollection[DomainKind])
    assert_type(catalog.domains.get("sales"), DomainEntry)
    assert_type(catalog.dimensions, CatalogCollection[DimensionKind])
    assert_type(catalog.dimensions.get("region"), DimensionEntry)
