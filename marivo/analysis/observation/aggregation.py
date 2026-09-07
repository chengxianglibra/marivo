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
    owner_of,
)


def aggregate(dataset: Dataset) -> Dataset:
    """Remove Entity once while retaining exact selected contribution boundaries."""
    definition = metric_definition(dataset)
    if not definition.entity_present:
        raise construction_error("Entity axis present before reduction", "already reduced Metric")
    if any(not item.supports_coordinate_aggregation for item in definition.metrics):
        raise construction_error(
            "exact component coordinate recomputation for every Metric",
            "unsupported aggregation contract",
        )
    updated = replace(definition, entity_present=False)
    owner = owner_of(dataset)
    row, row_set = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.aggregate",
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=row_set,
        payload=MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=()),
    )
