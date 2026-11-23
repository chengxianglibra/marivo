"""Local kernels over checked stage outputs from the current source invocation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, TypeAlias

import pandas as pd

from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.query_program import (
    FrameCombineProgram,
    FrameCompareProgram,
    FrameProgram,
    ResultProgram,
)
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    LocalCompute,
    local_steps,
    read_reduction,
    require_reduction,
)
from marivo.analysis.core.attachments import OutputAttachment
from marivo.analysis.core.attribute import (
    AttributeContributionProgram,
    AttributeFormulaContext,
    AttributeInputProgram,
    AttributeMethodPayload,
    AttributeProjectProgram,
)
from marivo.analysis.core.attribute_game import AttributeGameProgram
from marivo.analysis.core.frame_data import required_attachments
from marivo.analysis.core.frozen_calendar import FrozenAttachment, validate_frozen_coordinates
from marivo.analysis.materialization.query_components import complete_attribute_parent_domain
from marivo.analysis.materialization.query_runtime_types import effective_frame, effective_local
from marivo.analysis.methods import (
    frame_attribute_contribution,
    query_values,
)
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_attribute import (
    attribute_input_frame_data,
    attribute_parent_mapping,
    project_attribute_parents,
)
from marivo.analysis.methods.frame_attribute_merge import merge_attribute_data
from marivo.analysis.methods.frame_attribute_shapley import attribute_game_data
from marivo.analysis.methods.frame_comparison import combine_frame_data, compare_frame_data
from marivo.analysis.methods.local import query_combine
from marivo.analysis.methods.query_values import evaluate_result
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_attribute_merge import AttributeMergeProgram
from marivo.analysis.model.frame_contracts import CheckReceipt
from marivo.analysis.model.optimization_policy import (
    DEFAULT_OPTIMIZATION_POLICY,
    OptimizationPolicy,
)
from marivo.analysis.model.unique_keys import UniqueKeys

if TYPE_CHECKING:
    from marivo.analysis.methods.carriers.stage_components import AttributeRootInputs

LocalKernel: TypeAlias = Callable[[LocalCompute, tuple[pd.DataFrame, ...]], pd.DataFrame]


def run_local(
    stage: LocalCompute | FrameCompute,
    inputs: tuple[StageOutput, ...],
    kernel: LocalKernel | None = None,
    *,
    report_domain: pd.DataFrame | None = None,
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None = None,
    policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY,
) -> StageOutput:
    """Run one checked Query kernel without mutating its shared inputs."""
    if isinstance(stage, FrameCompute):
        return _run_frame(stage, inputs, policy)
    attribute_inputs: tuple[AttributeRootInputs, ...] = ()
    if local_steps(stage):
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
        kernel = evaluate_result if isinstance(stage.program, ResultProgram) else query_combine
    with pd.option_context("mode.copy_on_write", True):
        views, view_keys = _kernel_input_views(stage, inputs)
        if stage.attribute is not None:
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
            frame = query_combine(
                stage,
                views,
                attribute=context,
                unique_keys=tuple(view_keys),
            )
        elif isinstance(stage.program, FormulaProgram) and kernel is query_combine:
            frame = query_combine(
                stage,
                views,
                unique_keys=tuple(view_keys),
                report_domain=report_domain,
                sample_domains=sample_domains,
            )
        else:
            frame = kernel(stage, views)
    _validate_kernel_output(frame, stage, inputs, views)
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
        attribute_state_captures=tuple(
            {
                capture.stage_id: capture
                for source in inputs
                for capture in source.attribute_state_captures
            }.values()
        ),
        unique_keys=stage.unique_keys,
        attribute_frequencies=tuple(
            {
                (capture.stage_id, capture.value_slot): capture
                for source in inputs
                for capture in source.attribute_frequencies
            }.values()
        ),
    )


def _run_frame(
    stage: FrameCompute, inputs: tuple[StageOutput, ...], policy: OptimizationPolicy
) -> StageOutput:
    attribute_inputs: tuple[AttributeRootInputs, ...] = ()
    if stage.filter_fusion is None and stage.select_fusion is None:
        stage = effective_frame(stage, inputs)
    # The schedule admits a fused program only on its exact compiled input schema.
    resolved_schema = stage.program.output_schema
    required = required_attachments(resolved_schema)

    attribute_methods: tuple[AttributeMethodPayload, ...] = ()
    attachments: tuple[OutputAttachment, ...]

    if isinstance(stage.program, AttributeGameProgram):
        frame, payload = attribute_game_data(
            stage.program, inputs, memoize_values=policy.allows("shapley_summary_state")
        )
        attachments = _retained_input_attachments(inputs, required)
        attribute_methods = (payload,)
    elif isinstance(stage.program, AttributeMergeProgram):
        frame, attachments = merge_attribute_data(stage.program, inputs)
    elif isinstance(stage.program, AttributeContributionProgram):
        frame, payload = frame_attribute_contribution.attribute_contribution_data(
            stage.program, inputs
        )
        attachments = _retained_input_attachments(inputs, required)
        attribute_methods = (payload,)
    elif isinstance(stage.program, AttributeProjectProgram):
        frame = project_attribute_parents(
            stage.program,
            inputs[0].table,
            inputs[0].frame_attachments,
            unique_keys=inputs[0].unique_keys,
        )
        attachments = ()
    elif isinstance(stage.program, AttributeInputProgram):
        frame, attachments, attribute_inputs = _prepare_attribute_input_frame(stage.program, inputs)
    elif isinstance(stage.program, FrameCompareProgram):
        frame, attachments, resolved_schema = compare_frame_data(
            stage.program,
            tuple(item.table for item in inputs),
            tuple(item.frame_attachments for item in inputs),
            unique_keys=tuple(item.unique_keys for item in inputs),
        )
    elif isinstance(stage.program, FrameCombineProgram):
        frame, attachments, resolved_schema = combine_frame_data(
            stage.program,
            tuple(item.table for item in inputs),
            tuple(item.frame_attachments for item in inputs),
            unique_keys=tuple(item.unique_keys for item in inputs),
        )
    else:
        assert isinstance(stage.program, FrameProgram)
        frame = _evaluate_unary_frame(stage.program, stage, inputs[0], policy)
        attachments = tuple(a for a in inputs[0].frame_attachments if a.name in required)
    if isinstance(stage.program, (FrameCompareProgram, FrameCombineProgram)):
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
        unique_keys=stage.unique_keys,
    )
    validate_frozen_coordinates(
        stage.program.output_schema,
        output.table,
        tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in attachments),
        unique_keys=output.unique_keys,
    )
    if isinstance(stage.program, AttributeInputProgram):
        return replace(
            output,
            receipts=(CheckReceipt("attribute.complete_key_mapping", stage.stage_id),),
        )
    return output


def _evaluate_unary_frame(
    program: FrameProgram, stage: FrameCompute, source: StageOutput, policy: OptimizationPolicy
) -> pd.DataFrame:
    projection = stage.select_fusion
    schema = (
        program.input_schema if projection is None else projection.steps[0].program.input_schema
    )
    validate_frozen_coordinates(
        schema,
        source.table,
        tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in source.frame_attachments),
        unique_keys=source.unique_keys,
    )
    tables = (source.table,)
    evaluate = (
        query_values.evaluate_frame
        if policy.allows("frame_evaluation_cache")
        else partial(query_values.evaluate_frame, memoize=False)
    )
    if projection is not None:
        return evaluate(program, tables, select_steps=projection.steps)
    if stage.filter_fusion is not None:
        return evaluate(program, tables, filter_steps=stage.filter_fusion.steps)
    return evaluate(program, tables)


def _retained_input_attachments(
    inputs: tuple[StageOutput, ...], required: tuple[str, ...]
) -> tuple[OutputAttachment, ...]:
    retained: dict[str, OutputAttachment] = {}
    for source in inputs:
        for attachment in source.frame_attachments:
            if attachment.name not in required:
                continue
            previous = retained.get(attachment.name)
            if previous is None:
                retained[attachment.name] = attachment
    if set(retained) != set(required):
        raise MaterializationError(
            expected="all required frozen coordinate attachments",
            received=repr(tuple(retained)),
            repair="Retain required complete coordinate data.",
            stage="frame_compute",
        )
    return tuple(retained[name] for name in required)


def _prepare_attribute_input_frame(
    program: AttributeInputProgram, inputs: tuple[StageOutput, ...]
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...], tuple[AttributeRootInputs, ...]]:
    parent, left, right, left_projection, right_projection = inputs[:5]
    roles: dict[str, list[AttributeRootInputs]] = {"left": [], "right": []}
    for read_binding in program.reads:
        actual = inputs[read_binding.input_index].attribute_inputs
        for root in actual:
            if (
                root.layout.scope != read_binding.scope
                or tuple(leaf.use for leaf in root.layout.leaves) != read_binding.uses
            ):
                raise MaterializationError(
                    expected="the exact typed Attribute role dependency",
                    received=root.stage_id,
                    repair="Retain each planned role and complete source use until input assembly.",
                    stage="attribute_inputs",
                    help_target="analysis_frame.compute",
                )
            projection = left_projection if read_binding.side == "left" else right_projection
            roles[read_binding.side].append(complete_attribute_parent_domain(root, projection))
    left = replace(left, attribute_inputs=tuple(roles["left"]))
    right = replace(right, attribute_inputs=tuple(roles["right"]))
    inputs = (parent, left, right, left_projection, right_projection)
    frame, attachments = attribute_input_frame_data(program, inputs)
    attribute_inputs = tuple(
        {root.stage_id: root for root in (*left.attribute_inputs, *right.attribute_inputs)}.values()
    )
    return frame, attachments, attribute_inputs


def _kernel_input_views(
    stage: LocalCompute, inputs: tuple[StageOutput, ...]
) -> tuple[tuple[pd.DataFrame, ...], list[UniqueKeys]]:
    views = tuple(item.table.copy(deep=False) for item in inputs)
    view_keys = [item.unique_keys for item in inputs]
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
            fixed_slots: tuple[str, ...] = ()
            if reduction.window_key is not None:
                slot = next(
                    slot for slot, calendar in reduction.calendars if calendar == reduction.calendar
                )
                view = view.loc[view[slot] == reduction.window_key]
                fixed_slots = (slot,)
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
            view_keys[index] = (
                view_keys[index]
                .project(tuple(str(c) for c in views[index].columns))
                .fixed(names, fixed_slots)
            )
        views = tuple(selected_views)

    return views, view_keys


def _validate_kernel_output(
    frame: object,
    stage: LocalCompute,
    inputs: tuple[StageOutput, ...],
    views: tuple[pd.DataFrame, ...],
) -> None:
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
