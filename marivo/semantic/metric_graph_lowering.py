"""Catalog metric lowering into the shared bounded expression graph."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import NoReturn

from marivo.semantic.ir import (
    AggregateFoldInput,
    CumulativeComposition,
    LinearComposition,
    RatioComposition,
    SemiAdditive,
    TimeFoldIR,
    WeightedAverageComposition,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CanonicalField,
    CanonicalValue,
    CatalogBodyLeafV1,
    CatalogMetricIdentity,
    CumulativeNodeV1,
    ExpressionOccurrenceV1,
    ExpressionPresentationV1,
    LinearNodeV1,
    LinearTermV1,
    MetricExpressionGraphV1,
    MetricGraphNodeV1,
    MetricIdentity,
    RatioNodeV1,
    SemanticDependencyDigestV1,
    SemanticDependencyEntryV1,
    WeightedAverageNodeV1,
)
from marivo.semantic.metric_graph_canonical import (
    canonicalize_slices,
    fingerprint,
    intern_nodes,
    node_fingerprint,
)
from marivo.semantic.validator import Registry


class MetricGraphLoweringError(ValueError):
    """Raised when a catalog metric cannot lower into the closed v1 graph."""

    def __init__(self, *, kind: str, metric_id: str, path: str, message: str) -> None:
        self.kind = kind
        self.metric_id = metric_id
        self.path = path
        super().__init__(message)


@dataclass(frozen=True)
class MetricExpressionForestV1:
    """One ordered expression forest plus its resolved dependency contract."""

    graph: MetricExpressionGraphV1
    dependency_digest: SemanticDependencyDigestV1
    identities: tuple[MetricIdentity, ...]
    presentation: ExpressionPresentationV1


def _fail(*, kind: str, metric_id: str, path: str, message: str) -> NoReturn:
    raise MetricGraphLoweringError(
        kind=kind,
        metric_id=metric_id,
        path=path,
        message=message,
    )


def _freeze(value: object) -> CanonicalValue:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Enum):
        return _freeze(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return tuple((field.name, _freeze(getattr(value, field.name))) for field in fields(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("semantic dependency mappings require string keys")
        return tuple((key, _freeze(value[key])) for key in sorted(value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(_freeze(item) for item in value)
    raise TypeError(f"unsupported semantic dependency value: {type(value).__name__}")


def _fields(**values: object) -> tuple[CanonicalField, ...]:
    return tuple((name, _freeze(value)) for name, value in values.items())


def _time_fold_value(fold: TimeFoldIR | None) -> AggregateFoldInput:
    if fold is None:
        return None
    if fold.kind == "percentile":
        if fold.q is None:
            raise AssertionError("percentile TimeFoldIR requires q")
        return ("percentile", fold.q)
    return fold.kind


def _entry_for(
    registry: Registry, semantic_kind: str, semantic_id: str
) -> SemanticDependencyEntryV1:
    if semantic_kind == "metric":
        metric = registry.metrics[semantic_id]
        return SemanticDependencyEntryV1(
            semantic_kind="metric",
            semantic_id=semantic_id,
            body_digest=metric.body_ast_hash,
            fields=_fields(
                domain=metric.domain,
                metric_type=metric.metric_type,
                entities=metric.entities,
                aggregation=metric.aggregation,
                measure=metric.measure,
                composition=metric.composition,
                additivity=metric.additivity,
                root_entity=metric.root_entity,
                fanout_policy=metric.fanout_policy,
                aggregation_target=metric.aggregation_target,
                aggregation_target_kind=metric.aggregation_target_kind,
                fold_override=metric.fold_override,
                filter=metric.filter,
                unit_override=metric.unit_override,
            ),
        )
    if semantic_kind == "measure":
        measure = registry.measures[semantic_id]
        return SemanticDependencyEntryV1(
            semantic_kind="measure",
            semantic_id=semantic_id,
            body_digest=measure.body_ast_hash,
            fields=_fields(
                entity=measure.entity,
                additivity=measure.additivity,
                unit=measure.unit,
            ),
        )
    if semantic_kind in {"dimension", "time_dimension"}:
        dimension = registry.dimensions[semantic_id]
        return SemanticDependencyEntryV1(
            semantic_kind=semantic_kind,
            semantic_id=semantic_id,
            body_digest=dimension.body_ast_hash,
            fields=_fields(
                entity=dimension.entity,
                kind=dimension.kind,
                granularity=dimension.granularity,
                parse=dimension.parse,
                is_default=dimension.is_default,
            ),
        )
    if semantic_kind == "entity":
        entity = registry.entities[semantic_id]
        return SemanticDependencyEntryV1(
            semantic_kind="entity",
            semantic_id=semantic_id,
            body_digest=None,
            fields=_fields(
                datasource=entity.datasource,
                source=entity.source.to_dict(),
                primary_key=entity.primary_key,
                versioning=entity.versioning,
            ),
        )
    if semantic_kind == "datasource":
        datasource = registry.datasources[semantic_id]
        return SemanticDependencyEntryV1(
            semantic_kind="datasource",
            semantic_id=semantic_id,
            body_digest=None,
            fields=_fields(
                backend_type=datasource.backend_type,
                fields=datasource.fields,
                env_refs=datasource.env_refs,
            ),
        )
    if semantic_kind == "relationship":
        relationship = registry.relationships[semantic_id]
        return SemanticDependencyEntryV1(
            semantic_kind="relationship",
            semantic_id=semantic_id,
            body_digest=None,
            fields=_fields(
                from_entity=relationship.from_entity,
                to_entity=relationship.to_entity,
                keys=relationship.keys,
            ),
        )
    raise AssertionError(f"unsupported dependency kind: {semantic_kind}")


class _DependencyCollector:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self._keys: set[tuple[str, str]] = set()
        self._active_metrics: set[str] = set()

    def _add(self, kind: str, semantic_id: str) -> None:
        self._keys.add((kind, semantic_id))

    def collect_metric(self, metric_id: str) -> None:
        metric = self.registry.metrics.get(metric_id)
        if metric is None:
            raise KeyError(metric_id)
        if metric_id in self._active_metrics:
            return
        self._active_metrics.add(metric_id)
        self._add("metric", metric_id)
        for entity_id in metric.entities:
            self.collect_entity(entity_id)
        if metric.root_entity is not None:
            self.collect_entity(metric.root_entity)
        target_id = metric.aggregation_target or metric.measure
        target_kind = metric.aggregation_target_kind or (
            "measure" if metric.measure is not None else None
        )
        if target_id is not None and target_kind == "measure":
            self.collect_measure(target_id)
        elif target_id is not None and target_kind == "entity":
            self.collect_entity(target_id)
        if isinstance(metric.additivity, SemiAdditive):
            self.collect_dimension(metric.additivity.over)
        composition = metric.composition
        if isinstance(composition, RatioComposition):
            self.collect_metric(composition.numerator)
            self.collect_metric(composition.denominator)
        elif isinstance(composition, WeightedAverageComposition):
            self.collect_metric(composition.value)
            self.collect_metric(composition.weight)
        elif isinstance(composition, LinearComposition):
            for term in composition.terms:
                self.collect_metric(term.metric)
        elif isinstance(composition, CumulativeComposition):
            self.collect_metric(composition.base)
            if composition.over is not None:
                self.collect_dimension(composition.over)
        self._active_metrics.remove(metric_id)

    def collect_measure(self, measure_id: str) -> None:
        measure = self.registry.measures.get(measure_id)
        if measure is None:
            raise KeyError(measure_id)
        self._add("measure", measure_id)
        self.collect_entity(measure.entity)
        if isinstance(measure.additivity, SemiAdditive):
            self.collect_dimension(measure.additivity.over)

    def collect_dimension(self, dimension_id: str) -> None:
        dimension = self.registry.dimensions.get(dimension_id)
        if dimension is None:
            raise KeyError(dimension_id)
        kind = "time_dimension" if dimension.is_time_dimension else "dimension"
        self._add(kind, dimension_id)
        self.collect_entity(dimension.entity)

    def collect_entity(self, entity_id: str) -> None:
        entity = self.registry.entities.get(entity_id)
        if entity is None:
            raise KeyError(entity_id)
        if ("entity", entity_id) in self._keys:
            return
        self._add("entity", entity_id)
        if entity.datasource in self.registry.datasources:
            self._add("datasource", entity.datasource)
        versioning = entity.versioning
        for field_name in ("valid_from", "valid_to"):
            dimension_id = getattr(versioning, field_name, None)
            if isinstance(dimension_id, str) and dimension_id in self.registry.dimensions:
                self.collect_dimension(dimension_id)

    def entries(self) -> tuple[SemanticDependencyEntryV1, ...]:
        entity_ids = {semantic_id for kind, semantic_id in self._keys if kind == "entity"}
        for relationship in self.registry.relationships.values():
            if relationship.from_entity in entity_ids and relationship.to_entity in entity_ids:
                self._add("relationship", relationship.semantic_id)
        return tuple(
            _entry_for(self.registry, kind, semantic_id) for kind, semantic_id in sorted(self._keys)
        )


def dependency_digest(
    registry: Registry,
    *,
    metric_ids: Iterable[str] = (),
    measure_ids: Iterable[str] = (),
    dimension_ids: Iterable[str] = (),
) -> SemanticDependencyDigestV1:
    """Build one canonical dependency digest for resolved semantic targets."""
    collector = _DependencyCollector(registry)
    for metric_id in metric_ids:
        collector.collect_metric(metric_id)
    for measure_id in measure_ids:
        collector.collect_measure(measure_id)
    for dimension_id in dimension_ids:
        collector.collect_dimension(dimension_id)
    entries = collector.entries()
    return SemanticDependencyDigestV1(
        schema="semantic-dependency/v1",
        entries=entries,
        fingerprint=fingerprint(entries),
    )


def _dependency_fingerprint(
    registry: Registry, *, metric_id: str | None = None, target: tuple[str, str] | None = None
) -> str:
    collector = _DependencyCollector(registry)
    if metric_id is not None:
        collector.collect_metric(metric_id)
    if target is not None:
        kind, semantic_id = target
        if kind == "measure":
            collector.collect_measure(semantic_id)
        elif kind == "entity":
            collector.collect_entity(semantic_id)
        elif kind in {"dimension", "time_dimension"}:
            collector.collect_dimension(semantic_id)
        else:
            raise AssertionError(f"unsupported dependency target: {kind}")
    return fingerprint(collector.entries())


def dependency_fingerprint_for_target(registry: Registry, *, kind: str, semantic_id: str) -> str:
    """Return the dependency fingerprint for one measure/entity/dimension target."""
    return _dependency_fingerprint(registry, target=(kind, semantic_id))


class _CatalogGraphBuilder:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.nodes: dict[str, MetricGraphNodeV1] = {}

    def _intern(self, node: MetricGraphNodeV1) -> str:
        node_id = node_fingerprint(node)
        self.nodes.setdefault(node_id, node)
        return node_id

    def lower_metric(
        self,
        metric_id: str,
        *,
        path: str,
        active: tuple[str, ...],
    ) -> tuple[str, tuple[ExpressionOccurrenceV1, ...]]:
        metric = self.registry.metrics.get(metric_id)
        if metric is None:
            _fail(
                kind="unknown_metric_dependency",
                metric_id=metric_id,
                path=path,
                message=f"metric graph dependency {metric_id!r} is not loaded at {path}",
            )
        if metric_id in active:
            cycle = " -> ".join((*active, metric_id))
            _fail(
                kind="metric_graph_cycle",
                metric_id=metric_id,
                path=path,
                message=f"metric graph cycle detected at {path}: {cycle}",
            )
        next_active = (*active, metric_id)
        node: MetricGraphNodeV1

        if metric.metric_type == "simple":
            if metric.aggregation is None:
                node = CatalogBodyLeafV1(
                    kind="catalog_body_leaf",
                    metric_id=metric_id,
                    dependency_fingerprint=_dependency_fingerprint(
                        self.registry, metric_id=metric_id
                    ),
                    unit_override=metric.unit_override,
                )
            else:
                target_id = metric.aggregation_target or metric.measure
                target_kind = metric.aggregation_target_kind or (
                    "measure" if metric.measure is not None else None
                )
                if target_id is None or target_kind not in {"measure", "entity"}:
                    _fail(
                        kind="invalid_aggregate_target",
                        metric_id=metric_id,
                        path=path,
                        message=f"aggregate metric {metric_id!r} has no valid target at {path}",
                    )
                node = AggregateNodeV1(
                    kind="aggregate",
                    target_id=target_id,
                    target_kind=target_kind,
                    dependency_fingerprint=_dependency_fingerprint(
                        self.registry,
                        target=(target_kind, target_id),
                    ),
                    agg=metric.aggregation,
                    fold=_time_fold_value(metric.fold_override),
                    filter=tuple(metric.filter or ()),
                    unit_override=metric.unit_override,
                )
            node_id = self._intern(node)
            return node_id, (ExpressionOccurrenceV1(path=path, node_id=node_id),)

        composition = metric.composition
        if composition is None:
            _fail(
                kind="missing_composition",
                metric_id=metric_id,
                path=path,
                message=f"derived metric {metric_id!r} has no composition at {path}",
            )
        child_paths: tuple[str, ...]
        if isinstance(composition, RatioComposition):
            numerator_path = f"{path}.numerator"
            denominator_path = f"{path}.denominator"
            numerator_id, numerator_occurrences = self.lower_metric(
                composition.numerator,
                path=numerator_path,
                active=next_active,
            )
            denominator_id, denominator_occurrences = self.lower_metric(
                composition.denominator,
                path=denominator_path,
                active=next_active,
            )
            node = RatioNodeV1(
                kind="ratio",
                numerator_id=numerator_id,
                denominator_id=denominator_id,
                zero_division="null",
                unit_override=metric.unit_override,
            )
            child_paths = (numerator_path, denominator_path)
            children = (*numerator_occurrences, *denominator_occurrences)
        elif isinstance(composition, WeightedAverageComposition):
            value_path = f"{path}.value"
            weight_path = f"{path}.weight"
            value_id, value_occurrences = self.lower_metric(
                composition.value,
                path=value_path,
                active=next_active,
            )
            weight_id, weight_occurrences = self.lower_metric(
                composition.weight,
                path=weight_path,
                active=next_active,
            )
            node = WeightedAverageNodeV1(
                kind="weighted_average",
                value_id=value_id,
                weight_id=weight_id,
                unit_override=metric.unit_override,
            )
            child_paths = (value_path, weight_path)
            children = (*value_occurrences, *weight_occurrences)
        elif isinstance(composition, LinearComposition):
            child_paths = tuple(f"{path}.term[{index}]" for index in range(len(composition.terms)))
            terms: list[LinearTermV1] = []
            child_occurrences: list[ExpressionOccurrenceV1] = []
            for term, child_path in zip(composition.terms, child_paths, strict=True):
                child_id, occurrences = self.lower_metric(
                    term.metric,
                    path=child_path,
                    active=next_active,
                )
                terms.append(
                    LinearTermV1(
                        child_id=child_id,
                        coefficient=1.0 if term.sign == "+" else -1.0,
                    )
                )
                child_occurrences.extend(occurrences)
            node = LinearNodeV1(
                kind="linear",
                terms=tuple(terms),
                unit_override=metric.unit_override,
            )
            children = tuple(child_occurrences)
        elif isinstance(composition, CumulativeComposition):
            base_path = f"{path}.base"
            base_id, base_occurrences = self.lower_metric(
                composition.base,
                path=base_path,
                active=next_active,
            )
            dimension_fingerprint = (
                _dependency_fingerprint(
                    self.registry,
                    target=("time_dimension", composition.over),
                )
                if composition.over is not None
                else fingerprint(())
            )
            node = CumulativeNodeV1(
                kind="cumulative",
                child_id=base_id,
                over=composition.over,
                anchor=composition.anchor,
                dependency_fingerprint=dimension_fingerprint,
                unit_override=metric.unit_override,
            )
            child_paths = (base_path,)
            children = base_occurrences
        else:
            _fail(
                kind="unsupported_catalog_metric_node",
                metric_id=metric_id,
                path=path,
                message=(
                    f"metric {metric_id!r} uses unsupported composition "
                    f"{type(composition).__name__} at {path}"
                ),
            )
        node_id = self._intern(node)
        root_occurrence = ExpressionOccurrenceV1(
            path=path,
            node_id=node_id,
            child_paths=child_paths,
        )
        return node_id, (root_occurrence, *children)


def lower_catalog_metrics(
    registry: Registry,
    metric_ids: Iterable[str],
) -> MetricExpressionForestV1:
    """Lower one ordered, non-empty catalog metric forest and enforce v1 budgets."""
    roots = tuple(metric_ids)
    if not roots:
        raise ValueError("catalog metric lowering requires at least one root")
    builder = _CatalogGraphBuilder(registry)
    root_ids: list[str] = []
    occurrences: list[ExpressionOccurrenceV1] = []
    for index, metric_id in enumerate(roots):
        node_id, root_occurrences = builder.lower_metric(
            metric_id,
            path=f"root[{index}]",
            active=(),
        )
        root_ids.append(node_id)
        occurrences.extend(root_occurrences)
    graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=tuple(root_ids),
        nodes=intern_nodes(builder.nodes.values()),
        occurrences=tuple(occurrences),
    )
    canonicalized = canonicalize_slices(
        graph,
        ExpressionPresentationV1(schema="metric-presentation/v1", labels=()),
    )
    return MetricExpressionForestV1(
        graph=canonicalized.graph,
        dependency_digest=dependency_digest(registry, metric_ids=roots),
        identities=tuple(
            CatalogMetricIdentity(kind="catalog", metric_id=metric_id) for metric_id in roots
        ),
        presentation=canonicalized.presentation,
    )


def lower_catalog_metric(registry: Registry, metric_id: str) -> MetricExpressionForestV1:
    """Lower one catalog metric root through the shared forest implementation."""
    return lower_catalog_metrics(registry, (metric_id,))


__all__ = [
    "MetricExpressionForestV1",
    "MetricGraphLoweringError",
    "dependency_digest",
    "dependency_fingerprint_for_target",
    "lower_catalog_metric",
    "lower_catalog_metrics",
]
