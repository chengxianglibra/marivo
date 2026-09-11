"""Exact private paired-family source and operator typing acceptance."""

from typing_extensions import assert_type

from marivo.analysis import grain
from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.analysis.observation.predicates import AnalysisPredicate, eq, gt
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref


def sources(sources: LazySources) -> None:
    revenue = ref.metric("sales.revenue")
    assert_type(sources.population(ref.entity("sales.orders")), LogicalPopulationDataset)
    assert_type(sources.observe(revenue), LogicalMetricDataset)
    assert_type(sources.observe([revenue]), LogicalMetricDataset)
    assert_type(eq(revenue, 0), AnalysisPredicate)


def states(
    logical: LogicalMetricDataset,
    retained: MaterializedMetricDataset,
    membership: MaterializedPopulationDataset,
) -> None:
    revenue = ref.metric("sales.revenue")
    assert_type(logical.execute(), MaterializedMetricDataset)
    assert_type(logical.where(gt(revenue, 0)), LogicalMetricDataset)
    assert_type(retained.where(gt(revenue, 0)), LogicalMetricDataset)
    assert_type(retained.metric(revenue), LogicalMetricDataset)
    assert_type(retained.metric(retained.fields.get("revenue")), LogicalMetricDataset)
    assert_type(logical.metric(logical.fields.get("revenue")), LogicalMetricDataset)
    assert_type(
        logical.with_dimensions(ref.dimension("sales.customers.region")), LogicalMetricDataset
    )
    assert_type(
        logical.with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day")),
        LogicalMetricDataset,
    )
    assert_type(logical.aggregate(), LogicalMetricDataset)
    assert_type(
        membership.where(eq(ref.dimension("sales.customers.region"), "EU")),
        LogicalPopulationDataset,
    )
