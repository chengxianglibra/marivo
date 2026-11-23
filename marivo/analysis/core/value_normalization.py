"""Lower Frame-specific value notation to the finite result grammar."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.query import (
    FilterValues,
    PairStatistic,
    ReduceValues,
    SelectValues,
    Statistic,
    TopValues,
    ValueBinary,
    ValueExpression,
    ValuePartition,
    ValueShare,
    ValueUnary,
)
from marivo.analysis.core.frame_operations import FrameUnaryOperationKind


def physical_expression(value: ValueExpression, axis_count: int) -> ValueExpression:
    """Expand explicit share intent once into the existing sum/partition/divide grammar."""

    if isinstance(value, ValueShare):
        numerator = physical_expression(value.value, axis_count)
        groups = tuple(p for p in range(axis_count) if p not in value.positions)
        return ValueBinary("div", numerator, ValuePartition(Statistic("sum", numerator), groups))
    if isinstance(value, ValueUnary):
        return replace(value, operand=physical_expression(value.operand, axis_count))
    if isinstance(value, ValueBinary):
        return replace(
            value,
            left=physical_expression(value.left, axis_count),
            right=physical_expression(value.right, axis_count),
        )
    if isinstance(value, ValuePartition):
        return replace(value, statistic=physical_statistic(value.statistic, axis_count))
    return value


def physical_statistic(
    value: Statistic | PairStatistic, axis_count: int
) -> Statistic | PairStatistic:

    if isinstance(value, PairStatistic):
        return replace(
            value,
            left=physical_expression(value.left, axis_count),
            right=physical_expression(value.right, axis_count),
        )
    return replace(
        value, value=None if value.value is None else physical_expression(value.value, axis_count)
    )


def physical_operation(
    operation: FrameUnaryOperationKind, axis_count: int
) -> FrameUnaryOperationKind:
    """Prepare all expression positions, including predicates, scores and statistic operands."""

    if isinstance(operation, SelectValues):
        return replace(
            operation,
            values=tuple((n, physical_expression(v, axis_count)) for n, v in operation.values),
        )
    if isinstance(operation, ReduceValues):
        return replace(
            operation,
            values=tuple((n, physical_statistic(v, axis_count)) for n, v in operation.values),
        )
    if isinstance(operation, FilterValues):
        return replace(operation, predicate=physical_expression(operation.predicate, axis_count))
    if isinstance(operation, TopValues):
        return replace(operation, score=physical_expression(operation.score, axis_count))
    return operation
