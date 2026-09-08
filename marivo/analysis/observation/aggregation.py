"""Entity reduction under normalized component recomputation authority."""

from dataclasses import replace

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.observation.contracts import (
    MetricPayload,
    construction_error,
    metric_contracts,
    metric_definition,
    producer_contract,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import bind_aggregation


def aggregate(dataset: Dataset) -> Dataset:
    """Remove Entity once while retaining exact selected contribution boundaries."""
    from marivo.analysis.datasets.handles import LogicalRootHandle

    root = dataset._root
    if not isinstance(root, LogicalRootHandle) or not isinstance(root.payload, MetricPayload):
        from marivo.analysis.observation.rollup import retained_aggregate

        return retained_aggregate(dataset)
    definition = metric_definition(dataset)
    if not definition.entity_present:
        raise construction_error("Entity axis present before reduction", "already reduced Metric")
    owner = source_owner_of(dataset)
    updated = bind_aggregation(owner, replace(definition, entity_present=False))
    row, row_set = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.aggregate",
        contract_versions=producer_contract("metric.aggregate").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=row_set,
        payload=MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=()),
    )
