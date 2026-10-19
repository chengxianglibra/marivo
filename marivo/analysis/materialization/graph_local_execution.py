"""Local kernels over checked stage outputs from the current source invocation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import pandas as pd

from marivo.analysis.compiler.graph_plan import FrameCompute, LocalCompute
from marivo.analysis.compiler.query_program import FrameCombineProgram
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    StageOutput,
)

LocalKernel: TypeAlias = Callable[[LocalCompute, tuple[pd.DataFrame, ...]], pd.DataFrame]


def run_local(
    stage: LocalCompute | FrameCompute,
    inputs: tuple[StageOutput, ...],
    kernel: LocalKernel | None = None,
) -> StageOutput:
    """Run one checked Query kernel without mutating its shared inputs."""
    if isinstance(stage, FrameCompute):
        from marivo.analysis.core.frame_data import required_attachments
        from marivo.analysis.core.frozen_calendar import (
            FrozenAttachment,
            validate_frozen_coordinates,
        )
        from marivo.analysis.materialization.query_runtime_types import effective_frame
        from marivo.analysis.methods.frame_comparison import combine_frame_data
        from marivo.analysis.methods.frame_values import evaluate_frame

        stage = effective_frame(stage, inputs)
        required = required_attachments(stage.program.output_schema)
        if isinstance(stage.program, FrameCombineProgram):
            frame, attachments = combine_frame_data(
                stage.program,
                tuple(item.table for item in inputs),
                tuple(item.frame_attachments for item in inputs),
            )
        else:
            validate_frozen_coordinates(
                stage.program.input_schema,
                inputs[0].table,
                tuple(
                    FrozenAttachment(a.name, a.schema, a.rows) for a in inputs[0].frame_attachments
                ),
            )
            frame = evaluate_frame(stage.program, tuple(item.table for item in inputs))
            attachments = tuple(a for a in inputs[0].frame_attachments if a.name in required)
        if {a.name for a in attachments} != set(required):
            raise MaterializationError(
                expected="all required frozen coordinate attachments",
                received=repr(tuple(a.name for a in attachments)),
                repair="Retain required complete coordinate data.",
                stage="frame_compute",
            )
        output = StageOutput.from_frame(
            frame,
            stage.columns,
            stage.keys,
            key_groups=tuple(
                tuple(c.slot_id for c in a.columns) for a in stage.program.output_schema.axes
            ),
            frame_attachments=attachments,
        )
        validate_frozen_coordinates(
            stage.program.output_schema,
            output.table,
            tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in attachments),
        )
        return output
    if stage.fused_steps:
        from marivo.analysis.materialization.query_runtime_types import effective_local

        output = inputs[0]
        for member in stage.fused_steps:
            output = run_local(effective_local(member, (output,)), (output,))
        return output
    expected_kernel = "query.result@v1" if stage.result is not None else "query.combine@v1"
    if stage.kernel != expected_kernel:
        raise MaterializationError(
            expected=f"the Query compiler's {expected_kernel} route",
            received=stage.kernel,
            repair="Construct local stages through the Query compiler.",
            stage="local_compute",
            help_target="analysis_frame.compute",
        )
    components = tuple(
        {
            component.layout.reduction.value_slot: component
            for item in inputs
            for component in item.components
        }.values()
    )
    population_frames = (
        inputs[0].population_frames
        if inputs and stage.result is not None
        else tuple(item.table for item in inputs[len(stage.program.values) :])
        if stage.program is not None
        else ()
    )
    if kernel is None:
        if stage.result is not None:
            from marivo.analysis.methods.query_values import evaluate_result

            kernel = evaluate_result
        else:
            from marivo.analysis.methods.local import query_combine

            kernel = query_combine
    with pd.option_context("mode.copy_on_write", True):
        views = tuple(item.table.copy(deep=False) for item in inputs)
        frame = kernel(stage, views)
    if not isinstance(frame, pd.DataFrame) or any(
        frame is item.table or frame is view for item, view in zip(inputs, views, strict=True)
    ):
        raise MaterializationError(
            expected=f"{stage.kernel}: a new pandas DataFrame",
            received=type(frame).__name__
            if not isinstance(frame, pd.DataFrame)
            else "an input table",
            repair="Return a new frame from the registered kernel; inputs are shared stage data.",
            stage="local_compute",
            help_target="analysis_frame.compute",
        )
    return StageOutput.from_frame(
        frame,
        stage.columns,
        stage.keys,
        layout=stage.result.output_layout if stage.result is not None else None,
        components=components if stage.program is not None else (),
        population_frames=population_frames if stage.program is not None else (),
    )
