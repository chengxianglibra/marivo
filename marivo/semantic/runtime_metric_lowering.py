"""Lower dependency-neutral runtime metric descriptors into the shared graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from marivo.introspection._fuzzy import did_you_mean
from marivo.refs import MetricKind, Ref, RefPayloadV1, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import additivity_bucket
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CanonicalSliceEntryV1,
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
    WeightedMeanAggregateNodeV1,
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
from marivo.semantic.runtime_metric import (
    FrozenSliceMap,
    FrozenSlicePredicateV1,
    FrozenSliceValue,
    RuntimeAggregateExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
)
from marivo.semantic.validator import Registry


class RuntimeMetricLoweringError(ValueError):
    """Typed dependency-neutral failure raised while lowering runtime inputs."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        candidates: Mapping[str, object],
        repairs: tuple[Mapping[str, object], ...] = (),
    ) -> None:
        self.code = code
        self.candidates = dict(candidates)
        self.repairs = tuple(dict(item) for item in repairs)
        super().__init__(message)


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
    sidecar: CompiledExpressionSidecar | None = None

    def __post_init__(self) -> None:
        self.nodes: dict[str, MetricGraphNodeV1] = {}
        self.metric_dependencies: set[str] = set()
        self.measure_dependencies: set[str] = set()
        self.dimension_dependencies: set[str] = set()
        self.labels: list[PresentationLabelV1] = []
        self.root_dependencies: list[set[RefPayloadV1]] = []
        self._active_root_dependencies: set[RefPayloadV1] | None = None

    def begin_root(self) -> None:
        dependencies: set[RefPayloadV1] = set()
        self.root_dependencies.append(dependencies)
        self._active_root_dependencies = dependencies

    def _record_dependency(self, ref: Ref[SemanticKindTag]) -> None:
        if self._active_root_dependencies is None:
            raise RuntimeError("runtime metric root dependency tracking is not active")
        self._active_root_dependencies.add(RefPayloadV1.from_ref(ref))

    def _intern(self, node: MetricGraphNodeV1) -> str:
        node_id = node_fingerprint(node)
        self.nodes.setdefault(node_id, node)
        return node_id

    def _slice_node(self, child_id: str, by: FrozenSliceMap) -> SliceNodeV1:
        predicates: list[CanonicalSliceEntryV1] = []
        dependencies: list[tuple[RefPayloadV1, str]] = []
        for dimension, value in by.frozen_items():
            dimension_id = dimension.path
            dimension_ir = self.registry.dimensions.get(dimension_id)
            if dimension_ir is None:
                raise ValueError(f"runtime metric slice dimension {dimension_id!r} is not loaded")
            expected_time = dimension_ir.is_time_dimension
            if expected_time != (dimension.kind.value == "time_dimension"):
                raise TypeError(
                    f"runtime metric slice ref kind does not match loaded dimension {dimension_id!r}"
                )
            self.dimension_dependencies.add(dimension_id)
            self._record_dependency(dimension)
            payload = RefPayloadV1.from_ref(dimension)
            predicates.append(
                CanonicalSliceEntryV1(
                    dimension_ref=payload,
                    value=_canonical_slice_value(value),
                )
            )
            dependencies.append(
                (
                    payload,
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
        self, metric: Ref[MetricKind], *, path: str
    ) -> tuple[str, tuple[ExpressionOccurrenceV1, ...]]:
        metric_id = metric.path
        if metric_id not in self.registry.metrics:
            raise ValueError(f"runtime metric catalog dependency {metric_id!r} is not loaded")
        self.metric_dependencies.add(metric_id)
        self._record_dependency(metric)
        lowered = lower_catalog_metric(self.registry, metric_id, sidecar=self.sidecar)
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
        self, expression: Ref[MetricKind] | RuntimeMetricExpr, *, path: str
    ) -> tuple[str, tuple[ExpressionOccurrenceV1, ...]]:
        if type(expression) is Ref:
            if expression.kind is not SemanticKind.METRIC:
                raise TypeError("runtime metric catalog dependency must be Ref[metric]")
            return self._merge_catalog(expression, path=path)
        label = expression.label
        if label is not None:
            self.labels.append(PresentationLabelV1(occurrence_path=path, label=label))
        if isinstance(expression, RuntimeAggregateExpr):
            measure_id = expression.measure.path
            measure = self.registry.measures.get(measure_id)
            if measure is None:
                raise ValueError(f"runtime metric measure {measure_id!r} is not loaded")
            self.measure_dependencies.add(measure_id)
            self._record_dependency(expression.measure)
            aggregate = AggregateNodeV1(
                kind="aggregate",
                target_ref=RefPayloadV1.from_ref(expression.measure),
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
        if isinstance(expression, RuntimeWeightedMeanExpr):
            value_id = expression.value.path
            weight_id = expression.weight.path
            value = self.registry.measures.get(value_id)
            weight = self.registry.measures.get(weight_id)
            if value is None:
                suggestions = did_you_mean(value_id, sorted(self.registry.measures))
                raise RuntimeMetricLoweringError(
                    code="runtime-weighted-mean-measure-missing",
                    message=f"Runtime weighted_mean value measure {value_id!r} is not loaded.",
                    candidates={
                        "role": "value",
                        "measure_ref": value_id,
                        "did_you_mean": suggestions,
                    },
                    repairs=(
                        (
                            {
                                "action": "replace_measure_ref",
                                "target": "runtime_metric.weighted_mean",
                                "arg": "value",
                                "value": suggestions[0],
                                "safety": "modeling_decision",
                                "why": f"closest loaded measure ref to {value_id!r}",
                            },
                        )
                        if suggestions
                        else ()
                    ),
                )
            if weight is None:
                suggestions = did_you_mean(weight_id, sorted(self.registry.measures))
                raise RuntimeMetricLoweringError(
                    code="runtime-weighted-mean-measure-missing",
                    message=f"Runtime weighted_mean weight measure {weight_id!r} is not loaded.",
                    candidates={
                        "role": "weight",
                        "measure_ref": weight_id,
                        "did_you_mean": suggestions,
                    },
                    repairs=(
                        (
                            {
                                "action": "replace_measure_ref",
                                "target": "runtime_metric.weighted_mean",
                                "arg": "weight",
                                "value": suggestions[0],
                                "safety": "modeling_decision",
                                "why": f"closest loaded measure ref to {weight_id!r}",
                            },
                        )
                        if suggestions
                        else ()
                    ),
                )
            weight_candidates = sorted(
                measure.semantic_id
                for measure in self.registry.measures.values()
                if measure.entity == value.entity and measure.additivity == "additive"
            )
            if value.entity != weight.entity:
                raise RuntimeMetricLoweringError(
                    code="runtime-weighted-mean-grain-mismatch",
                    message=(
                        "Runtime weighted_mean value and weight must belong to the same entity "
                        "and physical row grain."
                    ),
                    candidates={
                        "value_entity": value.entity,
                        "weight_entity": weight.entity,
                        "additive_weight_refs": weight_candidates,
                    },
                    repairs=(
                        (
                            {
                                "action": "replace_measure_ref",
                                "target": "runtime_metric.weighted_mean",
                                "arg": "weight",
                                "value": weight_candidates[0],
                                "safety": "modeling_decision",
                                "why": (
                                    "the replacement is additive and shares the value "
                                    "measure's entity"
                                ),
                            },
                        )
                        if weight_candidates
                        else ()
                    ),
                )
            if weight.additivity != "additive":
                raise RuntimeMetricLoweringError(
                    code="runtime-weighted-mean-weight-non-additive",
                    message="Runtime weighted_mean weight must be additive.",
                    candidates={
                        "weight_ref": RefPayloadV1.from_ref(expression.weight).to_dict(),
                        "weight_additivity": additivity_bucket(weight.additivity),
                        "additive_weight_refs": weight_candidates,
                    },
                    repairs=(
                        (
                            {
                                "action": "replace_measure_ref",
                                "target": "runtime_metric.weighted_mean",
                                "arg": "weight",
                                "value": weight_candidates[0],
                                "safety": "modeling_decision",
                                "why": (
                                    "weighted_mean requires an additive weight on the value entity"
                                ),
                            },
                        )
                        if weight_candidates
                        else ()
                    ),
                )
            self.measure_dependencies.update((value_id, weight_id))
            self._record_dependency(expression.value)
            self._record_dependency(expression.weight)
            weighted_mean = WeightedMeanAggregateNodeV1(
                kind="weighted_mean",
                value_ref=RefPayloadV1.from_ref(expression.value),
                weight_ref=RefPayloadV1.from_ref(expression.weight),
                value_dependency_fingerprint=dependency_fingerprint_for_target(
                    self.registry, kind="measure", semantic_id=value_id
                ),
                weight_dependency_fingerprint=dependency_fingerprint_for_target(
                    self.registry, kind="measure", semantic_id=weight_id
                ),
                filter=(),
                unit_override=None,
            )
            weighted_mean_id = self._intern(weighted_mean)
            if not expression.slice_by:
                return weighted_mean_id, (
                    ExpressionOccurrenceV1(path=path, node_id=weighted_mean_id),
                )
            child_path = f"{path}.child"
            sliced = self._slice_node(weighted_mean_id, expression.slice_by)
            slice_id = self._intern(sliced)
            return slice_id, (
                ExpressionOccurrenceV1(
                    path=path,
                    node_id=slice_id,
                    child_paths=(child_path,),
                ),
                ExpressionOccurrenceV1(path=child_path, node_id=weighted_mean_id),
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
    inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
    *,
    sidecar: CompiledExpressionSidecar | None = None,
) -> MetricExpressionForestV1:
    """Lower an ordered catalog/runtime forest through one canonical path."""
    if not inputs:
        raise ValueError("metric expression lowering requires at least one root")
    builder = _RuntimeGraphBuilder(registry, sidecar)
    root_ids: list[str] = []
    occurrences: list[ExpressionOccurrenceV1] = []
    catalog_root_ids: list[str | None] = []
    for index, expression in enumerate(inputs):
        builder.begin_root()
        node_id, root_occurrences = builder.lower(expression, path=f"root[{index}]")
        root_ids.append(node_id)
        occurrences.extend(root_occurrences)
        catalog_root_ids.append(expression.path if type(expression) is Ref else None)
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
    if any(catalog_id is None for catalog_id in catalog_root_ids):
        entity_ids: set[str] = set()
        for dependencies in builder.root_dependencies:
            for dependency in dependencies:
                if dependency.kind is SemanticKind.METRIC:
                    metric = registry.metrics.get(dependency.path)
                    if metric is not None:
                        entity_ids.update(metric.entities)
                elif dependency.kind is SemanticKind.MEASURE:
                    measure = registry.measures.get(dependency.path)
                    if measure is not None:
                        entity_ids.add(measure.entity)
                elif dependency.kind in {
                    SemanticKind.DIMENSION,
                    SemanticKind.TIME_DIMENSION,
                }:
                    dimension = registry.dimensions.get(dependency.path)
                    if dimension is not None:
                        entity_ids.add(dimension.entity)
        datasource_ids = {
            registry.entities[entity_id].datasource
            for entity_id in entity_ids
            if entity_id in registry.entities
        }
        if len(datasource_ids) > 1:
            raise RuntimeMetricLoweringError(
                code="metric-graph-source-domain-mismatch",
                message=(
                    "A runtime metric expression forest must resolve to one datasource "
                    "compatibility domain."
                ),
                candidates={"datasources": sorted(datasource_ids)},
            )
    identities: list[MetricIdentity] = []
    for catalog_id, root_id in zip(catalog_root_ids, canonicalized.graph.roots, strict=True):
        if catalog_id is not None:
            identities.append(
                CatalogMetricIdentity(
                    kind="catalog",
                    metric_ref=RefPayloadV1.from_ref(ref_factory.metric(catalog_id)),
                )
            )
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
            sidecar=sidecar,
            metric_ids=builder.metric_dependencies,
            measure_ids=builder.measure_dependencies,
            dimension_ids=builder.dimension_dependencies,
        ),
        identities=tuple(identities),
        presentation=canonicalized.presentation,
        root_dependency_refs=tuple(
            tuple(sorted(dependencies, key=lambda ref: (ref.kind.value, ref.path)))
            for dependencies in builder.root_dependencies
        ),
    )


__all__ = ["RuntimeMetricLoweringError", "lower_metric_inputs"]
