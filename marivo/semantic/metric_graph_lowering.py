"""Catalog metric lowering into the shared bounded expression graph."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import NoReturn, cast

from marivo.refs import Ref, RefPayloadV1, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    AggregateFoldInput,
    CumulativeComposition,
    LinearComposition,
    RatioComposition,
    SemiAdditive,
    TimeFoldIR,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CanonicalField,
    CanonicalSliceEntryV1,
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
    WeightedMeanAggregateNodeV1,
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
    root_dependency_refs: tuple[tuple[RefPayloadV1, ...], ...] = ()


def _fail(*, kind: str, metric_id: str, path: str, message: str) -> NoReturn:
    raise MetricGraphLoweringError(
        kind=kind,
        metric_id=metric_id,
        path=path,
        message=message,
    )


def _freeze(value: object) -> CanonicalValue:
    if type(value) is RefPayloadV1:
        return value
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


def _composition_value(composition: object) -> object:
    if isinstance(composition, RatioComposition):
        return (
            ("kind", composition.kind),
            ("numerator_ref", _ref_payload("metric", composition.numerator)),
            ("denominator_ref", _ref_payload("metric", composition.denominator)),
        )
    if isinstance(composition, CumulativeComposition):
        return (
            ("kind", composition.kind),
            ("base_ref", _ref_payload("metric", composition.base)),
            (
                "time_dimension_ref",
                _ref_payload("time_dimension", composition.over)
                if composition.over is not None
                else None,
            ),
            ("anchor", composition.anchor),
        )
    if isinstance(composition, LinearComposition):
        return (
            ("kind", composition.kind),
            (
                "terms",
                tuple(
                    (
                        ("sign", term.sign),
                        ("metric_ref", _ref_payload("metric", term.metric)),
                    )
                    for term in composition.terms
                ),
            ),
        )
    return None


def _additivity_value(additivity: object) -> object:
    if isinstance(additivity, SemiAdditive):
        return (
            ("kind", "semi_additive"),
            ("time_dimension_ref", _ref_payload("time_dimension", additivity.over)),
            ("fold", additivity.fold),
        )
    return additivity


def _time_fold_value(fold: TimeFoldIR | None) -> AggregateFoldInput:
    if fold is None:
        return None
    if fold.kind == "percentile":
        if fold.q is None:
            raise AssertionError("percentile TimeFoldIR requires q")
        return ("percentile", fold.q)
    return fold.kind


def _ref_payload(kind: str, path: str) -> RefPayloadV1:
    factories = {
        "domain": ref_factory.domain,
        "datasource": ref_factory.datasource,
        "entity": ref_factory.entity,
        "dimension": ref_factory.dimension,
        "time_dimension": ref_factory.time_dimension,
        "measure": ref_factory.measure,
        "metric": ref_factory.metric,
        "relationship": ref_factory.relationship,
    }
    factory = factories.get(kind)
    if factory is None:
        raise AssertionError(f"unsupported dependency kind: {kind}")
    ref = factory(path)
    return RefPayloadV1.from_ref(ref)


def _dimension_payload(
    registry: Registry,
    path: str,
    *,
    entity_path: str | None = None,
) -> RefPayloadV1:
    dimension = registry.dimensions.get(path)
    if dimension is None and entity_path is not None and "." not in path:
        path = f"{entity_path}.{path}"
        dimension = registry.dimensions.get(path)
    if dimension is None:
        raise ValueError(f"slice dimension {path!r} is not loaded")
    kind = "time_dimension" if dimension.is_time_dimension else "dimension"
    return _ref_payload(kind, path)


def _entry_for(
    registry: Registry,
    semantic_kind: str,
    semantic_id: str,
    *,
    sidecar: CompiledExpressionSidecar | None,
) -> SemanticDependencyEntryV1:
    ref_payload = _ref_payload(semantic_kind, semantic_id)
    body = None
    if sidecar is not None:
        factory = {
            SemanticKind.DOMAIN: ref_factory.domain,
            SemanticKind.DATASOURCE: ref_factory.datasource,
            SemanticKind.ENTITY: ref_factory.entity,
            SemanticKind.DIMENSION: ref_factory.dimension,
            SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
            SemanticKind.MEASURE: ref_factory.measure,
            SemanticKind.METRIC: ref_factory.metric,
            SemanticKind.RELATIONSHIP: ref_factory.relationship,
        }[ref_payload.kind]
        body = sidecar.bodies.get(factory(ref_payload.path))
    bindings = body.bindings if body is not None else ()
    if semantic_kind == "metric":
        metric = registry.metrics[semantic_id]
        return SemanticDependencyEntryV1(
            ref=ref_payload,
            body_digest=metric.body_ast_hash,
            bindings=bindings,
            fields=_fields(
                domain_ref=_ref_payload("domain", metric.domain),
                metric_type=metric.metric_type,
                entity_refs=tuple(_ref_payload("entity", path) for path in metric.entities),
                aggregation=metric.aggregation,
                measure_ref=(
                    _ref_payload("measure", metric.measure) if metric.measure is not None else None
                ),
                composition=_composition_value(metric.composition),
                additivity=_additivity_value(metric.additivity),
                root_entity_ref=(
                    _ref_payload("entity", metric.root_entity)
                    if metric.root_entity is not None
                    else None
                ),
                fanout_policy=metric.fanout_policy,
                aggregation_target_kind=metric.aggregation_target_kind,
                aggregation_target_ref=(
                    _ref_payload(metric.aggregation_target_kind, metric.aggregation_target)
                    if metric.aggregation_target is not None
                    and metric.aggregation_target_kind is not None
                    else None
                ),
                fold_override=metric.fold_override,
                filter=metric.filter,
                weighted_mean=(
                    (
                        ("kind", metric.weighted_mean.kind),
                        ("value_ref", _ref_payload("measure", metric.weighted_mean.value)),
                        ("weight_ref", _ref_payload("measure", metric.weighted_mean.weight)),
                    )
                    if metric.weighted_mean is not None
                    else None
                ),
                unit_override=metric.unit_override,
            ),
        )
    if semantic_kind == "measure":
        measure = registry.measures[semantic_id]
        return SemanticDependencyEntryV1(
            ref=ref_payload,
            body_digest=measure.body_ast_hash,
            bindings=bindings,
            fields=_fields(
                entity_ref=_ref_payload("entity", measure.entity),
                additivity=_additivity_value(measure.additivity),
                unit=measure.unit,
            ),
        )
    if semantic_kind in {"dimension", "time_dimension"}:
        dimension = registry.dimensions[semantic_id]
        return SemanticDependencyEntryV1(
            ref=ref_payload,
            body_digest=dimension.body_ast_hash,
            bindings=bindings,
            fields=_fields(
                entity_ref=_ref_payload("entity", dimension.entity),
                kind=dimension.kind,
                granularity=dimension.granularity,
                parse=dimension.parse,
                is_default=dimension.is_default,
            ),
        )
    if semantic_kind == "entity":
        entity = registry.entities[semantic_id]
        return SemanticDependencyEntryV1(
            ref=_ref_payload("entity", semantic_id),
            body_digest=None,
            fields=_fields(
                datasource_ref=_ref_payload("datasource", entity.datasource),
                source=entity.source.to_dict(),
                primary_key=entity.primary_key,
                versioning=entity.versioning,
            ),
        )
    if semantic_kind == "datasource":
        datasource = registry.datasources[semantic_id]
        return SemanticDependencyEntryV1(
            ref=_ref_payload("datasource", semantic_id),
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
            ref=_ref_payload("relationship", semantic_id),
            body_digest=None,
            fields=_fields(
                from_entity_ref=_ref_payload("entity", relationship.from_entity),
                to_entity_ref=_ref_payload("entity", relationship.to_entity),
                keys=relationship.keys,
            ),
        )
    raise AssertionError(f"unsupported dependency kind: {semantic_kind}")


class _DependencyCollector:
    def __init__(
        self, registry: Registry, sidecar: CompiledExpressionSidecar | None = None
    ) -> None:
        self.registry = registry
        self.sidecar = sidecar
        self._keys: set[tuple[str, str]] = set()
        self._active_metrics: set[str] = set()

    def _add(self, kind: str, semantic_id: str) -> None:
        self._keys.add((kind, semantic_id))

    def _collect_expression_bindings(self, ref: Ref[SemanticKindTag]) -> None:
        if self.sidecar is None:
            return
        body = self.sidecar.bodies.get(ref)
        if body is None:
            return
        for binding in body.bindings:
            self.collect_ref(cast("Ref[SemanticKindTag]", binding.to_ref()))

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
        if metric.weighted_mean is not None:
            self.collect_measure(metric.weighted_mean.value)
            self.collect_measure(metric.weighted_mean.weight)
        if isinstance(metric.additivity, SemiAdditive):
            self.collect_dimension(metric.additivity.over)
        composition = metric.composition
        if isinstance(composition, RatioComposition):
            self.collect_metric(composition.numerator)
            self.collect_metric(composition.denominator)
        elif isinstance(composition, LinearComposition):
            for term in composition.terms:
                self.collect_metric(term.metric)
        elif isinstance(composition, CumulativeComposition):
            self.collect_metric(composition.base)
            if composition.over is not None:
                self.collect_dimension(composition.over)
        self._collect_expression_bindings(
            cast("Ref[SemanticKindTag]", ref_factory.metric(metric_id))
        )
        self._active_metrics.remove(metric_id)

    def collect_measure(self, measure_id: str) -> None:
        measure = self.registry.measures.get(measure_id)
        if measure is None:
            raise KeyError(measure_id)
        if ("measure", measure_id) in self._keys:
            return
        self._add("measure", measure_id)
        self.collect_entity(measure.entity)
        if isinstance(measure.additivity, SemiAdditive):
            self.collect_dimension(measure.additivity.over)
        self._collect_expression_bindings(
            cast("Ref[SemanticKindTag]", ref_factory.measure(measure_id))
        )

    def collect_dimension(self, dimension_id: str) -> None:
        dimension = self.registry.dimensions.get(dimension_id)
        if dimension is None:
            raise KeyError(dimension_id)
        kind = "time_dimension" if dimension.is_time_dimension else "dimension"
        if (kind, dimension_id) in self._keys:
            return
        self._add(kind, dimension_id)
        self.collect_entity(dimension.entity)
        field_ref = (
            ref_factory.time_dimension(dimension_id)
            if dimension.is_time_dimension
            else ref_factory.dimension(dimension_id)
        )
        self._collect_expression_bindings(cast("Ref[SemanticKindTag]", field_ref))

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

    def collect_ref(self, ref: Ref[SemanticKindTag]) -> None:
        """Collect one exact semantic target and its executable dependency closure."""
        if type(ref) is not Ref:
            raise TypeError("semantic dependency targets must be exact Ref values")
        if ref.kind is SemanticKind.METRIC:
            self.collect_metric(ref.path)
            return
        if ref.kind is SemanticKind.MEASURE:
            self.collect_measure(ref.path)
            return
        if ref.kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            self.collect_dimension(ref.path)
            return
        if ref.kind is SemanticKind.ENTITY:
            self.collect_entity(ref.path)
            return
        if ref.kind is SemanticKind.RELATIONSHIP:
            relationship = self.registry.relationships.get(ref.path)
            if relationship is None:
                raise KeyError(ref.path)
            self._add("relationship", ref.path)
            self.collect_entity(relationship.from_entity)
            self.collect_entity(relationship.to_entity)
            for key in relationship.keys:
                for dimension_id in key.to_tuple():
                    self.collect_dimension(dimension_id)
            return
        if ref.kind is SemanticKind.DATASOURCE:
            if ref.path not in self.registry.datasources:
                raise KeyError(ref.path)
            self._add("datasource", ref.path)
            return
        if ref.kind is SemanticKind.DOMAIN:
            if ref.path not in self.registry.domains:
                raise KeyError(ref.path)
            self._add("domain", ref.path)
            return
        raise AssertionError(f"unsupported semantic dependency target: {ref.kind}")

    def entries(self) -> tuple[SemanticDependencyEntryV1, ...]:
        entity_ids = {semantic_id for kind, semantic_id in self._keys if kind == "entity"}
        for relationship in self.registry.relationships.values():
            if relationship.from_entity in entity_ids and relationship.to_entity in entity_ids:
                self._add("relationship", relationship.semantic_id)
        return tuple(
            _entry_for(
                self.registry,
                kind,
                semantic_id,
                sidecar=self.sidecar,
            )
            for kind, semantic_id in sorted(self._keys)
        )


def dependency_digest(
    registry: Registry,
    *,
    sidecar: CompiledExpressionSidecar | None = None,
    metric_ids: Iterable[str] = (),
    measure_ids: Iterable[str] = (),
    dimension_ids: Iterable[str] = (),
    semantic_refs: Iterable[Ref[SemanticKindTag]] = (),
) -> SemanticDependencyDigestV1:
    """Build one canonical dependency digest for resolved semantic targets."""
    collector = _DependencyCollector(registry, sidecar)
    for metric_id in metric_ids:
        collector.collect_metric(metric_id)
    for measure_id in measure_ids:
        collector.collect_measure(measure_id)
    for dimension_id in dimension_ids:
        collector.collect_dimension(dimension_id)
    for ref in semantic_refs:
        collector.collect_ref(ref)
    entries = collector.entries()
    return SemanticDependencyDigestV1(
        schema="marivo.semantic_dependency_digest/v1",
        entries=entries,
        digest=f"sha256:{fingerprint(entries)}",
    )


def _dependency_fingerprint(
    registry: Registry,
    *,
    sidecar: CompiledExpressionSidecar | None = None,
    metric_id: str | None = None,
    target: tuple[str, str] | None = None,
) -> str:
    collector = _DependencyCollector(registry, sidecar)
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


def dependency_fingerprint_for_target(
    registry: Registry,
    *,
    kind: str,
    semantic_id: str,
    sidecar: CompiledExpressionSidecar | None = None,
) -> str:
    """Return the dependency fingerprint for one measure/entity/dimension target."""
    return _dependency_fingerprint(
        registry,
        sidecar=sidecar,
        target=(kind, semantic_id),
    )


class _CatalogGraphBuilder:
    def __init__(
        self, registry: Registry, sidecar: CompiledExpressionSidecar | None = None
    ) -> None:
        self.registry = registry
        self.sidecar = sidecar
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
            if metric.weighted_mean is not None:
                value_id = metric.weighted_mean.value
                weight_id = metric.weighted_mean.weight
                value_measure = self.registry.measures[value_id]
                node = WeightedMeanAggregateNodeV1(
                    kind="weighted_mean",
                    value_ref=_ref_payload("measure", value_id),
                    weight_ref=_ref_payload("measure", weight_id),
                    value_dependency_fingerprint=_dependency_fingerprint(
                        self.registry,
                        sidecar=self.sidecar,
                        target=("measure", value_id),
                    ),
                    weight_dependency_fingerprint=_dependency_fingerprint(
                        self.registry,
                        sidecar=self.sidecar,
                        target=("measure", weight_id),
                    ),
                    filter=tuple(
                        CanonicalSliceEntryV1(
                            dimension_ref=_dimension_payload(
                                self.registry,
                                dimension_id,
                                entity_path=value_measure.entity,
                            ),
                            value=_freeze(value),
                        )
                        for dimension_id, value in (metric.filter or ())
                    ),
                    unit_override=metric.unit_override,
                )
            elif metric.aggregation is None:
                node = CatalogBodyLeafV1(
                    kind="catalog_body_leaf",
                    metric_ref=RefPayloadV1.from_ref(ref_factory.metric(metric_id)),
                    dependency_fingerprint=_dependency_fingerprint(
                        self.registry, sidecar=self.sidecar, metric_id=metric_id
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
                    target_ref=_ref_payload(target_kind, target_id),
                    dependency_fingerprint=_dependency_fingerprint(
                        self.registry,
                        sidecar=self.sidecar,
                        target=(target_kind, target_id),
                    ),
                    agg=metric.aggregation,
                    fold=_time_fold_value(metric.fold_override),
                    filter=tuple(
                        CanonicalSliceEntryV1(
                            dimension_ref=_dimension_payload(
                                self.registry,
                                dimension_id,
                                entity_path=(
                                    self.registry.measures[target_id].entity
                                    if target_kind == "measure"
                                    else target_id
                                ),
                            ),
                            value=_freeze(value),
                        )
                        for dimension_id, value in (metric.filter or ())
                    ),
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
                    sidecar=self.sidecar,
                    target=("time_dimension", composition.over),
                )
                if composition.over is not None
                else fingerprint(())
            )
            node = CumulativeNodeV1(
                kind="cumulative",
                child_id=base_id,
                time_dimension_ref=(
                    _dimension_payload(self.registry, composition.over)
                    if composition.over is not None
                    else None
                ),
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
    *,
    sidecar: CompiledExpressionSidecar | None = None,
) -> MetricExpressionForestV1:
    """Lower one ordered, non-empty catalog metric forest and enforce v1 budgets."""
    roots = tuple(metric_ids)
    if not roots:
        raise ValueError("catalog metric lowering requires at least one root")
    builder = _CatalogGraphBuilder(registry, sidecar)
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
        dependency_digest=dependency_digest(registry, sidecar=sidecar, metric_ids=roots),
        identities=tuple(
            CatalogMetricIdentity(
                kind="catalog",
                metric_ref=RefPayloadV1.from_ref(ref_factory.metric(metric_id)),
            )
            for metric_id in roots
        ),
        presentation=canonicalized.presentation,
    )


def lower_catalog_metric(
    registry: Registry,
    metric_id: str,
    *,
    sidecar: CompiledExpressionSidecar | None = None,
) -> MetricExpressionForestV1:
    """Lower one catalog metric root through the shared forest implementation."""
    return lower_catalog_metrics(registry, (metric_id,), sidecar=sidecar)


__all__ = [
    "MetricExpressionForestV1",
    "MetricGraphLoweringError",
    "dependency_digest",
    "dependency_fingerprint_for_target",
    "lower_catalog_metric",
    "lower_catalog_metrics",
]
