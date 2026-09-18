"""Pure admission for individually qualified relational scalar methods."""

from __future__ import annotations

import re
from collections.abc import Callable

from marivo.analysis.compiler.normalize import (
    artifact_inputs,
    logical_roots,
    required_entities,
    required_source_dependencies,
)
from marivo.analysis.compiler.predicates import predicate_leaves
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import _CatalogFieldIdentity
from marivo.analysis.observation.contracts import MetricPayload, PopulationPayload, source_owner_of
from marivo.refs import RefPayloadV1
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    TableSourceIR,
    TargetDimensionContract,
    TargetSnapshotVersion,
    TargetValidityVersion,
    TimestampParse,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    LinearNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.validator import normalize_target_dimension

_METHODS = frozenset(
    {
        "session.population",
        "population.where",
        "session.observe",
        "metric.with_dimensions",
        "metric.with_time_axis",
        "metric.aggregate",
        "metric.where",
        "metric.metric",
        "metric.rank",
        "metric.limit",
    }
)


def supports_scalar_type(value: str) -> bool:
    """Recognize the common scalar types, including derived generic Decimal."""
    if value in {
        "string",
        "int8",
        "int16",
        "int32",
        "int64",
        "float32",
        "float64",
        "date",
        "decimal",
    }:
        return True
    decimal = re.fullmatch(r"decimal\(([1-9][0-9]?),\s*([0-9]+)\)", value)
    return decimal is not None and 0 <= int(decimal[2]) <= int(decimal[1]) <= 38


def supports_plain_timestamp(value: str) -> bool:
    """Recognize civil timestamp types up to microseconds."""
    return re.fullmatch(r"timestamp(?:\([0-6]\))?", value) is not None


def supports_timestamp(value: str) -> bool:
    """Recognize native timestamp precision and optional exact physical timezone."""
    if supports_plain_timestamp(value):
        return True
    return re.fullmatch(r"timestamp\('[^']+'(?:, [0-6])?\)", value) is not None


