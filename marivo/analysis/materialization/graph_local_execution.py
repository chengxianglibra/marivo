"""Local kernels over checked stage outputs from the current source invocation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias

import pandas as pd

from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    LocalCompute,
    local_steps,
    read_reduction,
    require_reduction,
)
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.compiler.query_program import (
    FrameCombineProgram,
    FrameCompareProgram,
    ResultProgram,
)
from marivo.analysis.core.artifact_data import OutputAttachment
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    StageOutput,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.query_components import AttributeRootInputs

LocalKernel: TypeAlias = Callable[[LocalCompute, tuple[pd.DataFrame, ...]], pd.DataFrame]


def run_local(
    stage: LocalCompute | FrameCompute,
    inputs: tuple[StageOutput, ...],
    kernel: LocalKernel | None = None,
) -> StageOutput:
    """Run one checked Query kernel without mutating its shared inputs."""
    attribute_inputs: tuple[AttributeRootInputs, ...] = ()
    if isinstance(stage, FrameCompute):
        from marivo.analysis.core.frame_data import required_attachments
        from marivo.analysis.core.frozen_calendar import (
            FrozenAttachment,
            validate_frozen_coordinates,
        )
        from marivo.analysis.materialization.query_runtime_types import effective_frame
        from marivo.analysis.methods.frame_comparison import combine_frame_data, compare_frame_data
        from marivo.analysis.methods.frame_values import evaluate_frame

        stage = effective_frame(stage, inputs)
        required = required_attachments(stage.program.output_schema)
        from marivo.analysis.core.attribute import (
            AttributeContributionProgram,
            AttributeInputProgram,
            AttributeMethodPayload,
            AttributeProjectProgram,
        )
        from marivo.analysis.methods.frame_attribute import (
            attribute_input_frame_data,
            project_attribute_parents,
        )

        attribute_methods: tuple[AttributeMethodPayload, ...] = ()
        attachments: tuple[OutputAttachment, ...]
        from marivo.analysis.core.frame_attribute_merge import AttributeMergeProgram

        if isinstance(stage.program, AttributeMergeProgram):
            from marivo.analysis.methods.frame_attribute_merge import merge_attribute_data

            frame, attachments = merge_attribute_data(stage.program, inputs)
        elif isinstance(stage.program, AttributeContributionProgram):
            from marivo.analysis.methods.frame_attribute_contribution import (
                attribute_contribution_data,
            )

            frame, payload = attribute_contribution_data(stage.program, inputs)
            attachments = inputs[0].frame_attachments
            attribute_methods = (payload,)
        elif isinstance(stage.program, AttributeProjectProgram):
            frame = project_attribute_parents(
                stage.program, inputs[0].table, inputs[0].frame_attachments
            )
            attachments = ()
        elif isinstance(stage.program, AttributeInputProgram):
            from dataclasses import replace

            from marivo.analysis.materialization.query_components import (
                complete_attribute_parent_domain,
            )

            parent, left, right, left_projection, right_projection = inputs
            left = replace(
                left,
                attribute_inputs=tuple(
                    complete_attribute_parent_domain(root, left_projection)
                    for root in left.attribute_inputs
                ),
            )
            right = replace(
                right,
                attribute_inputs=tuple(
                    complete_attribute_parent_domain(root, right_projection)
                    for root in right.attribute_inputs
                ),
            )
            inputs = (parent, left, right, left_projection, right_projection)
            frame, attachments = attribute_input_frame_data(stage.program, inputs)
            attribute_inputs = tuple(
                {
                    root.stage_id: root
                    for endpoint in (left, right)
                    for root in endpoint.attribute_inputs
                }.values()
            )
        elif isinstance(stage.program, FrameCompareProgram):
            frame, attachments, resolved_schema = compare_frame_data(
                stage.program,
                tuple(item.table for item in inputs),
                tuple(item.frame_attachments for item in inputs),
            )
        elif isinstance(stage.program, FrameCombineProgram):
            frame, attachments, resolved_schema = combine_frame_data(
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
        if isinstance(stage.program, (FrameCompareProgram, FrameCombineProgram)):
            from dataclasses import replace

            stage = replace(stage, program=replace(stage.program, output_schema=resolved_schema))
            required = required_attachments(resolved_schema)
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
            attribute_methods=attribute_methods,
            attribute_inputs=attribute_inputs,
            frame_schema=stage.program.output_schema,
        )
        validate_frozen_coordinates(
            stage.program.output_schema,
            output.table,
            tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in attachments),
        )
        return output
    if local_steps(stage):
        from marivo.analysis.materialization.query_runtime_types import effective_local

        output = inputs[0]
        for member in local_steps(stage):
            output = run_local(effective_local(member, (output,)), (output,))
        return output
    components = tuple(
        {
            component.layout.reduction.value_slot: component
            for item in inputs
            for component in item.components
        }.values()
    )
    population_frames = (
        inputs[0].population_frames
        if inputs and isinstance(stage.program, ResultProgram)
        else tuple(item.table for item in inputs[len(stage.program.values) :])
        if isinstance(stage.program, FormulaProgram)
        else ()
    )
    if kernel is None:
        if isinstance(stage.program, ResultProgram):
            from marivo.analysis.methods.query_values import evaluate_result

            kernel = evaluate_result
        else:
            from marivo.analysis.methods.local import query_combine

            kernel = query_combine
    with pd.option_context("mode.copy_on_write", True):
        views = tuple(item.table.copy(deep=False) for item in inputs)
        if isinstance(stage.program, FormulaProgram) and stage.attribute is not None:
            selected_views = list(views)
            for index, binding in enumerate(stage.program.values):
                native = next(
                    (
                        native
                        for root in inputs[index].attribute_inputs
                        for native in root.layout.native_formulas
                        if native.column.slot_id == binding.column.slot_id
                    ),
                    None,
                )
                leaf = next(
                    leaf
                    for root in inputs[index].attribute_inputs
                    for leaf in root.layout.leaves
                    if (native is not None and leaf.use == native.use)
                    or (
                        read_reduction(leaf.read) is not None
                        and require_reduction(leaf.read).value_slot == binding.column.slot_id
                    )
                )
                reduction = read_reduction(leaf.read)
                assert reduction is not None
                view = views[index]
                if reduction.window_key is not None:
                    slot = next(
                        slot
                        for slot, calendar in reduction.calendars
                        if calendar == reduction.calendar
                    )
                    view = view.loc[view[slot] == reduction.window_key]
                names = tuple(
                    dict.fromkeys(
                        (
                            *binding.keys,
                            binding.column.slot_id,
                            *(name for name, _, _ in binding.checks),
                            *(
                                name
                                for name in ("__numeric_present", "__sample_present")
                                if name in view.columns
                            ),
                        )
                    )
                )
                selected_views[index] = view.loc[:, list(names)].copy(deep=False)
            views = tuple(selected_views)
        if stage.attribute is not None:
            from marivo.analysis.core.attribute import AttributeFormulaContext
            from marivo.analysis.methods.frame_attribute import attribute_parent_mapping
            from marivo.analysis.methods.local import query_combine

            formula_binding = stage.attribute
            context = AttributeFormulaContext(
                formula_binding.uses,
                tuple(c for c in stage.columns if c.slot_id in stage.keys),
                formula_binding.source_parent_columns,
                tuple(c for a in formula_binding.parent_schema.axes for c in a.columns),
                tuple(
                    c
                    for c in stage.columns
                    if c.slot_id in stage.keys
                    and c.slot_id not in {p.slot_id for p in formula_binding.source_parent_columns}
                ),
                attribute_parent_mapping(
                    formula_binding.parent_schema,
                    inputs[formula_binding.projection_input_index],
                    formula_binding.source_parent_columns,
                ),
            )
            frame = query_combine(stage, views, attribute=context)
        else:
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
    if isinstance(stage.program, FormulaProgram):
        # Formula values occupy the ordered input prefix, including repeated
        # aliases of one bundled root. Domain/projection dependencies own no mass.
        attribute_inputs = tuple(
            {
                root.stage_id: root
                for source in inputs[: len(stage.program.values)]
                for root in source.attribute_inputs
            }.values()
        )
    return StageOutput.from_frame(
        frame,
        stage.columns,
        stage.keys,
        layout=stage.program.output_layout if isinstance(stage.program, ResultProgram) else None,
        components=components if isinstance(stage.program, FormulaProgram) else (),
        population_frames=population_frames if isinstance(stage.program, FormulaProgram) else (),
        attribute_inputs=attribute_inputs,
    )
