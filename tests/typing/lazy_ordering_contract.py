"""Exact paired-state rank and ordered-limit typing."""

from typing_extensions import assert_type

from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
from marivo.refs import ref


def ordering(logical: LogicalMetricDataset, retained: MaterializedMetricDataset) -> None:
    metric = ref.metric("sales.revenue")
    assert_type(logical.rank(logical.fields.metric(metric)), LogicalMetricDataset)
    assert_type(
        logical.rank(
            logical.fields.metric(metric), order="ascending", ties="dense", partition_by=()
        ),
        LogicalMetricDataset,
    )
    assert_type(retained.rank(retained.fields.metric(metric), ties="max"), LogicalMetricDataset)
    assert_type(logical.limit(10), LogicalMetricDataset)
    assert_type(retained.limit(10), LogicalMetricDataset)
