"""Bounded facts about a pending Frame without loading any source."""

from __future__ import annotations

from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    PairStatistic,
    PresenceSlot,
    SelectValues,
    TopValues,
    ValueBinary,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.binding.attribute_rules import rule_effects
from marivo.analysis.core.attribute_game import AttributeGameValues
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    FrameBacking,
    FrameNode,
    LiteralFrameBinding,
    MemoryFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeInputsValues,
    AttributeProjectValues,
    CompareFrameValues,
)
from marivo.analysis.core.frame_fix import FixFrameValues
from marivo.analysis.model.frame_attribute_merge import AttributeMergeValues


def _pending_source_inputs(
    frame: FrameNode,
) -> tuple[tuple[FrameNode, SourceFrameBinding], ...]:
    """Collect explicit current source leaves; fixed inputs never recover history."""
    pending: list[FrameNode] = [frame]
    seen: set[str] = set()
    sources: list[tuple[FrameNode, SourceFrameBinding]] = []
    while pending:
        current = pending.pop()
        if current._input_id in seen:
            continue
        seen.add(current._input_id)
        backing = current._backing
        if not isinstance(backing, ExpressionFrameBacking):
            continue
        root = backing.root
        if isinstance(root, SourceFrameBinding):
            sources.append((current, root))
        elif isinstance(root, OperationFrameBinding):
            pending.extend(reversed(root.inputs))
    return tuple(sources)


def _input_kind(backing: FrameBacking) -> str:
    if isinstance(backing, MemoryFrameBacking):
        return "memory"
    if isinstance(backing, StoredFrameBacking):
        return "stored"
    if isinstance(backing.root, SourceFrameBinding):
        return "expression(source)"
    if isinstance(backing.root, LiteralFrameBinding):
        return "expression(literal)"
    return "expression"


def _value_summary(value: object, source: FrameNode, depth: int = 0) -> str:
    if depth >= 8:
        return "... omitted (expression depth)"
    if isinstance(value, ValueSlot):
        name = ".".join(source._schema.value_paths[value.index])
        return f"value[{name!r}]"
    if isinstance(value, CoordinateSlot):
        return f"coordinate[{source._schema.axes[value.index].token!r}]"
    if isinstance(value, PresenceSlot):
        return f"presence[{'.'.join(value.path)!r}]"
    if isinstance(value, ValueLiteral):
        return repr(value.value)
    if isinstance(value, ValueUnary):
        return f"{value.operation}({_value_summary(value.operand, source, depth + 1)})"
    if isinstance(value, ValueBinary):
        return (
            f"{value.operation}({_value_summary(value.left, source, depth + 1)}, "
            f"{_value_summary(value.right, source, depth + 1)})"
        )
    return type(value).__name__


def _operation_summary(binding: OperationFrameBinding) -> str:
    operation, source = binding.operation, binding.inputs[0]
    if isinstance(operation, AttributeMergeValues):
        targets: list[str] = []
        for frame in binding.inputs[:6]:
            backing = frame._backing
            if isinstance(backing, ExpressionFrameBacking) and isinstance(
                backing.root, OperationFrameBinding
            ):
                targets.append(_operation_summary(backing.root))
        remaining = len(binding.inputs) - len(targets)
        suffix = f", remaining={remaining}" if remaining else ""
        return f"attribute(targets={tuple(targets)!r}{suffix})"
    if isinstance(operation, SelectValues):
        values = tuple((name, _value_summary(value, source)) for name, value in operation.values)
        return f"select(values={values!r})"
    if isinstance(operation, FixFrameValues):
        return f"fix(axis={source._schema.axes[operation.position].name!r}, key={operation.key!r})"
    if isinstance(operation, FilterValues):
        return f"where(predicate={_value_summary(operation.predicate, source)})"
    if isinstance(operation, TopValues):
        return (
            f"top(n={operation.n}, by={_value_summary(operation.score, source)}, "
            f"descending={operation.descending})"
        )
    if isinstance(operation, CompareFrameValues):
        return f"compare(baselines={operation.baselines!r})"
    if isinstance(operation, AlignFrameValues):
        match = f", match={operation.match!r}" if operation.match is not None else ""
        return f"align(on={operation.pairs!r}{match})"
    if isinstance(operation, AttributeContributionValues):
        target = operation.target
        rule = (
            "level_share"
            if operation.single_endpoint
            else "rate_mix"
            if rule_effects(operation.rules[0][0][1])
            else "additive"
        )
        return (
            f"attribute(target={target.path!r}, kind={target.mode!r}, rule={rule!r})"
            if target is not None
            else "attribute(source_contribution)"
        )
    if isinstance(operation, AttributeGameValues):
        return (
            f"attribute(target={operation.target.path!r}, rule='group_replacement', "
            f"permutations={operation.permutations}, seed={operation.seed}, "
            "players=unknown, state_size=unknown)"
        )
    if isinstance(operation, AttributeInputsValues):
        return "private Attribute input preparation"
    assert not isinstance(operation, AttributeProjectValues)
    statistics = tuple(
        (
            name,
            statistic.method,
            (_value_summary(statistic.left, source), _value_summary(statistic.right, source))
            if isinstance(statistic, PairStatistic)
            else None
            if statistic.value is None
            else _value_summary(statistic.value, source),
        )
        for name, statistic in operation.values
    )
    axes = tuple(source._schema.axes[position].token for position in operation.positions)
    return f"reduce(values={statistics!r}, by={axes!r})"
