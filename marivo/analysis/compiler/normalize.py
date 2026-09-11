"""Pure discovery of exact reachable semantic sources and captured parameters."""

from __future__ import annotations

from collections.abc import Iterator

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.predicates import predicate_leaves
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CatalogFieldIdentity, _EntityFieldIdentity
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventPayload,
    EventSelectionPayload,
    EventTimeToEventPayload,
)
from marivo.analysis.domains.event_attribution import FunnelAttributePayload
from marivo.analysis.domains.event_comparison import FunnelComparePayload
from marivo.analysis.domains.lifecycle import LifecyclePayload
from marivo.analysis.observation.contracts import (
    MetricPayload,
    PopulationPayload,
    RetainedRowsPayload,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import functional_path, governed_path, path_entities
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.observation.population_sample import PopulationSamplePayload
from marivo.analysis.observation.source_bindings import BoundSourceParametersV1
from marivo.analysis.operators.association_contracts import CorrelatePayload
from marivo.analysis.operators.attribution_contracts import AttributePayload
from marivo.analysis.operators.candidate_contracts import CandidatePayload
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
from marivo.analysis.operators.forecast_contracts import ForecastPayload
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.metric_graph import AggregateNodeV1, WeightedMeanAggregateNodeV1
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity


def logical_roots(dataset: LogicalDataset) -> Iterator[LogicalRootHandle]:
    """Walk definition inputs in dependency order without following retained origins."""
    seen: set[int] = set()

    def visit(root: LogicalRootHandle | MaterializedScanLeafHandle) -> Iterator[LogicalRootHandle]:
        if isinstance(root, MaterializedScanLeafHandle):
            return
        if id(root) not in seen:
            seen.add(id(root))
            for child in root.inputs:
                yield from visit(child.root)
            yield root

    yield from visit(dataset._root)


def required_entities(
    dataset: LogicalDataset, *, registry: Registry | None = None
) -> tuple[TargetEntityContract, ...]:
    """Return normalized, exactly reachable Entities, never unrelated catalog entries."""
    registry = source_owner_of(dataset).semantic_registry if registry is None else registry
    ids: set[str] = set()

    def path(source: str, target: str, *, versioned: bool = False) -> None:
        route = functional_path(registry, source, target, allow_versioned_target=versioned)
        ids.update(path_entities(registry, source, (route,)))

    for root in logical_roots(dataset):
        payload = root.payload
        if isinstance(payload, PopulationPayload):
            entity = payload.entity.ref.path
            ids.add(entity)
            if payload.reference_axis is not None:
                path(entity, payload.reference_axis.entity_ref.path)
            for predicate in predicate_leaves(payload.predicate):
                field = predicate.field
                if field is None or not isinstance(field.identity, _CatalogFieldIdentity):
                    raise compilation_error("exact membership Dimension", "invalid predicate field")
                dimension = normalize_target_dimension(
                    registry, field.identity.identity_id.split(":", 1)[1]
                )
                path(entity, dimension.entity_ref.path)
        elif isinstance(payload, (EventPayload, LifecyclePayload)):
            for step in payload.definition.steps:
                ids.update(path_entities(registry, step.source.ref.path, (step.participant_path,)))
        elif isinstance(payload, EventFunnelPayload):
            for event_axis in payload.axes:
                ids.update(path_entities(registry, event_axis.subject.ref.path, (event_axis.path,)))
        elif isinstance(payload, MetricPayload):
            definition = payload.definition
            entity = definition.entity.ref.path
            ids.add(entity)
            axes = (
                *definition.dimensions,
                *((definition.time_axis,) if definition.time_axis else ()),
            )
            for axis in axes:
                binding = next(
                    (item for item in definition.coordinate_paths if item.ref == axis.ref.path),
                    None,
                )
                if binding is None:
                    path(entity, axis.entity_ref.path)
                else:
                    ids.update(path_entities(registry, entity, (binding.spine_path,)))
                    for coordinate_root, component_path in binding.component_paths:
                        ids.update(path_entities(registry, coordinate_root, (component_path,)))
            for metric in definition.metrics:
                for component_root in metric.computation_roots:
                    path(component_root.path, entity, versioned=True)
                    if definition.reference_axis is not None:
                        path(component_root.path, definition.reference_axis.entity_ref.path)
                    for cumulative in metric.cumulative:
                        axis = normalize_target_dimension(registry, cumulative.over_ref.path)
                        path(component_root.path, axis.entity_ref.path)
                for component in metric.components:
                    if component.status_time_dimension is not None:
                        axis = normalize_target_dimension(
                            registry, component.status_time_dimension.path
                        )
                        path(component.computation_root.path, axis.entity_ref.path)
                for record in metric.graph.nodes:
                    node = record.node
                    if isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
                        for condition in node.filter:
                            dimension = normalize_target_dimension(
                                registry, condition.dimension_ref.path
                            )
                            for component in metric.components:
                                if component.node_id == record.node_id:
                                    source = component.computation_root.path
                                    route = governed_path(
                                        registry, source, dimension.entity_ref.path
                                    )
                                    ids.update(path_entities(registry, source, (route,)))
        elif not isinstance(
            payload,
            (
                EventTimeToEventPayload,
                EventSelectionPayload,
                PopulationSamplePayload,
                RetainedRowsPayload,
                RetainedFoldPayload,
                ComparePayload,
                FunnelComparePayload,
                FunnelAttributePayload,
                AttributePayload,
                CorrelatePayload,
                ForecastPayload,
                CandidatePayload,
                DriverCandidatePayload,
            ),
        ):
            raise compilation_error("closed Observation payload", "unsupported definition payload")
    # A retained identity input supplies its own membership keys. No origin
    # table is needed unless newly authored semantic work actually consumes it.
    roots = tuple(logical_roots(dataset))
    for retained in artifact_inputs(dataset):
        if retained.kind not in ("population", "metric") and (
            str(retained.row_contract.shape_id) != "candidate/entity-outlier@v1"
        ):
            continue
        identities = tuple(
            field.identity
            for field in retained.schema.columns
            if isinstance(field.identity, _EntityFieldIdentity)
        )
        if not identities:
            continue
        if len(identities) != 1:
            raise compilation_error("one retained Entity identity", "invalid membership shape")
        identity = identities[0]
        entity = identity.entity_ref.path
        needed = any(
            (
                isinstance(root.payload, PopulationPayload)
                and root.payload.predicate is not None
                and root.payload.entity.ref.path == entity
            )
            or (
                isinstance(root.payload, (EventPayload, LifecyclePayload))
                and any(
                    entity
                    in path_entities(registry, step.source.ref.path, (step.participant_path,))
                    for step in root.payload.definition.steps
                )
            )
            or (
                isinstance(root.payload, MetricPayload)
                and (
                    any(
                        item.path == entity
                        for metric in root.payload.definition.metrics
                        for item in metric.computation_roots
                    )
                    or root.payload.definition.dimensions
                    or root.payload.definition.time_axis is not None
                )
            )
            for root in roots
        )
        if not needed:
            ids.discard(entity)
    return tuple(normalize_target_entity(registry, name) for name in sorted(ids))


def artifact_inputs(dataset: Dataset) -> tuple[MaterializedDataset, ...]:
    """Walk retained operand occurrences without opening backing or following origins."""
    found: list[MaterializedDataset] = []

    def visit(value: Dataset) -> None:
        if isinstance(value, MaterializedDataset):
            found.append(value)
        elif isinstance(value, LogicalDataset):
            for child in value._inputs:
                visit(child)

    visit(dataset)
    return tuple(found)


def captured_parameters(dataset: LogicalDataset) -> tuple[BoundSourceParametersV1, ...]:
    """Return frozen arguments; conflicting captures cannot share one source realization."""
    found: dict[str, BoundSourceParametersV1] = {}
    for root in logical_roots(dataset):
        payload = root.payload
        if isinstance(
            payload,
            (PopulationPayload, MetricPayload, EventPayload, LifecyclePayload, EventFunnelPayload),
        ):
            for capture in payload.captures:
                previous = found.setdefault(capture.entity_ref.path, capture)
                if previous.exact_value_digest != capture.exact_value_digest:
                    raise compilation_error(
                        "one exact captured source binding", "conflicting source captures"
                    )
    return tuple(found[name] for name in sorted(found))
