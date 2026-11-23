"""Record singleton partitions proven by checked input keys."""

from collections.abc import Iterator
from dataclasses import replace

from marivo._definition.query import ValueBinary, ValueExpression, ValuePartition, ValueUnary
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.compiler.query_stages import FrameCompute, QueryPlan, QueryStage, RuleDecision
from marivo.analysis.core.frame_value_analysis import expressions, statistic_expressions


def _partitions(expression: ValueExpression) -> Iterator[tuple[int, ...]]:
    if isinstance(expression, ValuePartition):
        yield expression.positions
        for operand in statistic_expressions(expression.statistic):
            yield from _partitions(operand)
    elif isinstance(expression, ValueBinary):
        yield from _partitions(expression.left)
        yield from _partitions(expression.right)
    elif isinstance(expression, ValueUnary):
        yield from _partitions(expression.operand)


def with_singleton_partitions(plan: QueryPlan) -> QueryPlan:
    if not plan.optimization_policy.allows("unique_keys_degenerate_over"):
        return plan
    by_id = {stage.stage_id: stage for stage in plan.stages}
    stages: list[QueryStage] = []
    decisions = list(plan.optimization_decisions)
    for stage in plan.stages:
        if isinstance(stage, FrameCompute) and isinstance(stage.program, FrameProgram):
            source = by_id[stage.inputs[0]]
            program = stage.program
            partitions = frozenset(
                positions
                for expression in expressions(program.operation)
                for positions in _partitions(expression)
                if source.unique_keys.proves(
                    tuple(key for index in positions for key in program.key_groups[index])
                )
            )
            if partitions:
                stage = replace(stage, program=replace(program, singleton_partitions=partitions))
                decisions.append(
                    RuleDecision(
                        "unique_keys_degenerate_over",
                        "adopted",
                        (source.stage_id, stage.stage_id),
                        "Partition keys prove at most one checked input row per group.",
                    )
                )
        stages.append(stage)
    return replace(plan, stages=tuple(stages), optimization_decisions=tuple(decisions))
