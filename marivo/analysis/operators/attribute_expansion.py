"""Pure logical axis enrichment retaining the original selected Metric boundaries."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis.datasets.base import Dataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CatalogFieldIdentity
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricDefinition,
    MetricPayload,
)
from marivo.analysis.operators.errors import comparison_error


def _definition(dataset: Dataset) -> MetricDefinition:
    if isinstance(dataset, MaterializedDataset):
        raise comparison_error(
            "logical Metric contributions before materialization",
            "axis expansion crosses a retained Metric boundary",
            repair="Declare the requested axes on both logical Metric operands before materialization.",
        )
    root = dataset._root
    if not isinstance(root, LogicalRootHandle):
        raise comparison_error("logical Metric construction authority", "missing definition")
    if isinstance(root.payload, MetricPayload):
        definition = root.payload.definition
    elif len(dataset._inputs) == 1:
        definition = _definition(dataset._inputs[0])
    else:
        raise comparison_error("logical Metric construction authority", "missing definition")
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise comparison_error("Metric row semantics", "invalid expansion input")
    retained = {
        field.identity.identity_id.split(":", 1)[1]
        for field in dataset.schema.columns
        if isinstance(field.identity, _CatalogFieldIdentity) and field.role_id == "dimension"
    }
    has_time = any(field.role_id == "time_dimension" for field in dataset.schema.columns)
    return replace(
        definition,
        entity_present=isinstance(semantics, EntityPresentMetricSemantics),
        dimensions=tuple(axis for axis in definition.dimensions if axis.ref.path in retained),
        time_axis=definition.time_axis if has_time else None,
        coordinate_paths=tuple(
            path
            for path in definition.coordinate_paths
            if path.ref in retained
            or (
                has_time
                and definition.time_axis is not None
                and path.ref == definition.time_axis.ref.path
            )
        ),
        grain=semantics.fold_time_grain,
    )
