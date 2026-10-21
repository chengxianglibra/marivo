"""Execute the fixed Query stage schedule in topological order."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    FrameKeysInput,
    LocalCompute,
    QueryPlan,
    QueryStage,
    SourceRead,
)
from marivo.analysis.core.model import reject

StageValue = TypeVar("StageValue")


def run_query_plan(
    plan: QueryPlan,
    read: Callable[[SourceRead], StageValue],
    local: Callable[[LocalCompute, tuple[StageValue, ...]], StageValue],
    dependent_read: Callable[[SourceRead, tuple[StageValue, ...]], StageValue] | None = None,
    *,
    fixed: Callable[[FrameInput], StageValue] | None = None,
    frame: Callable[[FrameCompute, tuple[StageValue, ...]], StageValue] | None = None,
    finish: Callable[[QueryStage, StageValue], StageValue] | None = None,
) -> StageValue:
    """Run each owner to completion before finishing and releasing its inputs."""
    outputs: dict[str, StageValue] = {}
    remaining = {stage_id: len(consumers) for stage_id, consumers in plan.consumers}
    for stage in plan.stages:
        missing = tuple(item for item in stage.inputs if item not in outputs)
        if missing:
            reject(
                "stage inputs completed earlier in topological order",
                repr(missing),
                "Plan producers before their consumers.",
                "analysis.query_schedule",
            )
        if isinstance(stage, SourceRead):
            if stage.inputs:
                if dependent_read is None:
                    reject(
                        "a typed parameterized read consumer",
                        stage.stage_id,
                        "Use the planned dependency reader.",
                        "analysis.query_schedule",
                    )
                output = dependent_read(stage, tuple(outputs[item] for item in stage.inputs))
            else:
                output = read(stage)
        elif isinstance(stage, FrameKeysInput):
            reject(
                "a fully composed Frame schedule",
                stage.stage_id,
                "Resolve Frame key inputs in the outer Frame plan before execution.",
                "frame.population",
            )
        elif isinstance(stage, FrameInput):
            if fixed is None:
                reject(
                    "an invocation fixed-input reader",
                    stage.input_id,
                    "Bind the exact fixed input.",
                    "analysis.query_schedule",
                )
            output = fixed(stage)
        elif isinstance(stage, FrameCompute):
            if frame is None:
                reject(
                    "a typed Frame kernel",
                    stage.stage_id,
                    "Use the Frame schedule runner.",
                    "analysis.query_schedule",
                )
            output = frame(stage, tuple(outputs[item] for item in stage.inputs))
        else:
            output = local(stage, tuple(outputs[item] for item in stage.inputs))
        if finish is not None:
            output = finish(stage, output)
        for item in stage.inputs:
            remaining[item] -= 1
            if remaining[item] == 0 and item != plan.publish:
                del outputs[item]
        outputs[stage.stage_id] = output
    return outputs[plan.publish]
