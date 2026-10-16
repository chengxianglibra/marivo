"""Bounded semantic Metric preview through the shared nullable Ibis kernel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldCompare,
    FieldIn,
    FieldIsNull,
    FieldPredicate,
    MemberCount,
    MetricAdd,
    MetricConstant,
    MetricDivide,
    MetricMultiply,
    MetricNegate,
    MetricNode,
    MetricSubtract,
    PerReduce,
    PerWeightedMean,
    PredAnd,
    PredNot,
    PredOr,
    RowCompare,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import children, direct_refs, walk
from marivo.refs import SemanticKind, ref
from marivo.semantic.expressions import Metric
from marivo.semantic.metric_graph_lowering import (
    NumericCheck,
    NumericValue,
    lower_field_predicate,
    lower_metric_formula,
    lower_source_reduction,
    numeric_failure,
)

if TYPE_CHECKING:
    from marivo.semantic.resolver import SemanticResolver


def validate_preview_metric(metric: Metric) -> None:
    """Reject sample/Population routes before opening a datasource."""
    closure = walk((metric.node,), resolve=dict(metric._declared).get)
    if any(isinstance(node, (PerReduce, PerWeightedMean, MemberCount)) for node in closure.order):
        numeric_failure(
            "an ordinary source Metric preview",
            "Population/sample preview preparation is unavailable; use physical field previews",
        )

    def validate_predicate(predicate: FieldPredicate) -> None:
        if isinstance(predicate, (PredAnd, PredOr)):
            for child in predicate.operands:
                validate_predicate(child)
        elif isinstance(predicate, PredNot):
            validate_predicate(predicate.operand)
        elif (
            isinstance(predicate, (FieldCompare, FieldIn, FieldIsNull))
            and predicate.read is not None
            and (predicate.read.path or predicate.read.at is not None)
        ):
            numeric_failure(
                "native fields in an ordinary Metric preview",
                "path/attribute anchor preview preparation is unavailable",
            )

    for node in closure.order:
        if (
            isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount))
            and node.time is not None
            and (node.time.path or node.time.at is not None)
        ):
            numeric_failure(
                "native time in an ordinary Metric preview",
                "path/attribute anchor preview preparation is unavailable",
            )
        if isinstance(node, ContributionFilter):
            if not isinstance(
                node.predicate,
                (FieldCompare, FieldIn, FieldIsNull, RowCompare, PredAnd, PredOr, PredNot),
            ):
                numeric_failure(
                    "ordinary Field predicates in a Metric preview",
                    "Population preview preparation is unavailable",
                )
            validate_predicate(node.predicate)


def lower_preview_metric(
    metric: Metric, resolver: SemanticResolver
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Prepare bounded physical fields, then call the same Row/Metric kernel."""
    declared = dict(metric._declared)
    closure = walk((metric.node,), resolve=declared.get)
    refs = {exact.ref for node in closure.order for exact in direct_refs(node)}
    tables: dict[str, ir.Table] = {}
    registry = resolver.catalog._state.registry
    for root in metric.roots:
        table = resolver.entity(ref.entity(root.ref.path))
        fields: dict[str, ir.Value] = {}
        for target in sorted(refs, key=lambda item: item.key):
            if (
                target.kind is SemanticKind.MEASURE
                and target.path in registry.measures
                and registry.measures[target.path].entity == root.ref.path
            ):
                fields[target.path] = resolver.measure_on(ref.measure(target.path), table)
            elif (
                target.kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION)
                and registry.dimensions[target.path].entity == root.ref.path
            ):
                field_ref = (
                    ref.time_dimension(target.path)
                    if target.kind is SemanticKind.TIME_DIMENSION
                    else ref.dimension(target.path)
                )
                fields[target.path] = resolver.dimension_on(field_ref, table)
        tables[root.ref.path] = table.mutate(**fields)
    values: dict[tuple[int, ...], NumericValue] = {}
    reductions: list[ir.Table] = []
    checks: list[tuple[str, NumericCheck]] = []

    def visit(
        node: MetricNode, occurrence: tuple[int, ...], predicates: tuple[FieldPredicate, ...]
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*occurrence, 0), predicates)
            return
        if isinstance(node, ContributionFilter):
            assert isinstance(
                node.predicate,
                (FieldCompare, FieldIn, FieldIsNull, RowCompare, PredAnd, PredOr, PredNot),
            )
            visit(node.operand, (*occurrence, 0), (*predicates, node.predicate))
            return
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            root = (
                node.entity.ref.path
                if isinstance(node, EntityCount)
                else _source_root(node, registry_roots)
            )
            table = tables[root].view()
            fields = {name: table[name] for name in table.columns}
            selected = tuple(lower_field_predicate(predicate, fields) for predicate in predicates)
            predicate_checks = tuple(check for item in selected for check in item.checks)
            predicate_summary = (
                table.aggregate(
                    **{
                        f"preview_predicate.{len(reductions)}.{i}": check.violation.any().fill_null(
                            False
                        )
                        for i, check in enumerate(predicate_checks)
                    }
                )
                if predicate_checks
                else None
            )
            filtered = table.filter(*(item.value for item in selected)) if selected else table
            item = lower_source_reduction(
                node, filtered, {name: filtered[name] for name in filtered.columns}
            )
            slot = f"preview_value.{len(reductions)}"
            names = tuple(f"preview_check.{len(reductions)}.{i}" for i in range(len(item.checks)))
            result = filtered.aggregate(
                **{
                    slot: ops.Cast(item.value, to=item.value.type()).to_expr(),
                    **{
                        name: check.violation
                        for name, check in zip(names, item.checks, strict=True)
                    },
                }
            )
            checks.extend(zip(names, item.checks, strict=True))
            if predicate_summary is not None:
                result = result.cross_join(predicate_summary)
                checks.extend(
                    (f"preview_predicate.{len(reductions)}.{i}", check)
                    for i, check in enumerate(predicate_checks)
                )
            reductions.append(result)
            values[occurrence] = NumericValue(result[slot])
            return
        for edge in children(node):
            child = edge.child
            # Only ordinary Metric nodes survive validate_preview_metric.
            if isinstance(
                child,
                (
                    ContributionFilter,
                    DeclaredMetricRef,
                    EntityCount,
                    SourceAggregate,
                    SourceWeightedMean,
                    MetricAdd,
                    MetricSubtract,
                    MetricMultiply,
                    MetricDivide,
                    MetricNegate,
                    MetricConstant,
                ),
            ):
                visit(child, (*occurrence, edge.index), predicates)

    registry_roots = {path: item.entity for path, item in registry.measures.items()}
    registry_roots.update({path: item.entity for path, item in registry.dimensions.items()})
    visit(metric.node, (0,), ())
    result = reductions[0] if reductions else ibis.literal(0).name("__singleton").as_table()
    for reduction in reductions[1:]:
        result = result.cross_join(reduction)
    values = {
        occurrence: NumericValue(result[item.value.get_name()])
        for occurrence, item in values.items()
    }
    final = lower_metric_formula(metric.node, (0,), values, declared)
    for i, check in enumerate(final.checks):
        checks.append((f"preview_formula_check.{i}", check))
    return result.select(
        value=ops.Cast(final.value, to=final.value.type()).to_expr(),
        **{
            name: result[name]
            for name, _ in checks
            if not name.startswith("preview_formula_check.")
        },
        **{f"preview_formula_check.{i}": check.violation for i, check in enumerate(final.checks)},
    ), tuple(checks)


def _source_root(node: SourceAggregate | SourceWeightedMean, roots: Mapping[str, str]) -> str:
    fields = [exact.ref.path for exact in direct_refs(node) if exact.ref.path in roots]
    return roots[fields[0]]
