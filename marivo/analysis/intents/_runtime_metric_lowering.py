"""Lower public runtime metric descriptors into the shared expression graph."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.runtime_metric import (
    FrozenSliceMap,
    FrozenSlicePredicateV1,
    FrozenSliceValue,
    MetricExprInput,
    RuntimeAggregateExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CanonicalValue,
    CatalogMetricIdentity,
    ExpressionOccurrenceV1,
    ExpressionPresentationV1,
    MetricExpressionGraphV1,
    MetricGraphNodeV1,
    MetricIdentity,
    PresentationLabelV1,
    RatioNodeV1,
    RuntimeExpressionIdentity,
    SliceNodeV1,
)
from marivo.semantic.metric_graph_canonical import (
    canonicalize_slices,
    intern_nodes,
    node_fingerprint,
)
from marivo.semantic.metric_graph_lowering import (
    MetricExpressionForestV1,
    dependency_digest,
    dependency_fingerprint_for_target,
    lower_catalog_metric,
)
from marivo.semantic.refs import MetricRef
from marivo.semantic.validator import Registry


def _canonical_slice_value(value: FrozenSliceValue) -> CanonicalValue:
    if isinstance(value, FrozenSlicePredicateV1):
        return (
            ("op", value.op),
            ("value", _canonical_slice_value(value.value)),
        )
    if isinstance(value, tuple):
        return tuple(_canonical_slice_value(item) for item in value)
    return value


@dataclass
class _RuntimeGraphBuilder:
    registry: Registry

    def __post_init__(self) -> None:
        self.nodes: dict[str, MetricGraphNodeV1] = {}
        self.metric_dependencies: set[str] = set()
        self.measure_dependencies: set[str] = set()
        self.dimension_dependencies: set[str] = set()
        self.labels: list[PresentationLabelV1] = []

    def _intern(self, node: MetricGraphNodeV1) -> str:
        node_id = node_fingerprint(node)
        self.nodes.setdefault(node_id, node)
        return node_id

    def _slice_node(self, child_id: str, by: FrozenSliceMap) -> SliceNodeV1:
        predicates: list[tuple[str, CanonicalValue]] = []
        dependencies: list[tuple[str, str]] = []
        for dimension, value in by.frozen_items():
            dimension_id = dimension.id
            dimension_ir = self.registry.dimensions.get(dimension_id)
            if dimension_ir is None:
                raise ValueError(f"runtime metric slice dimension {dimension_id!r} is not loaded")
            expected_time = dimension_ir.is_time_dimension
            if expected_time != (dimension.kind.value == "time_dimension"):
                raise TypeError(
                    f"runtime metric slice ref kind does not match loaded dimension {dimension_id!r}"
                )
            self.dimension_dependencies.add(dimension_id)
            predicates.append((dimension_id, _canonical_slice_value(value)))
            dependencies.append(
                (
                    dimension_id,
                    dependency_fingerprint_for_target(
                        self.registry,
                        kind="time_dimension" if expected_time else "dimension",
                        semantic_id=dimension_id,
                    ),
                )
            )
        return SliceNodeV1(
            kind="slice",
            child_id=child_id,
            predicates=tuple(predicates),
            predicate_dependencies=tuple(dependencies),
        )

    def _merge_catalog(
        self, metric: MetricRef, *, path: str
    ) -> tuple[str, tuple[ExpressionOccurrenceV1, ...]]:
        metric_id = metric.id
        if metric_id not in self.registry.metrics:
            raise ValueError(f"runtime metric catalog dependency {metric_id!r} is not loaded")
        self.metric_dependencies.add(metric_id)
        lowered = lower_catalog_metric(self.registry, metric_id)
        for record in lowered.graph.nodes:
            self.nodes.setdefault(record.node_id, record.node)

        def remap(source: str) -> str:
            if source == "root[0]":
                return path
            return f"{path}{source.removeprefix('root[0]')}"

        return lowered.graph.roots[0], tuple(
            ExpressionOccurrenceV1(
                path=remap(occurrence.path),
                node_id=occurrence.node_id,
                child_paths=tuple(remap(child) for child in occurrence.child_paths),
            )
            for occurrence in lowered.graph.occurrences
        )

    def lower(
        self, expression: MetricExprInput, *, path: str
    ) -> tuple[str, tuple[ExpressionOccurrenceV1, ...]]:
        if isinstance(expression, MetricRef):
            return self._merge_catalog(expression, path=path)
        label = expression.label
        if label is not None:
            self.labels.append(PresentationLabelV1(occurrence_path=path, label=label))
        if isinstance(expression, RuntimeAggregateExpr):
            measure_id = expression.measure.id
            measure = self.registry.measures.get(measure_id)
            if measure is None:
                raise ValueError(f"runtime metric measure {measure_id!r} is not loaded")
            self.measure_dependencies.add(measure_id)
            aggregate = AggregateNodeV1(
                kind="aggregate",
                target_id=measure_id,
                target_kind="measure",
                dependency_fingerprint=dependency_fingerprint_for_target(
                    self.registry, kind="measure", semantic_id=measure_id
                ),
                agg=expression.agg,
                fold=expression.fold,
                filter=(),
                unit_override=None,
            )
            aggregate_id = self._intern(aggregate)
            if not expression.slice_by:
                return aggregate_id, (ExpressionOccurrenceV1(path=path, node_id=aggregate_id),)
            child_path = f"{path}.child"
            sliced = self._slice_node(aggregate_id, expression.slice_by)
            slice_id = self._intern(sliced)
            return slice_id, (
                ExpressionOccurrenceV1(
                    path=path,
                    node_id=slice_id,
                    child_paths=(child_path,),
                ),
                ExpressionOccurrenceV1(path=child_path, node_id=aggregate_id),
            )
        if isinstance(expression, RuntimeSliceExpr):
            child_path = f"{path}.child"
            child_id, child_occurrences = self.lower(expression.metric, path=child_path)
            node = self._slice_node(child_id, expression.by)
            node_id = self._intern(node)
            return node_id, (
                ExpressionOccurrenceV1(
                    path=path,
                    node_id=node_id,
                    child_paths=(child_path,),
                ),
                *child_occurrences,
            )
        if isinstance(expression, RuntimeRatioExpr):
            numerator_path = f"{path}.numerator"
            denominator_path = f"{path}.denominator"
            numerator_id, numerator_occurrences = self.lower(
                expression.numerator, path=numerator_path
            )
            denominator_id, denominator_occurrences = self.lower(
                expression.denominator, path=denominator_path
            )
            ratio_node = RatioNodeV1(
                kind="ratio",
                numerator_id=numerator_id,
                denominator_id=denominator_id,
                zero_division=expression.zero_division,
                unit_override=None,
            )
            node_id = self._intern(ratio_node)
            return node_id, (
                ExpressionOccurrenceV1(
                    path=path,
                    node_id=node_id,
                    child_paths=(numerator_path, denominator_path),
                ),
                *numerator_occurrences,
                *denominator_occurrences,
            )
        raise TypeError(f"unsupported runtime metric expression {type(expression).__name__}")


def lower_metric_inputs(
    registry: Registry,
    inputs: tuple[MetricRef | RuntimeMetricExpr, ...],
) -> MetricExpressionForestV1:
    """Lower an ordered catalog/runtime forest through one canonical path."""
    if not inputs:
        raise ValueError("metric expression lowering requires at least one root")
    builder = _RuntimeGraphBuilder(registry)
    root_ids: list[str] = []
    occurrences: list[ExpressionOccurrenceV1] = []
    catalog_root_ids: list[str | None] = []
    for index, expression in enumerate(inputs):
        node_id, root_occurrences = builder.lower(expression, path=f"root[{index}]")
        root_ids.append(node_id)
        occurrences.extend(root_occurrences)
        catalog_root_ids.append(expression.id if isinstance(expression, MetricRef) else None)
    submitted = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=tuple(root_ids),
        nodes=intern_nodes(builder.nodes.values()),
        occurrences=tuple(occurrences),
    )
    canonicalized = canonicalize_slices(
        submitted,
        ExpressionPresentationV1(
            schema="metric-presentation/v1",
            labels=tuple(builder.labels),
        ),
    )
    identities: list[MetricIdentity] = []
    for catalog_id, root_id in zip(catalog_root_ids, canonicalized.graph.roots, strict=True):
        if catalog_id is not None:
            identities.append(CatalogMetricIdentity(kind="catalog", metric_id=catalog_id))
        else:
            identities.append(
                RuntimeExpressionIdentity(
                    kind="runtime_expression",
                    expression_schema="metric-expression/v1",
                    expression_fingerprint=root_id,
                )
            )
    return MetricExpressionForestV1(
        graph=canonicalized.graph,
        dependency_digest=dependency_digest(
            registry,
            metric_ids=builder.metric_dependencies,
            measure_ids=builder.measure_dependencies,
            dimension_ids=builder.dimension_dependencies,
        ),
        identities=tuple(identities),
        presentation=canonicalized.presentation,
    )


__all__ = ["lower_metric_inputs"]
