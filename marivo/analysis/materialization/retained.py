"""Runtime-selected component dependencies and independent physical schema checks."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pyarrow as pa

from marivo.analysis.datasets.base import Dataset, LogicalDataset
from marivo.analysis.datasets.descriptors import DatasetRowContract, _EntityFieldIdentity
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.contracts import ArtifactDescriptor, RetainedPart
from marivo.analysis.materialization.storage import _integrity, _matches_type
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
)
from marivo.analysis.observation.fold_contracts import (
    MetricFoldAuthorityV1,
    RetainedFoldPayload,
    fold_part_role,
    fold_state_columns,
)

_STATE_TYPE_CHECKS: dict[str, tuple[Callable[[pa.DataType], bool], ...]] = {
    "integer": (pa.types.is_integer,),
    "numeric": (pa.types.is_integer, pa.types.is_floating, pa.types.is_decimal),
    "timestamp": (pa.types.is_timestamp,),
    "floating": (pa.types.is_floating,),
    "boolean": (pa.types.is_boolean,),
}


def metric_parts(row: DatasetRowContract) -> tuple[MetricFoldAuthorityV1, ...]:
    semantics = row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        return ()
    retained = {binding[0].value for binding in semantics.metric_bindings if binding[3]}
    return tuple(item for item in semantics.metric_folds if item.field_id in retained)


def selected_parts(descriptor: ArtifactDescriptor, dataset: Dataset) -> tuple[RetainedPart, ...]:
    """Select final dependencies plus every intermediate fold's consumed metrics."""
    required = {fold_part_role(item) for item in metric_parts(dataset.row_contract)}
    pending = [dataset]
    while pending:
        current = pending.pop()
        if isinstance(current, LogicalDataset) and isinstance(current._root, LogicalRootHandle):
            if isinstance(current._root.payload, RetainedFoldPayload):
                required.update(fold_part_role(item) for item in metric_parts(current.row_contract))
            pending.extend(current._inputs)
    available = {part.role: part for part in descriptor.retained_parts}
    if not required.issubset(available):
        _integrity("every consumed registered Metric state role", "missing required Metric part")
    return tuple(part for part in descriptor.retained_parts if part.role in required)


def component_schema(row: DatasetRowContract, role: str, schema: pa.Schema) -> tuple[str, ...]:
    """Validate meaning from the owner; the receipt separately pins physical schema."""
    authority = next((item for item in metric_parts(row) if fold_part_role(item) == role), None)
    if authority is None:
        _integrity("a required role owned by this Metric contract", "unknown Metric part role")
    keys = tuple(field for field in row.schema.columns if field.field_id in row.key_field_ids)
    states = fold_state_columns(authority)
    expected = (*(field.name for field in keys), *(name for name, _, _ in states))
    if tuple(schema.names) != expected:
        _integrity(
            "exact complete contribution keys and component state fields", "part fields differ"
        )
    for field in keys:
        physical = schema.field(field.name).type
        if isinstance(field.identity, _EntityFieldIdentity):
            signature = field.identity.identity_signature
            if not pa.types.is_struct(physical) or tuple(physical.names) != tuple(
                name for name, _ in signature
            ):
                _integrity(
                    "the complete retained Entity identity signature",
                    "part identity schema differs",
                )
            if any(not _matches_type(kind, physical.field(name).type) for name, kind in signature):
                _integrity("the declared Entity identity types", "part identity type differs")
        elif not _matches_type(field.logical_type_id, physical):
            _integrity("the declared contribution coordinate types", "part coordinate type differs")
    for name, kind, _nullable in states:
        physical = schema.field(name).type
        admitted = any(check(physical) for check in _STATE_TYPE_CHECKS.get(kind, ()))
        if not admitted:
            _integrity("the registered component state type class", "part state type differs")
    return tuple(field.name for field in keys)


def checked_component_batches(
    batches: Iterable[pa.RecordBatch], row: DatasetRowContract, role: str
) -> Iterable[pa.RecordBatch]:
    """Check required component support fields as actual data, independently of headers."""
    authority = next((item for item in metric_parts(row) if fold_part_role(item) == role), None)
    if authority is None:
        _integrity("a required Metric component role", "unknown component role")
    nonnull = tuple(name for name, _, nullable in fold_state_columns(authority) if not nullable)
    for batch in batches:
        component_schema(row, role, batch.schema)
        if any(batch.column(name).null_count for name in nonnull):
            _integrity("non-null retained support and coverage", "null required component state")
        yield batch
