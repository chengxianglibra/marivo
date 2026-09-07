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
    composition_components,
)
from marivo.semantic.metric_graph import MAX_EXPRESSION_DEPTH, MAX_EXPRESSION_OCCURRENCES
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
    """Describe time semantics without inferring a fold for a composition."""
    occurrences = 0

    def visit(current: MetricIR, active: tuple[str, ...]) -> _TemporalRules:
        nonlocal occurrences
        occurrences += 1
        if current.semantic_id in active:
            raise SemanticDefinitionReadError(
                ref=f"metric:{current.semantic_id}",
                location=current.location,
                received="cyclic metric dependency in temporal description",
            )
        if len(active) >= MAX_EXPRESSION_DEPTH or occurrences > MAX_EXPRESSION_OCCURRENCES:
            raise SemanticDefinitionReadError(
                ref=f"metric:{current.semantic_id}",
                location=current.location,
                received="temporal description exceeds metric graph depth or occurrence limits",
            )
        composition = current.composition
        if composition is None:
            if current.metric_type == "derived":
                raise SemanticDefinitionReadError(
                    ref=f"metric:{current.semantic_id}",
                    location=current.location,
                    received="derived metric has no composition",
                )
            return _leaf_temporal_rules(current, registry)
        if not isinstance(
            composition, (LinearComposition, RatioComposition, CumulativeComposition)
        ):
            raise SemanticDefinitionReadError(
                ref=f"metric:{current.semantic_id}",
                location=current.location,
                received="unknown metric composition",
            )
        children: list[tuple[MetricIR, _TemporalRules]] = []
        for dependency in composition_components(composition).values():
            child = registry.metrics.get(dependency)
            if child is None:
                raise SemanticDefinitionReadError(
                    ref=f"metric:{current.semantic_id}",
                    location=current.location,
                    received=f"missing temporal component metric:{dependency}",
                )
            children.append((child, visit(child, (*active, current.semantic_id))))
        ordinary_linear = isinstance(composition, LinearComposition) and all(
            child.additivity == "additive" and rules.source == "not_applicable"
            for child, rules in children
        )
        return _TemporalRules(
            source="not_applicable" if ordinary_linear else "component_defined",
        )

    return visit(metric, ())


def _leaf_temporal_rules(metric: MetricIR, registry: Registry) -> _TemporalRules:
    target_kind = metric.aggregation_target_kind or ("measure" if metric.measure else None)
    if metric.aggregation is not None and target_kind == "measure":
        target = metric.aggregation_target or metric.measure
        if target not in registry.measures and target not in registry.dimensions:
            raise SemanticDefinitionReadError(
                ref=f"metric:{metric.semantic_id}",
                location=metric.location,
                received=f"missing temporal aggregate target {target!r}",
            )
    declared = (
        _TemporalRule(ref.time_dimension(metric.additivity.over), metric.additivity.fold)
        if isinstance(metric.additivity, SemiAdditive) and metric.aggregation is None
        else None
    )
    resolved = resolve_metric_temporal_contract(metric, registry)
    if resolved is None:
        if metric.fold_override is not None:
            raise SemanticDefinitionReadError(
                ref=f"metric:{metric.semantic_id}",
                location=metric.location,
                received="fold override has no applicable status-time contract",
            )
        return _TemporalRules(
            declared=declared,
            override=metric.fold_override,
            source="not_applicable",
        )
    axis = registry.dimensions.get(resolved.status_time_dimension)
    if axis is None or not axis.is_time_dimension:
        raise SemanticDefinitionReadError(
            ref=f"metric:{metric.semantic_id}",
            location=metric.location,
            received=f"unresolved status time dimension {resolved.status_time_dimension!r}",
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
