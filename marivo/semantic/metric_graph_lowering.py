"""Catalog metric lowering into the shared bounded expression graph."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import NoReturn, cast

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
from ibis.common.exceptions import IbisTypeError

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldPredicate,
    MeasureAssertion,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import ExactRef
from marivo._temporal import Grain as TemporalGrain
from marivo.refs import (
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
    _create_ref,
)
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
)
from marivo.semantic._metric_resolution import (
    fold_ir_to_input,
    resolve_metric_temporal_contract,
)
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    RatioComposition,
    SemiAdditive,
    WhereValue,
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
from marivo.semantic.validator import (
    Registry,
)


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

    @property
    def bound_graph_fingerprint(self) -> str:
        """Bind a reusable value DAG to its effective Semantic definitions."""
        return fingerprint(("metric-bound-graph/v1", self.graph, self.dependency_digest.digest))


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
    if isinstance(value, Ref):
        return RefPayloadV1.from_ref(value)
    if isinstance(value, TemporalGrain):
        if value.kind == "builtin":
            return value.to_token()
        assert value.calendar is not None and value.level is not None
        return (
            "semantic_grain",
            RefPayloadV1.from_ref(value.calendar),
            value.level,
        )
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
        fields = (
            ("kind", composition.kind),
            ("numerator_ref", _ref_payload("metric", composition.numerator)),
            ("denominator_ref", _ref_payload("metric", composition.denominator)),
        )
        return (
            (*fields, ("zero_denominator_policy", composition.zero_denominator_policy))
            if composition.zero_denominator_policy is not None
            else fields
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


def _ref_payload(kind: str, path: str) -> RefPayloadV1:
    supported = {
        "domain",
        "datasource",
        "entity",
        "dimension",
        "time_dimension",
        "measure",
        "metric",
        "relationship",
        "work_schedule",
    }
    if kind not in supported:
        raise AssertionError(f"unsupported dependency kind: {kind}")
    return RefPayloadV1.from_ref(_create_ref(SemanticKind(kind), path))


def _dimension_payload(
    registry: Registry,
    path: str,
    *,
    metric_id: str,
    occurrence_path: str,
    entity_path: str | None = None,
    role: str = "metric filter",
) -> RefPayloadV1:
    dimension = registry.dimensions.get(path)
    if dimension is None and entity_path is not None and "." not in path:
        path = f"{entity_path}.{path}"
        dimension = registry.dimensions.get(path)
    if dimension is None:
        _fail(
            kind=(
                "invalid_filter_dimension" if role == "metric filter" else "dimension_not_loaded"
            ),
            metric_id=metric_id,
            path=occurrence_path,
            message=(
                f"{role} dimension {path!r} is not loaded"
                + (f" on entity {entity_path!r}" if entity_path is not None else "")
            ),
        )
    kind = "time_dimension" if dimension.is_time_dimension else "dimension"
    return _ref_payload(kind, path)


def _filter_value(value: WhereValue) -> CanonicalValue:
    """Encode one authored equality or membership value in the shared slice algebra."""
    if isinstance(value, tuple):
        return (("op", "in"), ("value", tuple(value)))
    return value


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
        body = sidecar.bodies.get(_create_ref(ref_payload.kind, ref_payload.path))
    bindings = body.bindings if body is not None else ()
    if semantic_kind == "metric":
        metric = registry.metrics[semantic_id]
        temporal_contract = resolve_metric_temporal_contract(metric, registry)
        return SemanticDependencyEntryV1(
            ref=ref_payload,
            body_digest=metric.body_ast_hash,
            bindings=bindings,
            fields=tuple(
                field
                for field in _fields(
                    domain_ref=_ref_payload("domain", metric.domain),
                    metric_type=metric.metric_type,
                    entity_refs=tuple(_ref_payload("entity", path) for path in metric.entities),
                    aggregation=metric.aggregation,
                    measure_ref=(
                        _ref_payload("measure", metric.measure)
                        if metric.measure is not None
                        else None
                    ),
                    composition=_composition_value(metric.composition),
                    additivity=_additivity_value(metric.additivity),
                    dsl_additivity=metric.dsl_additivity,
                    event_time_dimension=(
                        _ref_payload("time_dimension", metric.event_time_dimension)
                        if metric.event_time_dimension is not None
                        else None
                    ),
                    event_time_path=tuple(
                        _ref_payload("relationship", path) for path in metric.event_time_path
                    ),
                    status_time_dimension=(
                        _ref_payload("time_dimension", metric.status_time_dimension)
                        if metric.status_time_dimension is not None
                        else None
                    ),
                    status_time_fold=metric.status_time_fold,
                    null_policy=metric.null_policy,
                    empty_policy=metric.empty_policy,
                    zero_denominator_policy=metric.zero_denominator_policy,
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
                    temporal_contract=temporal_contract,
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
                )
                if metric.event_time_path or field[0] != "event_time_path"
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
                dsl_additivity=measure.dsl_additivity,
                status_time_dimension=(
                    _ref_payload("time_dimension", measure.status_time_dimension)
                    if measure.status_time_dimension is not None
                    else None
                ),
                status_time_fold=measure.status_time_fold,
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
            body_digest=body.body_ast_hash if body is not None else None,
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
    if semantic_kind == "work_schedule":
        schedule = registry.work_schedules[semantic_id]
        return SemanticDependencyEntryV1(
            ref=_ref_payload("work_schedule", semantic_id),
            body_digest=None,
            fields=_fields(
                domain_ref=_ref_payload("domain", schedule.domain),
                boundary_timezone=schedule.boundary_timezone,
                coverage=schedule.coverage,
                date_ref=_ref_payload("time_dimension", schedule.date),
                is_working_ref=_ref_payload("dimension", schedule.is_working),
                ai_context=schedule.ai_context,
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
        if metric.event_time_dimension is not None:
            self.collect_dimension(metric.event_time_dimension)
        for relationship_id in metric.event_time_path:
            self.collect_ref(_create_ref(SemanticKind.RELATIONSHIP, relationship_id))
        if metric.status_time_dimension is not None:
            self.collect_dimension(metric.status_time_dimension)
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
        self._collect_expression_bindings(_create_ref(SemanticKind.METRIC, metric_id))
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
        if measure.status_time_dimension is not None:
            self.collect_dimension(measure.status_time_dimension)
        self._collect_expression_bindings(_create_ref(SemanticKind.MEASURE, measure_id))

    def collect_dimension(self, dimension_id: str) -> None:
        dimension = self.registry.dimensions.get(dimension_id)
        if dimension is None:
            raise KeyError(dimension_id)
        kind = "time_dimension" if dimension.is_time_dimension else "dimension"
        if (kind, dimension_id) in self._keys:
            return
        self._add(kind, dimension_id)
        self.collect_entity(dimension.entity)
        self._collect_expression_bindings(_create_ref(SemanticKind(kind), dimension_id))

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
        if ref.kind is SemanticKind.WORK_SCHEDULE:
            schedule = self.registry.work_schedules.get(ref.path)
            if schedule is None:
                raise KeyError(ref.path)
            self._add("work_schedule", ref.path)
            self.collect_dimension(schedule.date)
            self.collect_dimension(schedule.is_working)
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
                                metric_id=metric_id,
                                occurrence_path=f"{path}.filter[{dimension_id}]",
                                entity_path=value_measure.entity,
                            ),
                            value=_filter_value(value),
                        )
                        for dimension_id, value in (metric.filter or ())
                    ),
                    unit_override=metric.unit_override,
                )
            elif metric.aggregation is None:
                node = CatalogBodyLeafV1(
                    kind="catalog_body_leaf",
                    metric_ref=_ref_payload("metric", metric_id),
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
                temporal_contract = resolve_metric_temporal_contract(metric, self.registry)
                if metric.fold_override is not None and temporal_contract is None:
                    _fail(
                        kind="time_fold_requires_semi_additive",
                        metric_id=metric_id,
                        path=path,
                        message=(
                            f"aggregate metric {metric_id!r} declares a fold override but its "
                            "measure is not semi-additive"
                        ),
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
                    fold=fold_ir_to_input(
                        temporal_contract.fold if temporal_contract is not None else None
                    ),
                    filter=tuple(
                        CanonicalSliceEntryV1(
                            dimension_ref=_dimension_payload(
                                self.registry,
                                dimension_id,
                                metric_id=metric_id,
                                occurrence_path=f"{path}.filter[{dimension_id}]",
                                entity_path=(
                                    self.registry.measures[target_id].entity
                                    if target_kind == "measure"
                                    else target_id
                                ),
                            ),
                            value=_filter_value(value),
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
                zero_division=(
                    composition.zero_denominator_policy.kind
                    if composition.zero_denominator_policy is not None
                    else "null"
                ),
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
                    _dimension_payload(
                        self.registry,
                        composition.over,
                        metric_id=metric_id,
                        occurrence_path=f"{path}.over",
                        role="cumulative time",
                    )
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
                metric_ref=_ref_payload("metric", metric_id),
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


# Nullable Row/Metric lowering for the definition DSL. These inputs contain no
# analysis Signature, Session, Population program or persisted state.


@dataclass(frozen=True, slots=True, eq=False)
class NumericCheck:
    """A temporary boolean violation expression, consumed before publication."""

    violation: ir.BooleanValue
    expected: str
    received: str


@dataclass(frozen=True, slots=True, eq=False)
class NumericValue:
    value: ir.Value
    checks: tuple[NumericCheck, ...] = ()


def numeric_failure(expected: str, received: str) -> NoReturn:
    from marivo.semantic.errors import ErrorKind, SemanticExpressionError

    action = "Repair the input values to satisfy the expected numeric/type constraints, then retry."
    if "nonzero denominator" in expected:
        action = "Use a nonzero denominator, or explicitly select zero_denominator='null' on this operator."
    elif "non-NA" in expected:
        action = "Supply non-NA inputs, or explicitly select nulls='ignore' on this reducer."
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        message="The numeric operation cannot produce an admitted nullable value.",
        expected=expected,
        received=received,
        action=action,
    )


def _finite_check(value: ir.Value, operation: str) -> tuple[NumericCheck, ...]:
    if not value.type().is_floating():
        return ()
    return (
        NumericCheck(
            value.notnull() & (value.isnan() | value.isinf()), "finite numeric values", operation
        ),
    )


def numeric_binary(
    operation: str, left: NumericValue, right: NumericValue, *, zero_denominator: str = "null"
) -> NumericValue:
    """Emit arithmetic with the denominator masked before division."""
    a, b = left.value, right.value
    if a.type().is_floating() and b.type().is_decimal():
        b = b.cast("float64")
    elif b.type().is_floating() and a.type().is_decimal():
        a = a.cast("float64")
    checks = (*left.checks, *right.checks)
    if operation == "divide":
        zero = b == 0
        if zero_denominator == "error":
            checks = (
                *checks,
                NumericCheck(zero, "a nonzero denominator", "divide(zero_denominator='error')"),
            )
        safe = zero.ifelse(ibis.null().cast(b.type()), b)
        value = a / safe
    elif operation == "add":
        value = add_numeric(a, b)
    elif operation == "subtract":
        value = add_numeric(a, -b)
    elif operation == "multiply":
        value = multiply_numeric(a, b)
    else:
        numeric_failure("add/subtract/multiply/divide", operation)
    if (
        operation in ("add", "subtract", "multiply")
        and a.type().is_integer()
        and b.type().is_integer()
    ):
        wide_a, wide_b = a.cast("decimal(38,0)"), b.cast("decimal(38,0)")
        wide = (
            wide_a + wide_b
            if operation == "add"
            else wide_a - wide_b
            if operation == "subtract"
            else wide_a * wide_b
        )
        overflow = (wide < -(2**63)) | (wide > 2**63 - 1)
        checks = (
            *checks,
            NumericCheck(overflow, "signed int64 arithmetic", f"{operation}: integer overflow"),
        )
        value = overflow.ifelse(ibis.null().cast("int64"), wide.cast("int64"))
    return NumericValue(value, (*checks, *_finite_check(value, operation)))


def lower_row(node: RowNode, fields: Mapping[str, ir.Value]) -> NumericValue:
    """Lower one closed Row expression over already prepared physical fields."""
    if isinstance(node, RowExpression):
        from marivo.semantic._numeric_recipe import lower_recipe

        return lower_recipe(node.recipe, tuple(lower_row(item, fields) for item in node.inputs))
    if isinstance(node, RowField):
        from marivo._definition.types import field_read_key

        value = fields.get(
            field_read_key(node.read) if node.read is not None else node.ref.ref.path
        )
        if value is None:
            numeric_failure(
                f"a prepared physical field for {node.ref.ref.path}", "unbound field read"
            )
        if node.ref.ref.kind is SemanticKind.MEASURE and not value.type().is_numeric():
            numeric_failure(f"a numeric physical type for {node.ref.ref.path}", str(value.type()))
        if value.type().is_integer() and value.type().nbytes < 8:
            value = value.cast("int64")
        elif value.type().is_floating() and value.type().nbytes < 8:
            value = value.cast("float64")
        return NumericValue(value, _finite_check(value, node.ref.ref.path))
    if isinstance(node, MeasureAssertion):
        return lower_row(node.operand, fields)
    numeric_failure("an admitted native row recipe", type(node).__name__)


def lower_field_predicate(node: FieldPredicate, fields: Mapping[str, ir.Value]) -> NumericValue:
    """Lower a native boolean recipe with its demand-aware numeric checks."""
    if not isinstance(node, RowExpression):
        numeric_failure("an admitted native row predicate", type(node).__name__)
    return lower_row(node, fields)


def _source_sum(value: ir.Value) -> ir.Value:
    return value.sum()


def lower_source_reduction(
    node: SourceAggregate | SourceWeightedMean | EntityCount,
    table: ir.Table,
    fields: Mapping[str, ir.Value],
) -> NumericValue:
    """Reduce one contribution. Checks returned here are scalar aggregates."""
    if isinstance(node, EntityCount):
        return NumericValue(table.count())
    item = lower_row(node.value if isinstance(node, SourceWeightedMean) else node.operand, fields)
    value = item.value
    checks = list(item.checks)
    if isinstance(node, SourceWeightedMean):
        weight = lower_row(node.weight, fields)
        checks.extend(weight.checks)
        pair = value.notnull() & weight.value.notnull()
        if node.nulls == "error":
            checks.append(
                NumericCheck(~pair, "non-NA value/weight pairs", "weighted_mean(nulls='error')")
            )
        product = numeric_binary("multiply", NumericValue(value), NumericValue(weight.value))
        checks.extend(product.checks)
        numerator = _source_sum(pair.ifelse(product.value, ibis.null().cast(product.value.type())))
        denominator = _source_sum(pair.ifelse(weight.value, ibis.null().cast(weight.value.type())))
        numerator = numerator.fill_null(ibis.literal(0).cast(numerator.type()))
        denominator = denominator.fill_null(ibis.literal(0).cast(denominator.type()))
        divided = numeric_binary(
            "divide",
            NumericValue(numerator, _finite_check(numerator, "weighted numerator sum")),
            NumericValue(denominator, _finite_check(denominator, "weight sum")),
            zero_denominator=node.zero_denominator,
        )
        return NumericValue(
            divided.value, (*(_aggregate_check(c) for c in checks), *divided.checks)
        )
    if node.nulls == "error":
        checks.append(
            NumericCheck(value.isnull(), "non-NA source values", f"{node.reducer}(nulls='error')")
        )
    if node.reducer == "count":
        result = value.count()
    elif node.reducer == "count_distinct":
        result = value.nunique()
    elif node.reducer == "sum":
        result = _source_sum(value)
        aggregate_checks: tuple[NumericCheck, ...] = ()
        if value.type().is_integer():
            wide = value.cast("decimal(38,0)").sum()
            overflow = (wide < -(2**63)) | (wide > 2**63 - 1)
            aggregate_checks = (
                NumericCheck(overflow, "signed int64 sum", "sum: integer overflow"),
            )
            result = overflow.ifelse(ibis.null().cast("int64"), wide.cast("int64"))
        if node.empty == "zero":
            result = result.fill_null(ibis.literal(0).cast(result.type()))
        else:
            result = (value.count() == 0).ifelse(ibis.null().cast(result.type()), result)
        return NumericValue(
            result,
            (
                *(_aggregate_check(c) for c in checks),
                *aggregate_checks,
                *_finite_check(result, node.reducer),
            ),
        )
    elif node.reducer == "approx_nunique":
        result = value.approx_nunique()
    elif node.reducer == "approx_median":
        result = value.approx_median()
    elif node.reducer == "approx_quantile":
        numeric_failure(
            "a qualified approximate quantile route",
            "Ibis 12 has no approximate quantile operation",
        )
    elif node.reducer == "mean":
        result = value.mean()
    elif node.reducer == "min":
        result = value.min()
    elif node.reducer == "max":
        result = value.max()
    elif node.reducer == "median":
        result = value.median()
    else:
        assert node.q is not None
        result = value.quantile(node.q)
    if node.reducer not in ("count", "count_distinct", "approx_nunique"):
        result = (value.count() == 0).ifelse(ibis.null().cast(result.type()), result)
    return NumericValue(
        result, (*(_aggregate_check(c) for c in checks), *_finite_check(result, node.reducer))
    )


def _aggregate_check(check: NumericCheck) -> NumericCheck:
    return NumericCheck(check.violation.any().fill_null(False), check.expected, check.received)


def lower_metric_formula(
    node: MetricNode,
    occurrence: tuple[int, ...],
    values: Mapping[tuple[int, ...], NumericValue],
    declared: Mapping[ExactRef, MetricNode],
) -> NumericValue:
    """Compose completed reductions only; never join contribution rows."""
    if occurrence in values:
        return values[occurrence]
    from marivo._definition.expressions import MemberCount

    if isinstance(node, MemberCount):
        # Type preparation only; the local member input supplies completed counts.
        return NumericValue(ibis.literal(0).cast("int64"))
    if isinstance(node, MetricExpression):
        from marivo.semantic._numeric_recipe import lower_recipe

        return lower_recipe(
            node.recipe,
            tuple(
                lower_metric_formula(item, (*occurrence, i), values, declared)
                for i, item in enumerate(node.operands)
            ),
        )
    if isinstance(node, DeclaredMetricRef):
        return lower_metric_formula(declared[node.ref], (*occurrence, 0), values, declared)
    if isinstance(node, (ContributionFilter, MetricWindow)):
        return lower_metric_formula(node.operand, (*occurrence, 0), values, declared)
    if isinstance(node, PerReduce):
        if node.reducer == "approx_nunique":
            numeric_failure(
                "a qualified approximate internal distinct route",
                "no qualified local approximate distinct kernel",
            )
        item = lower_metric_formula(node.operand, (*occurrence, 0), values, declared)
        # Only type preparation reaches an unmaterialized Per node. Local sample
        # execution supplies its completed slot before numerical composition.
        value = ibis.table({"value": item.value.type()}, name="_internal_sample_types").value
        if node.reducer in ("count", "count_distinct", "approx_nunique"):
            return NumericValue(value.count())
        if node.reducer in ("mean", "median", "quantile", "approx_median", "approx_quantile"):
            return NumericValue(
                value.mean()
                if node.reducer == "mean"
                else value.quantile(node.q if node.q is not None else 0.5)
            )
        return NumericValue(value.sum() if node.reducer == "sum" else value.min())
    if isinstance(node, PerWeightedMean):
        value = lower_metric_formula(node.value, (*occurrence, 0), values, declared)
        weight = lower_metric_formula(node.weight, (*occurrence, 1), values, declared)
        return numeric_binary(
            "divide",
            numeric_binary("multiply", value, weight),
            weight,
            zero_denominator=node.zero_denominator,
        )
    numeric_failure(
        "ordinary source reductions and Metric arithmetic",
        f"{type(node).__name__}; Population/sample preparation is delivered in R3",
    )


def multiply_numeric(left: ir.Value, right: ir.Value) -> ir.Value:
    """Use native multiplication with necessary Decimal/float and scale adaptation."""
    a, b = left.type(), right.type()
    if a.is_floating() and isinstance(b, dt.Decimal):
        right = right.cast(a)
    elif b.is_floating() and isinstance(a, dt.Decimal):
        left = left.cast(b)
    try:
        return left * right
    except IbisTypeError:
        a, b = left.type(), right.type()
        if not isinstance(a, dt.Decimal) or not isinstance(b, dt.Decimal):
            raise
        scale = max(a.scale or 0, b.scale or 0)
        precision = (
            max((a.precision or 38) - (a.scale or 0), (b.precision or 38) - (b.scale or 0)) + scale
        )
        common = dt.Decimal(min(38, precision), scale)
        return left.cast(common) * right.cast(common)


def add_numeric(left: ir.Value, right: ir.Value) -> ir.Value:
    """Add numeric components with the minimal Ibis Decimal adaptation."""
    a, b = left.type(), right.type()
    if a.is_floating() and isinstance(b, dt.Decimal):
        right = right.cast(a)
    elif b.is_floating() and isinstance(a, dt.Decimal):
        left = left.cast(b)
    try:
        return left + right
    except IbisTypeError:
        if not isinstance(a, dt.Decimal) or not isinstance(b, dt.Decimal):
            raise
        scale = max(a.scale or 0, b.scale or 0)
        precision = (
            max((a.precision or 38) - (a.scale or 0), (b.precision or 38) - (b.scale or 0)) + scale
        )
        common = dt.Decimal(min(38, precision), scale)
        return left.cast(common) + right.cast(common)
