"""Execute the fixed Query stage schedule in topological order."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    FrameInput,
    FrameKeysInput,
    LocalCompute,
    QueryPlan,
    QueryStage,
    SourceReadStage,
)
from marivo.analysis.model.errors import reject

StageValue = TypeVar("StageValue")


def run_query_plan(
    plan: QueryPlan,
    read: Callable[[SourceReadStage], StageValue],
    local: Callable[[LocalCompute, tuple[StageValue, ...]], StageValue],
    dependent_read: Callable[[SourceReadStage, tuple[StageValue, ...]], StageValue] | None = None,
    *,
    fixed: Callable[[FrameInput], StageValue] | None = None,
    frame_keys: Callable[[FrameKeysInput], StageValue] | None = None,
    frame: Callable[[FrameCompute, tuple[StageValue, ...]], StageValue] | None = None,
    finish: Callable[[QueryStage, StageValue], StageValue] | None = None,
    available: Mapping[str, StageValue] | None = None,
) -> StageValue:
    """Run each owner to completion before finishing and releasing its inputs."""
    outputs: dict[str, StageValue] = {}
    cached = {} if available is None else available
    stages = {stage.stage_id: stage for stage in plan.stages}
    remaining = dict.fromkeys(stages, 0)
    required: set[str] = set()

    def demand(identifier: str) -> None:
        if identifier in required:
            return
        required.add(identifier)
        if identifier in cached:
            return
        for dependency in stages[identifier].inputs:
            remaining[dependency] += 1
            demand(dependency)

    if cached:
        demand(plan.publish)
    else:
        required.update(stages)
        remaining = {stage_id: len(consumers) for stage_id, consumers in plan.consumers}
    for stage in plan.stages:
        if stage.stage_id not in required:
            continue
        if stage.stage_id in cached:
            output = cached[stage.stage_id]
            if finish is not None:
                output = finish(stage, output)
            outputs[stage.stage_id] = output
            continue
        missing = tuple(item for item in stage.inputs if item not in outputs)
        if missing:
            reject(
                "stage inputs completed earlier in topological order",
                repr(missing),
                "Plan producers before their consumers.",
                "analysis.query_schedule",
            )
        output = _execute_stage(
            stage,
            tuple(outputs[item] for item in stage.inputs),
            read=read,
            dependent_read=dependent_read,
            frame_keys=frame_keys,
            fixed=fixed,
            frame=frame,
            local=local,
        )
        if finish is not None:
            output = finish(stage, output)
        for item in stage.inputs:
            remaining[item] -= 1
            if remaining[item] == 0 and item != plan.publish:
                del outputs[item]
        outputs[stage.stage_id] = output
    return outputs[plan.publish]


def _execute_stage(
    stage: QueryStage,
    inputs: tuple[StageValue, ...],
    *,
    read: Callable[[SourceReadStage], StageValue],
    dependent_read: Callable[[SourceReadStage, tuple[StageValue, ...]], StageValue] | None,
    frame_keys: Callable[[FrameKeysInput], StageValue] | None,
    fixed: Callable[[FrameInput], StageValue] | None,
    frame: Callable[[FrameCompute, tuple[StageValue, ...]], StageValue] | None,
    local: Callable[[LocalCompute, tuple[StageValue, ...]], StageValue],
) -> StageValue:
    if isinstance(stage, SourceReadStage):
        if stage.inputs:
            if dependent_read is None:
                reject(
                    "a typed parameterized read consumer",
                    stage.stage_id,
                    "Use the planned dependency reader.",
                    "analysis.query_schedule",
                )
            return dependent_read(stage, inputs)
        else:
            return read(stage)
    elif isinstance(stage, FrameKeysInput):
        if frame_keys is None:
            reject(
                "a fully composed Frame schedule",
                stage.stage_id,
                "Resolve Frame key inputs in the outer Frame plan before execution.",
                "frame.population",
            )
        return frame_keys(stage)
    elif isinstance(stage, FrameInput):
        if fixed is None:
            reject(
                "an invocation fixed-input reader",
                stage.input_id,
                "Bind the exact fixed input.",
                "analysis.query_schedule",
            )
        return fixed(stage)
    elif isinstance(stage, FrameCompute):
        if frame is None:
            reject(
                "a typed Frame kernel",
                stage.stage_id,
                "Use the Frame schedule runner.",
                "analysis.query_schedule",
            )
        return frame(stage, inputs)
    else:
        return local(stage, inputs)