def unsupported_reason(
    dataset: LogicalDataset,
    supported_type: Callable[[str], bool],
    *,
    relationships: bool = False,
    versions: bool = False,
    date_buckets: bool = False,
    timestamp_buckets: bool = False,
    explicit_decimal_sources: bool = False,
    closed_open_null_validity: bool = False,
) -> str | None:
    """Check methods/types without I/O; placement.source_binding owns exact source identity."""
    if artifact_inputs(dataset):
        return "remote retained import is not supported"
    roots = tuple(logical_roots(dataset))
    for root in roots:
        if root.operator_id not in _METHODS:
            return (
                f"{root.operator_id} requires a source preparation or private-state "
                "implementation that this backend has not qualified"
            )
    entities = required_entities(dataset)
    if not entities or (len(entities) != 1 and not relationships):
        return "multi-relation execution is not qualified for this backend"
    if any(not isinstance(entity.source, TableSourceIR) for entity in entities):
        return "only declared table sources are supported"
    if any(entity.version is not None for entity in entities) and not versions:
        return "semantic version selection is not qualified for this backend"
    dependencies = required_source_dependencies(dataset)
    for entry in dependencies.entries:
        for column in entry.columns:
            if not supported_type(column.declared_type):
                return (
                    "unsupported declared source type "
                    f"{column.declared_type[:100]} for Entity {entry.entity.ref.path[:160]} "
                    f"column {column.logical[:100]} -> {column.physical[:100]}"
                )
    entity_paths = {entity.ref.path for entity in entities}
    owner = source_owner_of(dataset)

    def dimension(axis: TargetDimensionContract | None) -> bool:
        return axis is None or (
            axis.entity_ref.path in entity_paths
            and supported_type(axis.logical_type)
            and (
                not axis.is_time_dimension
                or axis.logical_type == "date"
                or (timestamp_buckets and axis.logical_type == "timestamp")
            )
            and (
                axis.parse is None
                or isinstance(axis.parse, DateParse)
                or (
                    timestamp_buckets
                    and axis.logical_type == "timestamp"
                    and isinstance(axis.parse, (DatetimeParse, TimestampParse))
                )
            )
        )

    def temporal(axis: TargetDimensionContract | None) -> bool:
        return axis is None or (
            dimension(axis)
            and (
                axis.logical_type == "date"
                or (timestamp_buckets and axis.logical_type == "timestamp")
            )
        )

    for entity in entities:
        version = entity.version
        if version is not None:
            axes = (
                (version.coordinate_ref,)
                if isinstance(version, TargetSnapshotVersion)
                else (version.valid_from_ref, version.valid_to_ref)
            )
            if any(
                normalize_target_dimension(owner.semantic_registry, axis.path).logical_type
                != "date"
                or not temporal(normalize_target_dimension(owner.semantic_registry, axis.path))
                for axis in axes
            ):
                return "semantic version selection requires qualified native civil-date axes"

    for root in roots:
        payload = root.payload
        if not isinstance(payload, (PopulationPayload, MetricPayload)):
            return "the source payload has no qualified scalar implementation"
        for predicate in predicate_leaves(payload.predicate):
            if predicate.field is not None and isinstance(
                predicate.field.identity, _CatalogFieldIdentity
            ):
                family, _, path = predicate.field.identity.identity_id.partition(":")
                if family in {"dimension", "time_dimension"} and not dimension(
                    normalize_target_dimension(owner.semantic_registry, path)
                ):
                    return "a predicate requires an unsupported dimension or time parser"
        if isinstance(payload, PopulationPayload):
            if payload.sampling is not None:
                return "sampling requires a source-owned single-evaluation implementation"
            if payload.version_selection is not None and not versions:
                return "semantic version selection is not qualified for this backend"
            if not dimension(payload.reference_axis):
                return "the population reference axis is not qualified"
            continue
        definition = payload.definition
        if definition.distinct_memberships or definition.distributions:
            return "membership and distribution state require a source-private implementation"
        if not relationships and (
            any(definition.contribution_paths)
            or any(
                binding.spine_path or any(path for _, path in binding.component_paths)
                for binding in definition.coordinate_paths
            )
        ):
            return "relationship contribution paths are not qualified for this backend"
        if not dimension(definition.reference_axis) or not all(
            dimension(axis) for axis in definition.dimensions
        ):
            return "a Metric dimension requires an unsupported type or parser"
        if definition.time_axis is not None or definition.grain is not None:
            grain = definition.grain
            if (
                not date_buckets
                or not temporal(definition.time_axis)
                or grain is None
                or (
                    grain.kind != "builtin"
                    or grain.count != 1
                    or grain.unit
                    not in (
                        {"hour", "day"}
                        if definition.time_axis is not None
                        and definition.time_axis.logical_type == "timestamp"
                        else {"day", "week", "month", "quarter", "year"}
                    )
                )
            ):
                return "this temporal type, parser or bucket is not qualified for this backend"
        if definition.temporal_snapshot is not None:
            return "semantic calendar buckets are not qualified for this backend"
        for metric in definition.metrics:
            if metric.logical_type == "decimal" and any(
                isinstance(record.node, (RatioNodeV1, LinearNodeV1, WeightedMeanAggregateNodeV1))
                or (isinstance(record.node, AggregateNodeV1) and record.node.agg == "mean")
                for record in metric.graph.nodes
            ):
                return "composed Decimal results require resolved precision and scale"
            if metric.cumulative or metric.source_requirements or metric.requires_source_recompute:
                return "this Metric requires unqualified cumulative or source-private state"
            if not supported_type(metric.logical_type):
                return "the Metric result type is not supported"
            for component in metric.components:
                if component.computation_root.path not in entity_paths:
                    return "the Metric computation root is outside the declared source closure"
                if component.status_time_dimension is not None or component.time_fold is not None:
                    return "status-time folds require additional temporal-state qualification"
                if component.requires_source_recompute:
                    return "the Metric requires source-private recomputation"
            for record in metric.graph.nodes:
                node = record.node
                if isinstance(node, RatioNodeV1):
                    continue
                if not isinstance(
                    node, (AggregateNodeV1, WeightedMeanAggregateNodeV1, SliceNodeV1)
                ):
                    return "the Metric graph contains an unqualified computation"
                conditions = node.predicates if isinstance(node, SliceNodeV1) else node.filter
                if any(
                    not dimension(
                        normalize_target_dimension(
                            owner.semantic_registry, condition.dimension_ref.path
                        )
                    )
                    for condition in conditions
                ):
                    return "a Metric slice requires an unsupported dimension or time parser"
                if isinstance(node, SliceNodeV1):
                    continue
                references: tuple[RefPayloadV1, ...]
                if isinstance(node, AggregateNodeV1):
                    if (
                        node.agg not in {"sum", "count", "min", "max", "mean"}
                        or node.fold is not None
                    ):
                        return "the aggregate requires unqualified private or temporal state"
                    references = (node.target_ref,)
                else:
                    references = (node.value_ref, node.weight_ref)
                for target in references:
                    if target.kind.value == "measure" and not any(
                        reference.path == target.path
                        and reference.kind == target.kind
                        and body.source_column is not None
                        for reference, body in owner.sidecar.bodies.items()
                    ):
                        return "only direct-column measures are qualified for this backend"
    if explicit_decimal_sources and any(
        column.declared_type == "decimal"
        for entry in dependencies.entries
        for column in entry.columns
    ):
        return "Decimal source columns require explicit precision and scale"
    if closed_open_null_validity and any(
        isinstance(entity.version, TargetValidityVersion)
        and (entity.version.interval != "closed_open" or entity.version.open_end != (None,))
        for entity in entities
    ):
        return "validity selection is qualified only for closed_open intervals with NULL open ends"
    return None
