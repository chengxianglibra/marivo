"""Catalog metric lowering into the shared bounded expression graph."""

from __future__ import annotations

import decimal
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from typing import Literal, NoReturn, cast

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._temporal import Grain as TemporalGrain
from marivo.refs import (
    EntityKind,
    MetricKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    SemanticKindTag,
    _create_ref,
    _decode_ref_payload,
)
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    ExpressionBody,
    evaluate_expression_body,
)
from marivo.semantic._metric_resolution import (
    fold_ir_to_input,
    resolve_metric_temporal_contract,
)
from marivo.semantic.decimal_precision import DecimalType, add_sub, multiply
from marivo.semantic.errors import SemanticLoadError, repair
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    MeasureIR,
    RatioComposition,
    SemiAdditive,
    TargetEntityContract,
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
    SliceNodeV1,
    TargetMetricComponent,
    TargetMetricContract,
    TargetMetricCumulative,
    WeightedMeanAggregateNodeV1,
    component_node,
    component_predicate,
    node_child_ids,
)
from marivo.semantic.metric_graph_canonical import (
    canonicalize_slices,
    fingerprint,
    intern_nodes,
    node_fingerprint,
)
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.unit_algebra import linear_unit, linear_units_conflict, ratio_unit
from marivo.semantic.validator import (
    Registry,
    normalize_target_dimension,
    normalize_target_entity,
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
        "event",
        "state_model",
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
    if semantic_kind == "event":
        event = registry.events[semantic_id]
        return SemanticDependencyEntryV1(
            ref=_ref_payload("event", semantic_id),
            body_digest=event.body_ast_hash,
            bindings=bindings,
            fields=_fields(
                source_entity_ref=_ref_payload("entity", event.source_entity),
                identity_refs=tuple(_ref_payload("dimension", path) for path in event.identity),
                occurred_at_ref=_ref_payload("time_dimension", event.occurred_at),
                participants=event.participants,
                predicate_kind=event.predicate_kind,
            ),
        )
    if semantic_kind == "state_model":
        model = registry.state_models[semantic_id]
        return SemanticDependencyEntryV1(
            ref=_ref_payload("state_model", semantic_id),
            body_digest=None,
            fields=_fields(
                subject_ref=_ref_payload("entity", model.subject),
                states=model.states,
                inceptions=model.inceptions,
                transitions=model.transitions,
                ai_context=model.ai_context,
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
        if ref.kind is SemanticKind.EVENT:
            event = self.registry.events.get(ref.path)
            if event is None:
                raise KeyError(ref.path)
            self._add("event", ref.path)
            self.collect_entity(event.source_entity)
            self.collect_dimension(event.occurred_at)
            for identity_ref in event.identity:
                self.collect_dimension(identity_ref)
            for participant in event.participants:
                for relationship_id in participant.path or ():
                    self.collect_ref(_create_ref(SemanticKind.RELATIONSHIP, relationship_id))
            self._collect_expression_bindings(ref)
            return
        if ref.kind is SemanticKind.STATE_MODEL:
            model = self.registry.state_models.get(ref.path)
            if model is None:
                raise KeyError(ref.path)
            self._add("state_model", ref.path)
            self.collect_entity(model.subject)
            for inception in model.inceptions:
                self.collect_ref(_create_ref(SemanticKind.EVENT, inception.trigger.event_ref))
            for transition in model.transitions:
                self.collect_ref(_create_ref(SemanticKind.EVENT, transition.trigger.event_ref))
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


def _target_metric_error(
    metric_id: str,
    expected: str,
    received: str,
    *,
    action: str = "Use typed governed aggregate, weighted-mean, ratio, linear, slice, or cumulative builders with exact component and temporal contracts.",
) -> NoReturn:
    raise SemanticLoadError(
        kind="invalid_target_metric",
        message="The Metric cannot supply a private lazy computation contract.",
        refs=(metric_id,),
        expected=expected,
        received=received,
        hint=action,
        repair=repair(kind="reauthor", canonical_id="metric", action=action),
    )


def _validate_component_slice(
    registry: Registry, condition: CanonicalSliceEntryV1, metric_id: str
) -> None:
    """Validate literal types against declared Dimension facts without source work."""
    op, value = component_predicate(condition.value)
    dimension = normalize_target_dimension(registry, condition.dimension_ref.path)
    dtype = dt.dtype(dimension.logical_type)
    values = value if isinstance(value, tuple) else (value,)
    for scalar in values:
        compatible = (
            (scalar is None and op in ("==", "!=", "in"))
            or (dtype.is_string() and type(scalar) is str)
            or (dtype.is_boolean() and type(scalar) is bool)
            or (dtype.is_integer() and type(scalar) is int)
            or ((dtype.is_floating() or dtype.is_decimal()) and type(scalar) in (int, float))
            or (dtype.is_temporal() and type(scalar) is str)
        )
        if compatible and scalar is not None:
            try:
                dt.normalize(dtype, scalar)
            except (TypeError, ValueError, OverflowError):
                compatible = False
        if not compatible:
            _target_metric_error(
                metric_id,
                f"slice {op!r} literals compatible with {dimension.ref.path} ({dtype})",
                f"incompatible {type(scalar).__name__} literal",
                action=(
                    f"Replace the slice value for {dimension.ref.path} with a {dtype} literal "
                    "or a sequence of that type for in/between. Use valid temporal strings "
                    "for temporal Dimensions and non-null bounds for between."
                ),
            )


# Smallest decimal that holds every int64 value; used to promote integer
# operands of row arithmetic so the decimal rule table stays authoritative.
_INTEGER_PROMOTION = DecimalType(19, 0)


def _fail_undeclared_column(metric_id: str, measure_path: str, column: str) -> NoReturn:
    _measure_step_error(
        metric_id,
        measure_path,
        "columns declared on the owning entity source",
        f"the body references undeclared column {column!r}",
        action=(
            f"Declare column {column!r} on the entity source schema, or reference only "
            "columns the entity already declares."
        ),
    )


def _measure_step_error(
    metric_id: str,
    measure_path: str,
    expected: str,
    received: str,
    *,
    action: str,
) -> NoReturn:
    raise SemanticLoadError(
        kind="invalid_target_metric",
        message=f"Computed measure {measure_path!r} cannot supply a private lazy "
        "computation contract.",
        refs=(metric_id, measure_path),
        expected=expected,
        received=received,
        hint=action,
        repair=repair(kind="reauthor", canonical_id="metric", action=action),
    )


def _decimal_literal_type(value: object) -> DecimalType | None:
    """Resolve the decimal derivation type of one ibis literal operand."""
    if type(value) is bool:
        return None
    if type(value) is int:
        return DecimalType(1, 0)
    if type(value) is float:
        return None
    if isinstance(value, decimal.Decimal):
        digits, exponent = value.as_tuple()[1:]
        if isinstance(exponent, str):
            return None
        scale = -exponent
        if scale < 0:
            return DecimalType(max(len(digits), -scale), 0)
        return DecimalType(max(len(digits), scale + 1), scale)
    return None


def _derive_measure_result_type(op: ops.Node) -> str:
    """Derive one row expression's result type from the declared field facts.

    Decimal results follow the decimal precision rules; any other result type
    is reported as its ibis-inferred type string. Owner facts in reported
    errors stay empty outside an active measure normalization.
    """
    return _derive_expression_type(op, {}, _EMPTY_MEASURE_FACTS)


def _derive_expression_type(
    op: ops.Node,
    decimal_fields: Mapping[ops.Node, DecimalType],
    facts: _MeasureFacts,
) -> str:
    """Walk the op tree once, deriving decimal results by rule and rejecting the rest."""
    if isinstance(op, ops.Field):
        field_type = decimal_fields.get(op)
        if field_type is not None:
            return str(field_type)
        return str(op.to_expr().type())
    if isinstance(op, ops.Literal):
        literal_type = _decimal_literal_type(op.value)
        if literal_type is not None:
            return str(literal_type)
        return str(op.to_expr().type())
    if isinstance(op, ops.Cast):
        arg_type = _derive_expression_type(op.arg, decimal_fields, facts)
        target = op.to
        if not isinstance(target, dt.Decimal):
            # Non-decimal cast targets keep their declared ibis type.
            return str(target)
        if target.precision is None:
            _fail_unresolved_step(op, arg_type, facts)
        scale = target.scale or 0
        try:
            derived = DecimalType(target.precision, scale)
        except ValueError as exc:
            # Out-of-bound cast targets (over 38 digits, scale above
            # precision) are an authoring rejection, never a crash, and the
            # same rejection for decimal and integer source operands.
            _measure_step_error(
                facts.metric_id,
                facts.measure_path,
                "a cast target within the 38-digit decimal bound",
                f"cast target {target} over {arg_type}: {exc}",
                action=(
                    "Declare the cast with an explicit resolved target of at most 38 "
                    "digits and scale not exceeding precision, for example decimal(38,4)."
                ),
            )
        return str(derived)
    if isinstance(op, ops.Negate):
        return _derive_expression_type(op.arg, decimal_fields, facts)
    if isinstance(op, ops.Add | ops.Subtract | ops.Multiply):
        left = _derive_expression_type(op.left, decimal_fields, facts)
        right = _derive_expression_type(op.right, decimal_fields, facts)
        left_decimal = dt.dtype(left)
        right_decimal = dt.dtype(right)
        if not (left_decimal.is_decimal() or right_decimal.is_decimal()):
            # No decimal operand: integer arithmetic keeps the ibis-inferred
            # integer result; floating or other operands stay unresolvable.
            if left_decimal.is_integer() and right_decimal.is_integer():
                return str(op.to_expr().type())
            _fail_unresolved_step(
                op,
                f"{left} {type(op).__name__.lower()} {right}",
                facts,
            )
        if not (left_decimal.is_decimal() or left_decimal.is_integer()) or not (
            right_decimal.is_decimal() or right_decimal.is_integer()
        ):
            _fail_unresolved_step(
                op,
                f"{left} {type(op).__name__.lower()} {right}",
                facts,
            )
        # A decimal operand without resolved precision and scale (for example
        # a bare "decimal" declared column) cannot enter the rule table.
        for operand_dtype, operand_text in ((left_decimal, left), (right_decimal, right)):
            if operand_dtype.is_decimal() and (
                operand_dtype.precision is None or operand_dtype.scale is None
            ):
                _measure_step_error(
                    facts.metric_id,
                    facts.measure_path,
                    "a decimal operand with resolved precision and scale",
                    f"decimal operand {operand_text} on "
                    f"{type(op).__name__.lower()} with unresolved precision or scale",
                    action=(
                        "Declare the source column with explicit precision and scale, for "
                        "example decimal(12,2), so the decimal rule table can derive the "
                        "step's result type."
                    ),
                )
        # The rule table covers decimal operands. Integer operands promote to
        # the smallest decimal that holds every int64 value, matching engine
        # promotion (for example DuckDB: dec(12,2) * int64 -> DECIMAL(31,2)).
        left_type = (
            DecimalType(left_decimal.precision, left_decimal.scale)
            if left_decimal.is_decimal()
            else _INTEGER_PROMOTION
        )
        right_type = (
            DecimalType(right_decimal.precision, right_decimal.scale)
            if right_decimal.is_decimal()
            else _INTEGER_PROMOTION
        )
        result = (
            add_sub(left_type, right_type)
            if isinstance(op, ops.Add | ops.Subtract)
            else multiply(left_type, right_type)
        )
        if result is None:
            _measure_step_error(
                facts.metric_id,
                facts.measure_path,
                (
                    f"a decimal {type(op).__name__.lower()} whose derived precision stays "
                    "within 38 digits"
                ),
                f"decimal step {left} {type(op).__name__.lower()} {right} exceeds 38 digits",
                action=(
                    "Narrow the measure's declared decimal types, or split the row expression "
                    "so each step stays within the 38-digit bound. Derivation overflow is "
                    "rejected before publication; it is never silently truncated."
                ),
            )
        return str(result)
    if isinstance(op, ops.Reduction):
        _measure_step_error(
            facts.metric_id,
            facts.measure_path,
            "row-level arithmetic over declared columns of exactly one entity",
            "a cross-row aggregation inside the measure body",
            action=(
                "Return a row expression from the measure body. Move the aggregation into a "
                "Metric that aggregates this measure (for example ms.aggregate(..., agg='sum'))."
            ),
        )
    if isinstance(op, ops.WindowFunction):
        _measure_step_error(
            facts.metric_id,
            facts.measure_path,
            "row-level arithmetic over declared columns of exactly one entity",
            "a window function inside the measure body",
            action=(
                "Remove the window call from the measure body. Window semantics belong to "
                "analysis operators, not to row-level measure expressions."
            ),
        )
    _fail_unresolved_step(op, None, facts)
    raise AssertionError("unreachable")


def _fail_unresolved_step(op: ops.Node, received: str | None, facts: _MeasureFacts) -> NoReturn:
    detail = type(op).__name__
    received_text = f"{detail} over {received}" if received is not None else detail
    _measure_step_error(
        facts.metric_id,
        facts.measure_path,
        "row-level arithmetic, explicit cast, conditional, or null handling over the "
        "declared columns of exactly one entity",
        f"a {received_text} step whose result type cannot be derived",
        action=(
            "Restrict the measure body to +, -, *, explicit casts over declared numeric "
            "columns of one entity, and keep every operand on the same resolved decimal "
            "or integer types."
        ),
    )


def _target_measure_type(
    registry: Registry,
    path: str,
    sidecar: CompiledExpressionSidecar | None,
    *,
    metric_id: str,
) -> tuple[str, str | None, RefPayloadV1]:
    measure = registry.measures.get(path)
    body = (
        sidecar.bodies.get(_create_ref(SemanticKind.MEASURE, path)) if sidecar is not None else None
    )
    if measure is None or body is None:
        _target_metric_error(
            metric_id,
            "a loaded measure with declared expression type facts",
            "missing declared measure facts",
        )
    declared_sidecar = sidecar
    if declared_sidecar is None:
        _target_metric_error(
            metric_id,
            "a compiled measure expression sidecar",
            "missing expression sidecar",
        )
    entity = normalize_target_entity(registry, measure.entity)
    if body.source_column is not None:
        data_type = dict(entity.columns).get(body.source_column)
        if data_type is None:
            _target_metric_error(
                metric_id,
                "a declared source type for the measure column",
                "missing source-column type",
            )
        return data_type, measure.unit, entity.ref
    return _computed_measure_type(path, declared_sidecar, measure, body, entity, metric_id)


@dataclass(frozen=True)
class _MeasureFacts:
    """Owner facts reported by derivation failures for the active measure."""

    metric_id: str
    measure_path: str


_EMPTY_MEASURE_FACTS = _MeasureFacts(metric_id="", measure_path="")


def _computed_measure_type(
    path: str,
    sidecar: CompiledExpressionSidecar,
    measure: MeasureIR,
    body: ExpressionBody,
    entity: TargetEntityContract,
    metric_id: str,
) -> tuple[str, str | None, RefPayloadV1]:
    """Derive one computed measure's type facts through its declared row expression."""
    entity_ref = cast("Ref[EntityKind]", _decode_ref_payload(entity.ref))
    columns = dict(entity.columns)
    for body_column in body.source_columns:
        if body_column not in columns:
            _fail_undeclared_column(metric_id, path, body_column)
    # The placeholder deliberately carries every entity-declared column, not
    # only body.source_columns, so bind-captured column references resolve.
    placeholder = ibis.table(columns, name=path)
    measure_ref = _create_ref(SemanticKind.MEASURE, path)
    result = evaluate_expression_body(
        catalog_definition_fingerprint=path,
        expression_sidecar=sidecar,
        owning_ref=measure_ref,
        body=body,
        entity_refs=(entity_ref,),
        aliases=(placeholder,),
    )
    if isinstance(result, ir.Table):
        _measure_step_error(
            metric_id,
            path,
            "one scalar value expression",
            "a table expression",
            action="Return one column-level value expression from the measure body.",
        )
    expression_op = result.op()
    placeholder_op = placeholder.op()
    tables = expression_op.find(ops.PhysicalTable)
    foreign = sorted({table.name for table in tables if table is not placeholder_op})
    if foreign:
        _measure_step_error(
            metric_id,
            path,
            "column references on the single owning entity alias",
            f"references to other ibis tables: {', '.join(foreign)}",
            action=(
                "Reference only the declared columns of the measure's one entity parameter. "
                "Cross-entity dependencies belong in governed Metric compositions, not in a "
                "measure body."
            ),
        )
    facts = _MeasureFacts(metric_id=metric_id, measure_path=path)
    for field_op in expression_op.find(ops.Field):
        if field_op.name not in columns:
            _fail_undeclared_column(metric_id, path, field_op.name)
    decimal_fields = {
        field_op: DecimalType(field_type.precision, field_type.scale)
        for field_op in expression_op.find(ops.Field)
        if isinstance(field_type := dt.dtype(columns.get(field_op.name, "")), dt.Decimal)
        and field_type.precision is not None
        and field_type.scale is not None
    }
    data_type = _derive_expression_type(expression_op, decimal_fields, facts)
    return data_type, measure.unit, entity.ref


def normalize_target_metric(
    registry: Registry,
    metric_id: str,
    *,
    sidecar: CompiledExpressionSidecar | None = None,
) -> TargetMetricContract:
    """Derive private intrinsic state from the existing canonical graph without I/O."""
    if metric_id not in registry.metrics:
        _target_metric_error(metric_id, "a loaded Metric", "Metric not loaded")
    forest = lower_catalog_metrics(registry, (metric_id,), sidecar=sidecar)
    return _normalize_target_graph(
        registry,
        forest,
        metric_id,
        registry.metrics[metric_id].name,
        sidecar,
        _create_ref(SemanticKind.METRIC, metric_id),
    )


def normalize_target_metric_inputs(
    registry: Registry,
    inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
    *,
    sidecar: CompiledExpressionSidecar | None = None,
) -> tuple[TargetMetricContract, ...]:
    """Normalize the complete bounded forest before deriving each root's state."""
    from marivo.semantic.runtime_metric import runtime_metric_leaf_refs
    from marivo.semantic.runtime_metric_lowering import lower_metric_inputs

    for item in inputs:
        if isinstance(item, RuntimeMetricExpr):
            runtime_metric_leaf_refs(item)
        if type(item) is Ref and item.path not in registry.metrics:
            _target_metric_error(item.path, "a loaded Metric", "Metric not loaded")
    forest = lower_metric_inputs(registry, inputs, sidecar=sidecar)
    for presentation in forest.presentation.labels:
        label = presentation.label
        if (
            not label.strip()
            or len(label) > 160
            or any(ord(char) < 32 for char in label)
            or unicodedata.normalize("NFC", label) != label
        ):
            raise ValueError(
                "Runtime Metric labels require non-empty NFC text of at most 160 characters without controls"
            )
    nodes = {item.node_id: item.node for item in forest.graph.nodes}
    result: list[TargetMetricContract] = []
    for index, (expression, root, identity, dependencies) in enumerate(
        zip(inputs, forest.graph.roots, forest.identities, forest.root_dependency_refs, strict=True)
    ):
        reachable: set[str] = set()
        pending = [root]
        while pending:
            node_id = pending.pop()
            if node_id not in reachable:
                reachable.add(node_id)
                pending.extend(node_child_ids(nodes[node_id]))
        prefix = f"root[{index}]"
        root_forest = replace(
            forest,
            graph=replace(
                forest.graph,
                roots=(root,),
                nodes=tuple(item for item in forest.graph.nodes if item.node_id in reachable),
                occurrences=tuple(
                    replace(
                        item,
                        path="root[0]" + item.path.removeprefix(prefix),
                        child_paths=tuple(
                            "root[0]" + path.removeprefix(prefix) for path in item.child_paths
                        ),
                    )
                    for item in forest.graph.occurrences
                    if item.path == prefix or item.path.startswith(prefix + ".")
                ),
            ),
            identities=(identity,),
            dependency_digest=dependency_digest(
                registry,
                sidecar=sidecar,
                semantic_refs=tuple(_create_ref(item.kind, item.path) for item in dependencies),
            ),
        )
        name = (
            expression.label
            if isinstance(expression, RuntimeMetricExpr)
            else registry.metrics[expression.path].name
        )
        key = (
            "runtime_metric:" + root
            if isinstance(expression, RuntimeMetricExpr)
            else expression.path
        )
        result.append(
            _normalize_target_graph(registry, root_forest, key, name, sidecar, expression)
        )
    return tuple(result)


def _normalize_target_graph(
    registry: Registry,
    forest: MetricExpressionForestV1,
    metric_id: str,
    name: str,
    sidecar: CompiledExpressionSidecar | None,
    expression: Ref[SemanticKindTag] | RuntimeMetricExpr,
) -> TargetMetricContract:
    for dependency in forest.dependency_digest.entries:
        if dependency.ref.kind is not SemanticKind.METRIC:
            continue
        definition = registry.metrics[dependency.ref.path]
        if definition.metric_type != "simple":
            continue
        declared_root = definition.root_entity or (
            definition.entities[0] if len(definition.entities) == 1 else None
        )
        if declared_root is None or declared_root not in definition.entities:
            _target_metric_error(
                metric_id,
                "one explicit computation root belonging to the Metric entities",
                "missing or foreign computation root",
            )
        target = definition.aggregation_target or definition.measure
        if definition.weighted_mean is not None:
            target = definition.weighted_mean.value
        if target is not None:
            target_root = (
                target
                if definition.aggregation_target_kind == "entity"
                else registry.measures[target].entity
            )
            if target_root != declared_root:
                _target_metric_error(
                    metric_id,
                    "aggregate inputs on the declared computation root",
                    "aggregate input belongs to another Entity",
                )
    nodes = {record.node_id: record.node for record in forest.graph.nodes}
    components: list[TargetMetricComponent] = []
    cumulative: list[TargetMetricCumulative] = []
    requirements: set[str] = set()
    source_recompute = False
    policies: dict[str, Literal["block", "aggregate_then_join"]] = {}

    def contribution_policies(path: str, role: str) -> None:
        declaration = registry.metrics[path]
        composition = declaration.composition
        if isinstance(composition, RatioComposition):
            contribution_policies(composition.numerator, f"{role}.numerator")
            contribution_policies(composition.denominator, f"{role}.denominator")
        elif isinstance(composition, LinearComposition):
            for index, term in enumerate(composition.terms):
                contribution_policies(term.metric, f"{role}.term[{index}]")
        elif isinstance(composition, CumulativeComposition):
            contribution_policies(composition.base, f"{role}.base")
        else:
            policies[role] = declaration.fanout_policy

    from marivo.semantic.runtime_metric import RuntimeLinearExpr, RuntimeRatioExpr, RuntimeSliceExpr

    def input_policies(value: Ref[SemanticKindTag] | RuntimeMetricExpr, role: str) -> None:
        if type(value) is Ref:
            contribution_policies(value.path, role)
        elif isinstance(value, RuntimeRatioExpr):
            input_policies(value.numerator, f"{role}.numerator")
            input_policies(value.denominator, f"{role}.denominator")
        elif isinstance(value, RuntimeLinearExpr):
            for index, term in enumerate((*value.add, *value.subtract)):
                input_policies(term, f"{role}.term[{index}]")
        elif isinstance(value, RuntimeSliceExpr):
            input_policies(value.metric, role)

    input_policies(expression, "value")

    def visit(node_id: str, role: str) -> tuple[str, bool, str | None]:
        nonlocal source_recompute
        node = nodes[node_id]
        if isinstance(node, SliceNodeV1):
            node = component_node(forest.graph, node_id)
        if isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
            for condition in node.filter:
                _validate_component_slice(registry, condition, metric_id)
        if isinstance(node, AggregateNodeV1):
            if node.target_ref.kind is SemanticKind.ENTITY:
                if node.agg not in ("count", "count_distinct"):
                    _target_metric_error(
                        metric_id, "count or count_distinct for an Entity target", role
                    )
                target_entity = normalize_target_entity(registry, node.target_ref.path)
                if node.agg == "count_distinct" and not target_entity.identity_signature:
                    _target_metric_error(
                        metric_id, "a declared Entity identity for distinct count", role
                    )
                root = target_entity.ref
                data_type, unit = "int64", None
            else:
                data_type, unit, root = _target_measure_type(
                    registry,
                    node.target_ref.path,
                    sidecar,
                    metric_id=metric_id,
                )
            status_time_dimension = None
            if node.target_ref.kind is SemanticKind.MEASURE:
                additivity = registry.measures[node.target_ref.path].additivity
                if isinstance(additivity, SemiAdditive):
                    status_time_dimension = _ref_payload("time_dimension", additivity.over)
            if node.agg not in ("count", "count_distinct") and not dt.dtype(data_type).is_numeric():
                _target_metric_error(metric_id, "a numeric measure", "non-numeric source type")
            if node.fold is not None:
                if status_time_dimension is None:
                    _target_metric_error(
                        metric_id, "a governed status-time axis for the fold", role
                    )
                source_recompute = True
                requirements.add("metric.source_temporal_fold@v1")
                if isinstance(node.fold, tuple):
                    requirements.add("metric.source_quantile@v1")
            expression = ibis.table({"value": data_type}, name="_target_type_facts").value
            state: tuple[str, ...]
            if node.agg == "sum":
                output_type = str(expression.sum().type())
                state = ("sum", "non_null_count", "row_count")
            elif node.agg == "mean":
                output_type = str(expression.mean().type())
                state = ("sum", "non_null_count", "row_count")
            elif node.agg in ("count", "count_distinct"):
                output_type = "int64"
                state = ("count" if node.agg == "count" else "value", "row_count")
                unit = None
                if node.agg == "count_distinct":
                    source_recompute = True
                    requirements.add("metric.source_distinct@v1")
            elif node.agg in ("min", "max"):
                output_type = data_type
                state = (node.agg, "non_null_count", "row_count")
            else:
                output_type = "float64"
                state = ("value", "non_null_count", "row_count")
                source_recompute = True
                requirements.add("metric.source_quantile@v1")
            component_recompute = node.fold is not None or node.agg not in (
                "sum",
                "count",
                "mean",
                "min",
                "max",
            )
            if node.fold is not None:
                state = ("value", "non_null_count", "row_count")
            components.append(
                TargetMetricComponent(
                    node_id,
                    role,
                    root,
                    state,
                    "ignore_null_inputs",
                    "zero" if node.agg in ("count", "count_distinct") else "null",
                    node.fold,
                    status_time_dimension,
                    component_recompute,
                    policies.get(role, "block"),
                )
            )
            return (
                output_type,
                node.agg not in ("count", "count_distinct"),
                node.unit_override or unit,
            )
        if isinstance(node, WeightedMeanAggregateNodeV1):
            value_type, unit, root = _target_measure_type(
                registry,
                node.value_ref.path,
                sidecar,
                metric_id=metric_id,
            )
            weight_type, _, weight_root = _target_measure_type(
                registry,
                node.weight_ref.path,
                sidecar,
                metric_id=metric_id,
            )
            if root != weight_root or any(
                not dt.dtype(value).is_numeric() for value in (value_type, weight_type)
            ):
                _target_metric_error(
                    metric_id,
                    "numeric value/weight measures on one computation root",
                    "incompatible weighted-mean inputs",
                )
            value_additivity = registry.measures[node.value_ref.path].additivity
            weight_additivity = registry.measures[node.weight_ref.path].additivity
            time_fold = None
            status_time_dimension = None
            if weight_additivity != "additive":
                _target_metric_error(metric_id, "an additive weight measure", f"{role}.weight")
            if isinstance(value_additivity, SemiAdditive):
                time_fold = fold_ir_to_input(value_additivity.fold)
                status_time_dimension = _ref_payload("time_dimension", value_additivity.over)
                source_recompute = True
                requirements.add("metric.source_temporal_fold@v1")
            components.append(
                TargetMetricComponent(
                    node_id,
                    role,
                    root,
                    ("weighted_numerator", "weight_sum", "non_null_pair_count", "row_count")
                    if time_fold is None
                    else ("value", "non_null_pair_count", "row_count"),
                    "non_null_pairs",
                    "null",
                    time_fold,
                    status_time_dimension,
                    time_fold is not None,
                    policies.get(role, "block"),
                )
            )
            typed_pair = ibis.table(
                {"value": value_type, "weight": weight_type}, name="_target_weighted_type_facts"
            )
            output_type = str(
                ((typed_pair.value * typed_pair.weight).sum() / typed_pair.weight.sum()).type()
            )
            return output_type, True, node.unit_override or unit
        if isinstance(node, RatioNodeV1):
            numerator_type, _, numerator_unit = visit(node.numerator_id, f"{role}.numerator")
            denominator_type, _, denominator_unit = visit(
                node.denominator_id, f"{role}.denominator"
            )
            typed_pair = ibis.table(
                {"numerator": numerator_type, "denominator": denominator_type},
                name="_target_ratio_type_facts",
            )
            return (
                str((typed_pair.numerator / typed_pair.denominator).type()),
                True,
                node.unit_override or ratio_unit(numerator_unit, denominator_unit),
            )
        if isinstance(node, SliceNodeV1):
            return visit(node.child_id, f"{role}.slice")
        if isinstance(node, LinearNodeV1):
            terms = tuple(
                visit(term.child_id, f"{role}.term[{index}]")
                for index, term in enumerate(node.terms)
            )
            units = tuple(term[2] for term in terms)
            if linear_units_conflict(units):
                _target_metric_error(metric_id, "commensurable linear component units", role)
            typed = ibis.table(
                {f"term_{index}": term[0] for index, term in enumerate(terms)},
                name="_target_linear_type_facts",
            )
            values = tuple(
                typed[f"term_{index}"] * int(term.coefficient)
                if float(term.coefficient).is_integer()
                else typed[f"term_{index}"] * term.coefficient
                for index, term in enumerate(node.terms)
            )
            expression = values[0]
            for value in values[1:]:
                expression = expression + value
            return (
                str(expression.type()),
                any(term[1] for term in terms),
                node.unit_override or linear_unit(units),
            )
        if isinstance(node, CumulativeNodeV1):
            child = nodes[node.child_id]
            if isinstance(child, SliceNodeV1):
                child = component_node(forest.graph, node.child_id)
            if not (
                isinstance(child, WeightedMeanAggregateNodeV1)
                or (
                    isinstance(child, AggregateNodeV1)
                    and child.agg in ("sum", "count", "count_distinct")
                )
            ):
                _target_metric_error(
                    metric_id, "a governed sum/count/distinct/weighted-mean cumulative base", role
                )
            output_type, nullable, unit = visit(node.child_id, f"{role}.base")
            over = node.time_dimension_ref
            if over is None:
                roots = {
                    item.computation_root.path
                    for item in components
                    if item.role.startswith(f"{role}.base")
                }
                axes = tuple(
                    item
                    for item in registry.dimensions.values()
                    if item.entity in roots and item.is_time_dimension
                )
                if len(axes) != 1:
                    _target_metric_error(metric_id, "one exact cumulative over axis", role)
                over = _ref_payload("time_dimension", axes[0].semantic_id)
            cumulative.append(
                TargetMetricCumulative(node_id, role, node.child_id, over, node.anchor)
            )
            source_recompute = True
            requirements.add("metric.source_cumulative@v1")
            return output_type, nullable, node.unit_override or unit
        _target_metric_error(
            metric_id, "an exact governed contribution graph", f"{role}: {type(node).__name__}"
        )

    output_type, nullable, unit = visit(forest.graph.roots[0], "value")
    root_node = nodes[forest.graph.roots[0]]
    if isinstance(root_node, SliceNodeV1):
        root_node = component_node(forest.graph, forest.graph.roots[0])
    null_rule: Literal["ignore_null_inputs", "non_null_pairs", "null_component_or_zero_denominator"]
    if isinstance(root_node, RatioNodeV1):
        null_rule = "null_component_or_zero_denominator"
    elif isinstance(root_node, WeightedMeanAggregateNodeV1):
        null_rule = "non_null_pairs"
    else:
        null_rule = "ignore_null_inputs"
    return TargetMetricContract(
        identity=forest.identities[0],
        name=name,
        graph=forest.graph,
        dependency_fingerprint=forest.dependency_digest.digest,
        computation_roots=tuple(
            dict.fromkeys(component.computation_root for component in components)
        ),
        components=tuple(components),
        required_state=()
        if source_recompute
        else tuple(
            f"{component.role}.{state}"
            for component in components
            for state in component.required_state
        ),
        logical_type="decimal" if dt.dtype(output_type).is_decimal() else output_type,
        nullable=nullable,
        unit=(registry.metrics[metric_id].unit_override or unit)
        if isinstance(forest.identities[0], CatalogMetricIdentity)
        else unit,
        null_rule=null_rule,
        empty_rule="null" if nullable else "zero",
        cumulative=tuple(cumulative),
        source_requirements=tuple(sorted(requirements)),
        requires_source_recompute=source_recompute,
    )


__all__ = [
    "MetricExpressionForestV1",
    "MetricGraphLoweringError",
    "dependency_digest",
    "dependency_fingerprint_for_target",
    "lower_catalog_metric",
    "lower_catalog_metrics",
]
