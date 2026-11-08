"""Transport captured contribution-time descriptions through fixed-data operations."""

from __future__ import annotations

from marivo._definition.query import (
    PairStatistic,
    ReduceValues,
    SelectValues,
    Statistic,
    ValueBinary,
    ValueExpression,
    ValuePartition,
    ValueShare,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.core.frame_interpretation import (
    FrameDerivedTimeSemantics,
    FrameTimeContribution,
    FrameValueTimeSemantics,
)
from marivo.analysis.core.frame_schema import LogicalSchema


def _indices(value: ValueExpression | Statistic | PairStatistic) -> tuple[int, ...]:
    if isinstance(value, ValueSlot):
        return (value.index,)
    if isinstance(value, ValueBinary):
        return (*_indices(value.left), *_indices(value.right))
    if isinstance(value, PairStatistic):
        return (*_indices(value.left), *_indices(value.right))
    if isinstance(value, ValueUnary):
        return _indices(value.operand)
    if isinstance(value, (Statistic, ValueShare)):
        return () if value.value is None else _indices(value.value)
    if isinstance(value, ValuePartition):
        return _indices(value.statistic)
    return ()


def unary_time_semantics(
    schema: LogicalSchema, operation: object
) -> tuple[tuple[str, FrameValueTimeSemantics], ...]:
    """Keep direct projections and mark calculated results as input descriptions."""
    if not isinstance(operation, (SelectValues, ReduceValues)):
        return schema.value_time_semantics
    previous = dict(schema.value_time_semantics)
    result: list[tuple[str, FrameValueTimeSemantics]] = []
    for name, expression in operation.values:
        if isinstance(expression, ValueSlot):
            fact = previous.get(schema.values[expression.index].name)
            if fact is not None:
                result.append((name, fact))
            continue
        facts: list[FrameTimeContribution] = []
        for index in dict.fromkeys(_indices(expression)):
            fact = previous.get(schema.values[index].name)
            if fact is not None:
                facts.extend(item for item in fact.contributions if item not in facts)
        if facts:
            result.append((name, FrameDerivedTimeSemantics(tuple(facts))))
    return tuple(result)


def time_summary(value: FrameValueTimeSemantics) -> str:
    """Return a bounded interpretation label, without inferring business equivalence."""
    timed = [item for item in value.contributions if item.kind == "clocked"]
    independent = len(timed) != len(value.contributions)
    mode = "mixed" if timed and independent else "timed" if timed else "independent"
    clocks = {item.field for item in timed}
    return (f"derived: {mode}" if value.kind == "derived" else mode) + (
        "; multiple clocks" if len(clocks) > 1 else ""
    )
