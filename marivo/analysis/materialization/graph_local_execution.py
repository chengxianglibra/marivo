"""Local kernels over checked stage outputs from the current source invocation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import pandas as pd

from marivo.analysis.compiler.graph_plan import LocalCompute
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    StageOutput,
)

LocalKernel: TypeAlias = Callable[[LocalCompute, tuple[pd.DataFrame, ...]], pd.DataFrame]


def run_local(
    stage: LocalCompute, inputs: tuple[StageOutput, ...], kernel: LocalKernel | None = None
) -> StageOutput:
    """Run one checked Query kernel without mutating its shared inputs."""
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
            help_target="Query.execute",
        )
    from marivo._definition.query import ComponentKeys, DrillValues, RollupValues, ShareValues

    if stage.result is not None and isinstance(
        stage.result.operation, (DrillValues, ComponentKeys)
    ):
        from marivo.analysis.materialization.drill_execution import run_drill

        return run_drill(stage, inputs)

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
    if stage.result is not None and isinstance(stage.result.operation, (RollupValues, ShareValues)):
        from marivo.analysis.compiler.original import require_rollup
        from marivo.analysis.methods.query_original import rollup_components, share_frame

        program = stage.result
        assert program.original is not None
        operation = program.operation
        receiver = inputs[0].table
        key_columns = tuple(column for column in program.columns if column.slot_id in program.keys)
        if isinstance(operation, RollupValues):
            assert program.output_layout is not None
            require_rollup(program.original, program.axes, program.output_layout.axes)
            mapping = receiver.loc[:, list(program.keys)].copy()
            calendar_maps = dict(operation.calendar_maps)
            target_index = 0
            for axis_index, position in enumerate(operation.positions):
                for slot in program.key_groups[position]:
                    mapping[f"__target.{target_index}"] = (
                        receiver[slot].map(dict(enumerate(calendar_maps[axis_index])))
                        if axis_index in calendar_maps
                        else receiver[slot]
                    )
                    target_index += 1
            frame, components = rollup_components(
                program.original,
                inputs[0].components,
                program.keys,
                stage.keys,
                mapping,
                tuple(column for column in stage.columns if column.slot_id not in stage.keys),
                population_frames=population_frames,
                source_columns=key_columns,
                target_columns=tuple(
                    column for column in stage.columns if column.slot_id in stage.keys
                ),
            )
        else:
            assert isinstance(operation, ShareValues)
            partitions = tuple(
                slot for position in operation.partitions for slot in program.key_groups[position]
            )
            partition_mapping = receiver.loc[:, list(program.keys)].copy()
            offset = 0
            for position, axis_index in enumerate(operation.partitions):
                group = program.key_groups[axis_index]
                calendar_map = dict(operation.calendar_maps).get(position)
                for slot in group:
                    partition_mapping[f"__target.{offset}"] = (
                        receiver[slot].map(dict(enumerate(calendar_map)))
                        if calendar_map is not None
                        else receiver[slot]
                    )
                    offset += 1
            frame = share_frame(
                program.original,
                inputs[0].components,
                receiver,
                program.keys,
                partitions,
                operation.value_index,
                stage.columns[len(program.columns)].slot_id,
                operation.zero_denominator,
                population_frames=population_frames,
                source_columns=key_columns,
                partition_mapping=partition_mapping,
                merged_axes=tuple(
                    axis
                    for index, axis in enumerate(program.axes)
                    if index not in operation.partitions
                    or any(
                        operation.partitions[position] == index
                        for position, _ in operation.calendar_maps
                    )
                ),
            )
        return StageOutput.from_frame(
            frame.loc[:, [column.slot_id for column in stage.columns]],
            stage.columns,
            stage.keys,
            layout=program.output_layout,
            components=components,
            population_frames=population_frames,
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
            help_target="Query.execute",
        )
    return StageOutput.from_frame(
        frame,
        stage.columns,
        stage.keys,
        layout=stage.result.output_layout if stage.result is not None else None,
        components=components
        if stage.program is not None
        or (stage.result is not None and stage.result.original is not None)
        else (),
        population_frames=population_frames
        if stage.program is not None
        or (stage.result is not None and stage.result.original is not None)
        else (),
    )
