"""Bounded semantic Metric preview through the shared nullable Ibis kernel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.deferred import Deferred

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import children, direct_refs, walk
from marivo._definition.relations import PopulationContains
from marivo._definition.types import field_read_key
from marivo._source_aggregates import aggregate_operation, aggregate_repair_action, native_aggregate
from marivo.datasource.adapters import SourceSession
from marivo.refs import SemanticKind, ref
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._numeric_duration import recipe_duration
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
    lower_metric_formula,
    lower_predicate,
    lower_row,
    lower_source_reduction,
    numeric_failure,
)

if TYPE_CHECKING:
    from marivo.semantic.resolver import SemanticResolver


def validate_preview_metric(value: Deferred) -> None:
    """Reject sample/Population routes before opening a datasource."""
    metric = consume_metric(value)
    closure = walk((metric.node,), resolve=dict(metric._declared).get)
    if any(
        isinstance(
            node, (PerReduce, PerWeightedMean, MemberCount, MetricWindow, PopulationContains)
        )
        for node in closure.order
    ):
        numeric_failure(
            "an ordinary source Metric preview",
            "Population/sample/window preview preparation is unavailable; use physical field previews",
        )

    for node in closure.order:
        if (
            isinstance(node, RowField)
            and node.read is not None
            and (node.read.path or node.read.at is not None)
        ):
            numeric_failure(
                "native fields in an ordinary Metric preview",
                "path/attribute anchor preview preparation is unavailable",
            )
        if (
            isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount))
            and node.time is not None
            and (node.time.path or node.time.at is not None)
        ):
            numeric_failure(
                "native time in an ordinary Metric preview",
                "path/attribute anchor preview preparation is unavailable",
            )


def lower_preview_metric(
    value: Deferred, resolver: SemanticResolver
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...], tuple[tuple[str, str], ...]]:
    """Prepare bounded physical fields, then call the same Row/Metric kernel."""
    metric = consume_metric(value)
    declared = dict(metric._declared)
    closure = walk((metric.node,), resolve=declared.get)
    refs = {exact.ref for node in closure.order for exact in direct_refs(node)}
    tables: dict[str, ir.Table] = {}
    durations: dict[str, str] = {}
    registry = resolver.catalog._state.registry
    backend = registry.datasources[
        registry.entities[metric.roots[0].ref.path].datasource
    ].backend_type
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
        source: SourceSession = resolver.connections.source_session(
            registry.entities[root.ref.path].datasource,
            registry.datasources[registry.entities[root.ref.path].datasource],
        )
        for name, expression in fields.items():
            units = source.duration_units(expression)
            if len(units) > 1:
                numeric_failure("one exact Duration unit", f"{name}: {units}")
            if units:
                durations[name] = units[0]
            elif expression.type().is_interval():
                durations[name] = "us"
        tables[root.ref.path] = table.mutate(**fields)
    values: dict[tuple[int, ...], NumericValue] = {}
    reductions: list[ir.Table] = []
    samples: list[tuple[str, str]] = []
    checks: list[tuple[str, NumericCheck]] = []

    def visit(
        node: MetricNode, occurrence: tuple[int, ...], predicates: tuple[PredicateExpression, ...]
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*occurrence, 0), predicates)
            return
        if isinstance(node, ContributionFilter):
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
            selected = tuple(lower_predicate(predicate, fields) for predicate in predicates)
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
            fields = {name: filtered[name] for name in filtered.columns}
            if isinstance(node, SourceAggregate) and aggregate_operation(node.reducer) is not None:
                dtype = lower_row(node.operand, fields).value.type()
                duration = _row_duration(node.operand, durations)
                if (
                    native_aggregate(backend, node.reducer, dtype, duration=duration is not None)
                    is None
                ):
                    from marivo.semantic.errors import ErrorKind, SemanticExpressionError

                    input_type = f"duration[{duration}]" if duration is not None else str(dtype)
                    raise SemanticExpressionError(
                        kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                        message="The source aggregate has no qualified native compilation.",
                        expected="a native algorithm preserving the declared accuracy and input type",
                        received=f"{backend}: {node.reducer}({input_type})",
                        action=aggregate_repair_action(
                            backend, node.reducer, dtype, duration=duration is not None
                        ),
                    )
            item = lower_source_reduction(node, filtered, fields)
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
            slot_checks = list(zip(names, item.checks, strict=True))
            if predicate_summary is not None:
                result = result.cross_join(predicate_summary)
                slot_checks.extend(
                    (f"preview_predicate.{len(reductions)}.{i}", check)
                    for i, check in enumerate(predicate_checks)
                )
            reductions.append(result)
            values[occurrence] = NumericValue(
                result[slot],
                tuple(
                    NumericCheck(result[name], check.expected, check.received)
                    for name, check in slot_checks
                ),
            )
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
                    MetricExpression,
                ),
            ):
                visit(child, (*occurrence, edge.index), predicates)

    registry_roots = {path: item.entity for path, item in registry.measures.items()}
    registry_roots.update({path: item.entity for path, item in registry.dimensions.items()})
    visit(metric.node, (0,), ())
    result = reductions[0] if reductions else ibis.literal(0).name("__singleton").as_table()
    for reduction in reductions[1:]:
        result = result.cross_join(reduction)
    for index, (entity_id, table) in enumerate(tables.items()):
        name = f"preview_rows.{index}"
        result = result.cross_join(table.aggregate(**{name: table.count()}))
        samples.append((entity_id, name))
    values = {
        occurrence: NumericValue(
            result[item.value.get_name()],
            tuple(
                NumericCheck(result[check.violation.get_name()], check.expected, check.received)
                for check in item.checks
            ),
        )
        for occurrence, item in values.items()
    }
    final = lower_metric_formula(metric.node, (0,), values, declared)
    for i, check in enumerate(final.checks):
        checks.append((f"preview_formula_check.{i}", check))
    return (
        result.select(
            value=ops.Cast(final.value, to=final.value.type()).to_expr(),
            **{name: result[name] for _entity_id, name in samples},
            **{
                name: result[name]
                for name, _ in checks
                if not name.startswith("preview_formula_check.")
            },
            **{
                f"preview_formula_check.{i}": check.violation
                for i, check in enumerate(final.checks)
            },
        ),
        tuple(checks),
        tuple(samples),
    )


def _row_duration(node: RowNode, durations: Mapping[str, str]) -> str | None:
    """Keep source units through row recipes, including dimensionless quotients."""
    if isinstance(node, RowField):
        return durations.get(
            field_read_key(node.read) if node.read is not None else node.ref.ref.path
        )
    if isinstance(node, MeasureAssertion):
        return _row_duration(node.operand, durations)
    assert isinstance(node, RowExpression)
    return recipe_duration(
        node.recipe, tuple(_row_duration(item, durations) for item in node.inputs)
    )


def _source_root(node: SourceAggregate | SourceWeightedMean, roots: Mapping[str, str]) -> str:
    fields = [exact.ref.path for exact in direct_refs(node) if exact.ref.path in roots]
    return roots[fields[0]]
