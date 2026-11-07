"""Contribution-time facts and requirements over closed Metric definitions."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldCalendarSample,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    RowNode,
    SampleDomainSpec,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.relations import LiteralPopulation, PopulationProgram, SampleDomain
from marivo._definition.types import CalendarCoordinate, ExactRef, IntervalCoordinate
from marivo._temporal import Grain
from marivo.semantic.errors import ErrorKind, SemanticExpressionError

_TimeLeaf = SourceAggregate | SourceWeightedMean | EntityCount | MemberCount
MissingTimeReason = Literal["no_entity_default", "fixed_member_count"]


@dataclass(frozen=True, slots=True)
class MetricTimeOccurrence:
    """One source or fixed-count occurrence, including its enclosing declared Metric."""

    path: tuple[str, ...]
    metric: ExactRef | None
    root: ExactRef | None
    binding: TimeBinding | None
    reason: MissingTimeReason | None
    node: _TimeLeaf


def _row_root(row: RowNode) -> ExactRef | None:
    from marivo._definition.expressions import MeasureAssertion, RowExpression

    if isinstance(row, MeasureAssertion):
        return _row_root(row.operand)
    if isinstance(row, RowExpression):
        return _row_root(row.inputs[0]) if row.inputs else None
    return row.root


def _children(node: MetricNode) -> Iterator[tuple[str, MetricNode]]:
    if isinstance(node, (ContributionFilter, MetricWindow, PerReduce)):
        yield "operand", node.operand
    elif isinstance(node, PerWeightedMean):
        yield "value", node.value
        yield "weight", node.weight
    elif isinstance(node, MetricExpression):
        for position, operand in enumerate(node.operands):
            yield f"operand[{position}]", operand


def metric_time_occurrences(
    node: MetricNode, declared: Mapping[ExactRef, MetricNode]
) -> tuple[MetricTimeOccurrence, ...]:
    """Return facts in occurrence order; constants and sample/predicate reads are neutral."""

    def visit(
        node: MetricNode, path: tuple[str, ...], metric: ExactRef | None
    ) -> Iterator[MetricTimeOccurrence]:
        if isinstance(node, DeclaredMetricRef):
            yield from visit(declared[node.ref], (*path, node.ref.ref.key), node.ref)
        elif isinstance(node, MemberCount):
            yield MetricTimeOccurrence(path, metric, None, None, "fixed_member_count", node)
        elif isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            root = (
                node.entity
                if isinstance(node, EntityCount)
                else node.contribution_root
                or _row_root(node.value if isinstance(node, SourceWeightedMean) else node.operand)
            )
            yield MetricTimeOccurrence(
                path,
                metric,
                root,
                node.time,
                "no_entity_default" if node.time is None else None,
                node,
            )
        else:
            for role, child in _children(node):
                yield from visit(child, (*path, role), metric)

    return tuple(visit(node, (), None))


def require_metric_time(
    node: MetricNode,
    declared: Mapping[ExactRef, MetricNode],
    *,
    operation: str,
    output: str | None = None,
    help_target: str = "load",
) -> None:
    """Require concrete contribution time at every occurrence of a completed Metric."""
    missing = tuple(
        fact for fact in metric_time_occurrences(node, declared) if fact.binding is None
    )
    if not missing:
        return
    details = tuple(
        {
            "path": item.path,
            "metric": item.metric.ref.key if item.metric is not None else None,
            "root": item.root.ref.key if item.root is not None else None,
            "reason": item.reason,
        }
        for item in missing
    )
    received = ", ".join(
        f"{' / '.join(item.path) or 'root'}: "
        f"{item.root.ref.key if item.root is not None else 'fixed Population.count'}"
        for item in missing[:6]
    )
    raise SemanticExpressionError(
        kind=ErrorKind.INVALID_TIME_PATH,
        message=f"{operation} requires contribution time for every Metric branch"
        + (f" in output {output!r}." if output is not None else "."),
        refs=tuple(
            dict.fromkeys(
                reference.ref.key
                for item in missing
                for reference in (item.metric, item.root)
                if reference is not None
            )
        ),
        expected="a concrete contribution time (TimeDimension) on every Metric branch",
        received=received,
        action=(
            "Define the missing source reductions with a valid time= or Entity default_time, "
            "or remove the time operation. Keep fixed Population.count outside time operations."
            if any(item.reason == "fixed_member_count" for item in missing)
            else "Define the missing source reductions with a valid time= or Entity default_time, "
            "or remove the time operation."
        ),
        details={"output": output, "missing_contribution_time": details},
        help_target=help_target,
    )


def calendar_sample_domain(samples: SampleDomainSpec) -> bool:
    if isinstance(samples, (Grain, FieldCalendarSample)):
        return True
    if isinstance(samples, (SampleDomain, LiteralPopulation, PopulationProgram)):
        return any(
            isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) for axis in samples.axes
        )
    return False


def validate_metric_time_consumers(
    node: MetricNode, declared: Mapping[ExactRef, MetricNode]
) -> None:
    """Validate existing Grain and window operations even without external report time."""
    seen: set[int] = set()

    def visit(node: MetricNode) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref])
            return
        if isinstance(node, MetricWindow):
            require_metric_time(
                node.operand, declared, operation="Metric.window", help_target="metric.window"
            )
        elif isinstance(node, (PerReduce, PerWeightedMean)) and calendar_sample_domain(
            node.samples
        ):
            require_metric_time(
                node,
                declared,
                operation="Metric.per with Grain samples",
                help_target="metric.per",
            )
        for _role, child in _children(node):
            visit(child)

    visit(node)
