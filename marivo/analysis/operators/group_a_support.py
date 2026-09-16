"""Pure admission for the implemented single-table scalar Metric closure."""

from __future__ import annotations

from collections.abc import Callable

from marivo.analysis.compiler.normalize import artifact_inputs, logical_roots, required_entities
from marivo.analysis.compiler.predicates import predicate_leaves
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import _CatalogFieldIdentity
from marivo.analysis.observation.contracts import MetricPayload, PopulationPayload, source_owner_of
from marivo.semantic.ir import DateParse, TableSourceIR, TargetDimensionContract
from marivo.semantic.metric_graph import AggregateNodeV1, SliceNodeV1
from marivo.semantic.validator import normalize_target_dimension

_METHODS = frozenset(
    {
        "session.population",
        "population.where",
        "session.observe",
        "metric.with_dimensions",
        "metric.aggregate",
        "metric.where",
        "metric.metric",
        "metric.rank",
        "metric.limit",
    }
)


def _dimension(
    axis: TargetDimensionContract | None, entity: str, supported_type: Callable[[str], bool]
) -> bool:
    return axis is None or (
        axis.entity_ref.path == entity
        and supported_type(axis.logical_type)
        and (axis.parse is None or isinstance(axis.parse, DateParse))
    )


def supports(dataset: LogicalDataset, supported_type: Callable[[str], bool]) -> bool:
    """Admit only an exact, source-only, single-table Group A definition."""
    if artifact_inputs(dataset):
        return False
    roots = tuple(logical_roots(dataset))
    if any(root.operator_id not in _METHODS for root in roots):
        return False
    entities = required_entities(dataset)
    if len(entities) != 1:
        return False
    entity = entities[0]
    if entity.version is not None or not isinstance(entity.source, TableSourceIR):
        return False
    if not all(supported_type(kind) for _, kind in entity.columns):
        return False
    owner = source_owner_of(dataset)
    for root in roots:
        payload = root.payload
        if isinstance(payload, (PopulationPayload, MetricPayload)):
            for predicate in predicate_leaves(payload.predicate):
                if (
                    predicate.field is not None
                    and isinstance(predicate.field.identity, _CatalogFieldIdentity)
                    and predicate.field.identity.identity_id.split(":", 1)[0]
                    in {"dimension", "time_dimension"}
                ):
                    axis = normalize_target_dimension(
                        owner.semantic_registry,
                        predicate.field.identity.identity_id.split(":", 1)[1],
                    )
                    if not _dimension(axis, entity.ref.path, supported_type):
                        return False
        if isinstance(payload, PopulationPayload):
            if (
                payload.sampling is not None
                or payload.version_selection is not None
                or not _dimension(payload.reference_axis, entity.ref.path, supported_type)
            ):
                return False
        elif isinstance(payload, MetricPayload):
            definition = payload.definition
            if (
                definition.time_axis is not None
                or definition.grain is not None
                or definition.temporal_snapshot is not None
                or definition.distinct_memberships
                or definition.distributions
                or any(definition.contribution_paths)
                or any(
                    binding.spine_path or any(path for _, path in binding.component_paths)
                    for binding in definition.coordinate_paths
                )
                or not _dimension(definition.reference_axis, entity.ref.path, supported_type)
                or not all(
                    _dimension(axis, entity.ref.path, supported_type)
                    for axis in definition.dimensions
                )
            ):
                return False
            for metric in definition.metrics:
                if (
                    metric.cumulative
                    or metric.source_requirements
                    or metric.requires_source_recompute
                    or not supported_type(metric.logical_type)
                    or any(
                        component.computation_root.path != entity.ref.path
                        or component.status_time_dimension is not None
                        or component.time_fold is not None
                        or component.requires_source_recompute
                        for component in metric.components
                    )
                ):
                    return False
                for record in metric.graph.nodes:
                    node = record.node
                    conditions = (
                        node.predicates
                        if isinstance(node, SliceNodeV1)
                        else node.filter
                        if isinstance(node, AggregateNodeV1)
                        else ()
                    )
                    if any(
                        not _dimension(
                            normalize_target_dimension(
                                owner.semantic_registry, condition.dimension_ref.path
                            ),
                            entity.ref.path,
                            supported_type,
                        )
                        for condition in conditions
                    ):
                        return False
                    if isinstance(node, SliceNodeV1):
                        continue
                    if (
                        not isinstance(node, AggregateNodeV1)
                        or node.agg not in {"sum", "count", "min", "max"}
                        or node.fold is not None
                    ):
                        return False
                    if node.target_ref.kind.value == "measure" and not any(
                        reference.path == node.target_ref.path
                        and reference.kind == node.target_ref.kind
                        and body.source_column is not None
                        for reference, body in owner.sidecar.bodies.items()
                    ):
                        return False
        else:
            return False
    return True
