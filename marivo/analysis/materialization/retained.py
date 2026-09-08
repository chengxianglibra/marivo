"""Runtime-selected component dependencies and independent physical schema checks."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pyarrow as pa

from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
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


def required_part_roles(dataset: Dataset, *, input_dataset: Dataset | None = None) -> set[str]:
    """Propagate consumed state to the exact input, respecting producer boundaries."""
    from marivo.analysis.operators.attribution_contracts import AttributePayload
    from marivo.analysis.operators.contracts import ComparePayload

    required: set[str] = set()

    def visit(value: Dataset, demanded: set[str]) -> None:
        if value is input_dataset or (
            isinstance(value, MaterializedDataset)
            and isinstance(input_dataset, MaterializedDataset)
            and value.state.artifact_ref == input_dataset.state.artifact_ref
        ):
            required.update(demanded)
            return
        if input_dataset is None:
            required.update(demanded)
        if not isinstance(value, LogicalDataset) or not isinstance(value._root, LogicalRootHandle):
            return
        payload = value._root.payload
        for child in value._inputs:
            if value._root.operator_id == "session.observe":
                child_demand: set[str] = set()
            elif isinstance(payload, (ComparePayload, AttributePayload, RetainedFoldPayload)):
                child_demand = _row_part_roles(child.row_contract)
            else:
                child_demand = demanded
            visit(child, child_demand)

    visit(dataset, _row_part_roles(dataset.row_contract))
    return required


def _row_part_roles(row: DatasetRowContract) -> set[str]:
    if row.shape_id.family_id == "delta":
        from marivo.analysis.operators.attribution_contracts import delta_part_authorities

        return {role for role, _ in delta_part_authorities(row)}
    return {fold_part_role(item) for item in metric_parts(row)}


def selected_parts(
    descriptor: ArtifactDescriptor, dataset: Dataset, *, input_dataset: Dataset | None = None
) -> tuple[RetainedPart, ...]:
    """Select final state and consumed folds on the exact input's dependency path."""
    required = required_part_roles(dataset, input_dataset=input_dataset)
    available = {part.role: part for part in descriptor.retained_parts}
    if not required.issubset(available):
        _integrity("every consumed registered Metric state role", "missing required Metric part")
    return tuple(part for part in descriptor.retained_parts if part.role in required)


def component_schema(row: DatasetRowContract, role: str, schema: pa.Schema) -> tuple[str, ...]:
    """Validate meaning from the owner; the receipt separately pins physical schema."""
    states = _part_state_columns(row, role)
    keys = tuple(field for field in row.schema.columns if field.field_id in row.key_field_ids)
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
    nonnull = tuple(name for name, _, nullable in _part_state_columns(row, role) if not nullable)
    for batch in batches:
        component_schema(row, role, batch.schema)
        if any(batch.column(name).null_count for name in nonnull):
            _integrity("non-null retained support and coverage", "null required component state")
        if row.shape_id.family_id == "delta":
            from marivo.analysis.operators.attribution_contracts import (
                delta_part_authorities,
                delta_presence_name,
                delta_state_name,
            )

            side = role.removeprefix("delta_components.")
            authority = next(item for name, item in delta_part_authorities(row) if name == role)
            present = batch.column(delta_presence_name(side)).to_pylist()
            for name, _, nullable in fold_state_columns(authority):
                values = batch.column(delta_state_name(side, name)).to_pylist()
                if any(
                    (selected and not nullable and value is None)
                    or (not selected and value is not None)
                    for selected, value in zip(present, values, strict=True)
                ):
                    _integrity(
                        "side presence consistent with complete component state",
                        "invalid Delta side component support",
                    )
        yield batch


def _part_state_columns(row: DatasetRowContract, role: str) -> tuple[tuple[str, str, bool], ...]:
    if row.shape_id.family_id == "delta":
        from marivo.analysis.operators.attribution_contracts import (
            delta_part_authorities,
            delta_presence_name,
            delta_state_name,
        )

        authority = next((item for name, item in delta_part_authorities(row) if name == role), None)
        if authority is None:
            _integrity("an exact registered Delta side role", "unknown Delta part role")
        side = role.removeprefix("delta_components.")
        return (
            *(
                (delta_state_name(side, name), kind, True)
                for name, kind, _ in fold_state_columns(authority)
            ),
            (delta_presence_name(side), "boolean", False),
        )
    authority = next((item for item in metric_parts(row) if fold_part_role(item) == role), None)
    if authority is None:
        _integrity("a required role owned by the Metric contract", "unknown Metric part role")
    return fold_state_columns(authority)
