"""One declaration projection shared by Catalog details and terminal trees."""

from __future__ import annotations

from marivo.refs import EntityKind, FieldKind, MeasureKind, Ref, ref
from marivo.semantic._metric_resolution import resolve_metric_temporal_contract
from marivo.semantic.definition import (
    DefinitionNode,
    ExpressionDescription,
    _Aggregate,
    _Cumulative,
    _Linear,
    _Ratio,
    _TemporalRule,
    _TemporalRules,
    _UnsupportedExpression,
    _WeightedMean,
)
from marivo.semantic.errors import SemanticDefinitionReadError
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    MetricIR,
    RatioComposition,
    SemiAdditive,
    WhereValue,
)
from marivo.semantic.validator import Registry


def metric_node(
    metric: MetricIR,
    registry: Registry,
    expression: ExpressionDescription | None = None,
    *,
    default_cumulative_axis: bool = False,
) -> DefinitionNode:
    filters: list[tuple[Ref[FieldKind], WhereValue]] = []
    for name, value in metric.filter or ():
        dimension = registry.dimensions.get(f"{metric.root_entity}.{name}")
        if dimension is None:
            raise SemanticDefinitionReadError(
                ref=f"metric:{metric.semantic_id}",
                location=metric.location,
                received="filter dimension absent from the target entity",
            )
        dimension_ref: Ref[FieldKind] = (
            ref.time_dimension(dimension.semantic_id)
            if dimension.is_time_dimension
            else ref.dimension(dimension.semantic_id)
        )
        filters.append((dimension_ref, value))
    composition = metric.composition
    if isinstance(composition, RatioComposition):
        return _Ratio(ref.metric(composition.numerator), ref.metric(composition.denominator))
    if isinstance(composition, LinearComposition):
        return _Linear(tuple((term.sign, ref.metric(term.metric)) for term in composition.terms))
    if isinstance(composition, CumulativeComposition):
        return _Cumulative(
            ref.metric(composition.base),
            ref.time_dimension(composition.over)
            if composition.over is not None and not default_cumulative_axis
            else None,
            composition.anchor,
        )
    if composition is not None or metric.metric_type == "derived":
        raise SemanticDefinitionReadError(
            ref=f"metric:{metric.semantic_id}",
            location=metric.location,
            received="unknown metric composition",
        )
    if metric.weighted_mean is not None:
        return _WeightedMean(
            ref.measure(metric.weighted_mean.value),
            ref.measure(metric.weighted_mean.weight),
            tuple(filters),
        )
    if metric.aggregation is not None:
        target_id = metric.aggregation_target or metric.measure
        kind = metric.aggregation_target_kind or ("measure" if metric.measure else None)
        if target_id is None or kind not in {"entity", "measure"}:
            raise SemanticDefinitionReadError(
                ref=f"metric:{metric.semantic_id}",
                location=metric.location,
                received="missing or unsupported aggregate target",
            )
        target: Ref[MeasureKind | EntityKind] = (
            ref.entity(target_id) if kind == "entity" else ref.measure(target_id)
        )
        return _Aggregate(metric.aggregation, target, tuple(filters))
    return expression or _UnsupportedExpression("description_unavailable")


def temporal_rules(metric: MetricIR, registry: Registry) -> _TemporalRules:
    declared = (
        _TemporalRule(ref.time_dimension(metric.additivity.over), metric.additivity.fold)
        if isinstance(metric.additivity, SemiAdditive) and metric.aggregation is None
        else None
    )
    resolved = resolve_metric_temporal_contract(metric, registry)
    if resolved is None:
        return _TemporalRules(
            declared=declared,
            override=metric.fold_override,
            source="context_required" if metric.metric_type == "derived" else "not_applicable",
        )
    return _TemporalRules(
        declared=declared,
        override=metric.fold_override,
        effective=_TemporalRule(ref.time_dimension(resolved.status_time_dimension), resolved.fold),
        source="metric_override"
        if metric.fold_override is not None
        else "declared"
        if declared is not None
        else "measure",
    )
