"""Pure logical axis enrichment retaining the original selected Metric boundaries."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN, _CatalogFieldIdentity, _make_schema
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import (
    DimensionInput,
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricDefinition,
    MetricPayload,
    additional_captures,
    metric_contracts,
    path_dependency_fingerprint,
    producer_contract,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import (
    _bind_coordinate_path,
    _validate_spine,
    bind_aggregation,
    normalize_dimension_input,
    path_entities,
)
from marivo.analysis.operators.compare import compare
from marivo.analysis.operators.contracts import (
    ComparePayload,
    CompareSpecV1,
    DeltaSemantics,
    comparison_basis,
)
from marivo.analysis.operators.errors import comparison_error
from marivo.semantic.validator import normalize_target_entity


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


def _expand_metric(dataset: Dataset, axes: tuple[DimensionInput, ...]) -> LogicalDataset:
    definition = _definition(dataset)
    owner = source_owner_of(dataset)
    requested = tuple(normalize_dimension_input(owner, axis, time=False) for axis in axes)
    present = {axis.ref.path for axis in definition.dimensions}
    added = tuple(axis for axis in requested if axis.ref.path not in present)
    bindings = tuple(_bind_coordinate_path(owner, definition, axis) for axis in added)
    paths = (*definition.coordinate_paths, *bindings)
    _validate_spine(owner, definition, paths)
    updated = bind_aggregation(
        owner,
        replace(
            definition,
            dimensions=(*definition.dimensions, *added),
            coordinate_paths=paths,
            coordinate_dependencies=(
                *definition.coordinate_dependencies,
                *(
                    (
                        axis.ref.path,
                        path_dependency_fingerprint(
                            owner,
                            definition.entity.ref.path,
                            (binding.spine_path, *(path for _, path in binding.component_paths)),
                        ),
                    )
                    for axis, binding in zip(added, bindings, strict=True)
                ),
            ),
        ),
    )
    row, rows = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    captures = additional_captures(
        dataset,
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(
                owner.semantic_registry,
                definition.entity.ref.path,
                tuple(
                    path
                    for binding in bindings
                    for path in (binding.spine_path, *(path for _, path in binding.component_paths))
                ),
            )
        ),
    )
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.expand_axes",
        contract_versions=producer_contract("metric.expand_axes").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=rows,
        payload=MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=captures),
    )


def expand_attribute_inputs(
    dataset: Dataset, axes: tuple[DimensionInput, ...]
) -> tuple[Dataset, Dataset, CompareSpecV1]:
    """Resolve both exact logical branches, without following retained origins."""
    current = dataset
    while True:
        root = current._root
        if isinstance(current, MaterializedDataset) or not isinstance(root, LogicalRootHandle):
            raise comparison_error(
                "logical Delta contributions before materialization",
                "axis expansion crosses a retained Delta boundary",
                repair="Declare the requested axes on both logical Metric operands before materialization.",
            )
        if isinstance(root.payload, ComparePayload):
            break
        if len(current._inputs) != 1:
            raise comparison_error("one exact Metric comparison origin", "missing logical origin")
        current = current._inputs[0]
    left = _expand_metric(current._inputs[0], axes)
    right = _expand_metric(current._inputs[1], axes)
    if left.row_contract.shape_id.local_shape_id.startswith("entity-"):
        original = root.payload.spec
        meaning = original.output_row.family_semantics
        left_semantics, right_semantics = (
            left.row_contract.family_semantics,
            right.row_contract.family_semantics,
        )
        if (
            not isinstance(meaning, DeltaSemantics)
            or not isinstance(left_semantics, EntityPresentMetricSemantics)
            or not isinstance(right_semantics, EntityPresentMetricSemantics)
        ):
            raise comparison_error(
                "exact Entity expansion authority", "invalid internal comparison"
            )
        coordinates = tuple(
            field
            for field in left.schema.columns
            if field.field_id in left.row_contract.coordinate_field_ids
        )
        generated = tuple(
            field
            for field in original.output_row.schema.columns
            if field.field_id not in original.output_row.coordinate_field_ids
        )
        output_row = replace(
            original.output_row,
            _token=_CORE_TOKEN,
            schema=_make_schema((*coordinates, *generated)),
            coordinate_field_ids=tuple(field.field_id for field in coordinates),
            key_field_ids=tuple(field.field_id for field in coordinates),
            family_semantics=replace(
                meaning,
                _token=_CORE_TOKEN,
                current_fold_authority=left_semantics.fold_authority,
                baseline_fold_authority=right_semantics.fold_authority,
            ),
        )
        return (
            left,
            right,
            replace(
                original,
                current_row=left.row_contract,
                current_rows=left.row_set_contract,
                baseline_row=right.row_contract,
                baseline_rows=right.row_set_contract,
                output_row=output_row,
                current_basis=comparison_basis(left),
                baseline_basis=comparison_basis(right),
            ),
        )
    expanded = compare(left, right)
    expanded_root = expanded._root
    if not isinstance(expanded_root, LogicalRootHandle) or not isinstance(
        expanded_root.payload, ComparePayload
    ):
        raise comparison_error("registered expanded comparison", "invalid expansion output")
    return left, right, expanded_root.payload.spec
