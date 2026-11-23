"""Prepared Query source execution through issued Ibis reads and pandas stages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

import ibis.expr.types as ir
import pandas as pd

from marivo.analysis.compiler.query_stages import (
    ActualWindowRead,
    AttributeCoalitionRead,
    AttributeRead,
    SampleWindowRead,
    local_steps,
    read_attribute,
    read_members,
    require_attribute,
    require_reduction,
)
from marivo.analysis.materialization.attribute_native_execution import (
    prepare_native_game,
    run_native_game,
)

if TYPE_CHECKING:
    from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
    from marivo.analysis.materialization.attribute_native_execution import (
        NativeCache,
        PreparedNativeGame,
    )

from marivo.analysis.compiler.attribute_inputs import (
    input_failure,
)
from marivo.analysis.compiler.frame_filter_fusion import frame_filter_steps
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    QueryStage,
    SourceReadStage,
)
from marivo.analysis.core.attribute import AttributeParentContext
from marivo.analysis.core.frame_data import CompletedFrameData
from marivo.analysis.materialization import (
    graph_execution,
    graph_local_execution,
    query_parameters,
    source_reads,
)
from marivo.analysis.materialization.actual_windows import prepare_actual_window_read
from marivo.analysis.materialization.native_query_checks import (
    finish_population_checks,
)
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_runtime_types import effective_local, source_value_keys
from marivo.analysis.materialization.sample_windows import prepare_sample_window_read
from marivo.analysis.methods.carriers.stages import StageOutput, source_columns
from marivo.analysis.methods.frame_attribute import attribute_parent_mapping
from marivo.analysis.model.domain_captures import DomainPreparationError
from marivo.analysis.model.errors import reject
from marivo.analysis.model.optimization_policy import (
    DEFAULT_OPTIMIZATION_POLICY,
    OptimizationPolicy,
)
from marivo.datasource.spi import (
    SourceSession,
)


def _prepare_dependent_read(
    stage: SourceReadStage,
    lowered: LoweredRead,
    owner: SourceSession,
    inputs: tuple[StageOutput, ...],
    parameter_relations: dict[tuple[SourceSession, str], ir.Table],
    policy: OptimizationPolicy,
) -> tuple[SourceReadStage, LoweredRead]:
    stage, request = finish_population_checks(stage, lowered, owner.provider.name, policy=policy)
    expression = request.expression
    for parameter in stage.parameters:
        source = inputs[stage.inputs.index(parameter.stage_id)]
        try:
            relation = None
            if owner.provider.query_policy.owns_arrow_key_relations and parameter.columns:
                key = (owner, parameter.placeholder)
                relation = parameter_relations.get(key)
                if relation is None:
                    relation = owner.bind_key_relation(
                        query_parameters.parameter_data(parameter.columns, source)
                    )
                    parameter_relations[key] = relation
            expression = query_parameters.instantiate(
                expression,
                parameter.placeholder,
                parameter.columns,
                source,
                relation=relation,
                provider=owner.provider.name,
                source=owner,
            )
        except (
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
            NotImplementedError,
        ) as error:
            raise DomainPreparationError(
                "query.population_parameters",
                "prepare",
                "a native typed complete-tuple Population parameter",
                str(error),
                "Reduce the explicit domain or use a datasource/type with an admitted native expression route.",
            ) from error
    request = replace(request, expression=expression)
    if read_attribute(stage) is not None:
        binding = stage.attribute_parent
        if binding is None:
            input_failure(
                "missing_component_proof",
                "the Attribute source lacks its exact parent projection dependency",
            )
        layout = require_attribute(stage)
        parent_columns = {column.slot_id: column for column in layout.columns}
        projection = inputs[binding.projection_input_index]
        parent_context = AttributeParentContext(
            tuple(column for axis in binding.parent_schema.axes for column in axis.columns),
            attribute_parent_mapping(
                binding.parent_schema,
                projection,
                tuple(parent_columns[name] for name in layout.parent_keys),
            ),
        )
        stage = replace(
            stage,
            program=AttributeRead(
                replace(layout, parent_context=parent_context),
                require_reduction(stage),
                read_members(stage),
            ),
        )
    return stage, request


def _evaluate_query_local(
    stage: LocalCompute,
    inputs: tuple[StageOutput, ...],
    lowered: LoweredQuery,
    report_domain: pd.DataFrame | None,
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None,
    observe_local: Callable[[LocalCompute, StageOutput], None] | None,
) -> StageOutput:
    stage = effective_local(stage, inputs) if not local_steps(stage) else stage
    output = (
        graph_local_execution.run_local(
            stage, inputs, report_domain=report_domain, sample_domains=sample_domains
        )
        if (report_domain is not None or sample_domains is not None)
        and stage.stage_id == lowered.plan.publish
        else graph_local_execution.run_local(stage, inputs)
    )
    if observe_local is not None:
        observe_local(stage, output)
    return output


def _execute_dependent_query(
    stage: SourceReadStage,
    inputs: tuple[StageOutput, ...],
    prepared: PreparedSources | None,
    prepared_by_stage: Mapping[str, PreparedSources] | None,
    native_prepared: Mapping[str, PreparedNativeGame] | None,
    native_cache: NativeCache,
    lowered: LoweredQuery,
    parameter_relations: dict[tuple[SourceSession, str], ir.Table],
    value_keys: Mapping[str, tuple[str, ...]],
    observe_read: Callable[[SourceReadStage, LoweredRead, StageOutput], None] | None,
    observe_frame: Callable[[FrameCompute | SourceReadStage, StageOutput], None] | None,
) -> StageOutput:
    if isinstance(stage.program, AttributeCoalitionRead):
        assert native_prepared is not None
        output = run_native_game(
            stage, inputs, native_prepared[stage.stage_id], native_cache, observe_read
        )
        if observe_frame is not None:
            observe_frame(stage, output)
        return output
    selected = prepared if prepared_by_stage is None else prepared_by_stage.get(stage.stage_id)
    assert selected is not None
    if isinstance(stage.program, (SampleWindowRead, ActualWindowRead)):
        nested = (
            prepare_sample_window_read(selected, stage, stage.program, inputs)
            if isinstance(stage.program, SampleWindowRead)
            else prepare_actual_window_read(selected, stage, stage.program, inputs)
        )
        if isinstance(nested, StageOutput):
            return nested
        output = execute_numeric_query(
            nested.sources,
            nested.lowered,
            observe_read=observe_read,
            report_domain=nested.report_domain,
            sample_domains=nested.sample_domains,
            frame_key_inputs=nested.frame_key_inputs,
            checked_inputs=nested.checked_inputs,
        )
        return replace(output, receipts=(*nested.receipts, *output.receipts))
    owner = selected.sources.get(stage.source_id, selected.source)
    stage, request = _prepare_dependent_read(
        stage,
        lowered.read(stage.stage_id),
        owner,
        inputs,
        parameter_relations,
        lowered.plan.optimization_policy,
    )
    output = source_reads.read_stage(
        selected, stage, request, value_keys=value_keys.get(stage.stage_id, ())
    )
    if observe_read is not None:
        observe_read(stage, request, output)
    return output


def _evaluate_fixed_query_input(
    stage: FrameInput, read_fixed: Callable[[FrameInput], CompletedFrameData] | None
) -> StageOutput:
    if read_fixed is None:
        reject(
            "an available complete fixed-input reader",
            stage.input_id,
            "Bind memory data or the qualified stored reader.",
            "frame.input",
        )
    data = read_fixed(stage)
    if data.schema != stage.schema:
        reject(
            "the exact fixed input schema",
            repr(data.schema),
            "Use this input's complete descriptor.",
            "frame.input",
        )
    return StageOutput.from_completed(data)


def _finish_query_output(
    stage: QueryStage,
    output: StageOutput,
    source_outputs: Mapping[str, str] | None,
    adapt_source: Callable[[str, StageOutput], StageOutput] | None,
    transport: Callable[[str, StageOutput], StageOutput],
    retain_inputs: dict[str, StageOutput] | None,
) -> StageOutput:
    if source_outputs is not None and stage.stage_id in source_outputs:
        if adapt_source is None:
            reject(
                "a complete source-to-data boundary adapter",
                stage.stage_id,
                "Freeze the explicit source output.",
                "frame.source",
            )
        if output.frame_schema is None:
            output = adapt_source(source_outputs[stage.stage_id], output)
    output = transport(stage.stage_id, output)
    if retain_inputs is not None:
        if isinstance(stage, FrameInput):
            retain_inputs[stage.input_id] = output
        if source_outputs is not None and stage.stage_id in source_outputs:
            retain_inputs[source_outputs[stage.stage_id]] = output
    return output


def _frame_kernel(
    policy: OptimizationPolicy,
) -> Callable[[FrameCompute, tuple[StageOutput, ...]], StageOutput]:
    run = graph_local_execution.run_local
    return run if policy == DEFAULT_OPTIMIZATION_POLICY else partial(run, policy=policy)


def execute_numeric_query(
    prepared: PreparedSources | None,
    lowered: LoweredQuery,
    *,
    observe_read: Callable[[SourceReadStage, LoweredRead, StageOutput], None] | None = None,
    observe_local: Callable[[LocalCompute, StageOutput], None] | None = None,
    prepared_by_stage: Mapping[str, PreparedSources] | None = None,
    read_fixed: Callable[[FrameInput], CompletedFrameData] | None = None,
    adapt_source: Callable[[str, StageOutput], StageOutput] | None = None,
    source_outputs: Mapping[str, str] | None = None,
    observe_frame: Callable[[FrameCompute | SourceReadStage, StageOutput], None] | None = None,
    native_prepared: Mapping[str, PreparedNativeGame] | None = None,
    frame_key_inputs: Mapping[str, StageOutput] | None = None,
    report_domain: pd.DataFrame | None = None,
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None = None,
    checked_inputs: Mapping[str, StageOutput] | None = None,
    retain_inputs: dict[str, StageOutput] | None = None,
) -> StageOutput:
    """Execute the fixed numeric schedule and return invocation-local pandas data."""
    required_by_stage = {required.stage_id: required for required in lowered.plan.required_slots}
    value_keys = source_value_keys(lowered.plan)
    parameter_relations: dict[tuple[SourceSession, str], ir.Table] = {}
    native_cache: NativeCache = {}
    frame_kernel = _frame_kernel(lowered.plan.optimization_policy)

    def transport(stage_id: str, output: StageOutput) -> StageOutput:
        if not lowered.plan.required_slots:
            return output
        required = required_by_stage[stage_id]
        columns = tuple(column for column in output.columns if column.slot_id in required.columns)
        return replace(
            output,
            table=output.table.loc[:, [column.slot_id for column in columns]],
            columns=columns,
            layout=replace(output.layout, columns=columns) if output.layout is not None else None,
            components=tuple(
                component
                for component in output.components
                if component.layout.reduction.value_slot in required.components
            ),
            population_frames=output.population_frames if required.retain_populations else (),
        )

    def read(stage: SourceReadStage) -> StageOutput:
        selected = prepared if prepared_by_stage is None else prepared_by_stage.get(stage.stage_id)
        if selected is None:
            reject(
                "prepared source connections",
                stage.stage_id,
                "Prepare the source before execution.",
                "analysis.query_source",
            )
        request = lowered.read(stage.stage_id)
        output = source_reads.read_stage(
            selected, stage, request, value_keys=value_keys.get(stage.stage_id, ())
        )
        if observe_read is not None:
            observe_read(stage, request, output)
        return output

    def local(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        return _evaluate_query_local(
            stage, inputs, lowered, report_domain, sample_domains, observe_local
        )

    def dependent(stage: SourceReadStage, inputs: tuple[StageOutput, ...]) -> StageOutput:
        return _execute_dependent_query(
            stage,
            inputs,
            prepared,
            prepared_by_stage,
            native_prepared,
            native_cache,
            lowered,
            parameter_relations,
            value_keys,
            observe_read,
            observe_frame,
        )

    def fixed(stage: FrameInput) -> StageOutput:
        return _evaluate_fixed_query_input(stage, read_fixed)

    def frame(stage: FrameCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        steps = (
            frame_filter_steps(stage, inputs[0].frame_schema) if stage.filter_fusion else (stage,)
        )
        for step in steps:
            output = frame_kernel(step, inputs)
            if observe_frame is not None:
                observe_frame(step, output)
            inputs = (output,)
        return inputs[0]

    def finish(stage: QueryStage, output: StageOutput) -> StageOutput:
        return _finish_query_output(
            stage, output, source_outputs, adapt_source, transport, retain_inputs
        )

    result = graph_execution.run_query_plan(
        lowered.plan,
        read,
        local,
        dependent,
        fixed=fixed,
        frame=frame,
        finish=finish,
        frame_keys=(lambda stage: frame_key_inputs[stage.stage_id])
        if frame_key_inputs is not None
        else None,
        available=checked_inputs,
    )
    planned_output = next(
        stage for stage in lowered.plan.stages if stage.stage_id == lowered.plan.publish
    )
    source_columns(result.table, planned_output.columns, result.keys)
    return replace(result, key_groups=lowered.plan.output_groups)


def run_frame_plan(
    compiled: CompiledFramePlan[StageOutput],
    *,
    source_prepared: Mapping[str, PreparedSources],
    read_fixed: Callable[[FrameInput], CompletedFrameData],
    adapt_source: Callable[[str, StageOutput], StageOutput],
    observe_read: Callable[[SourceReadStage, LoweredRead, StageOutput], None] | None = None,
    observe_local: Callable[[LocalCompute | FrameCompute | SourceReadStage, StageOutput], None]
    | None = None,
    completed_frames: Mapping[str, StageOutput] | None = None,
    retain_inputs: dict[str, StageOutput] | None = None,
) -> StageOutput:
    """Bind explicit inputs to the existing one-invocation numeric scheduler."""

    native = {
        stage.stage_id: prepare_native_game(stage, source_prepared)
        for stage in compiled.lowered.plan.stages
        if isinstance(stage, SourceReadStage) and isinstance(stage.program, AttributeCoalitionRead)
    }
    prepared = {
        stage_id: source_prepared[input_id]
        for stage_id, input_id in compiled.source_stages
        if input_id in source_prepared
    }
    completed = {} if completed_frames is None else completed_frames
    checked_inputs = {
        **dict(compiled.checked_inputs),
        **{
            stage.stage_id: completed[stage.input_id]
            for stage in compiled.lowered.plan.stages
            if isinstance(stage, FrameInput) and stage.input_id in completed
        },
        **{
            stage_id: completed[input_id]
            for stage_id, input_id in compiled.source_outputs
            if input_id in completed
        },
    }
    return execute_numeric_query(
        None,
        compiled.lowered,
        observe_read=observe_read,
        observe_local=observe_local,
        prepared_by_stage=prepared,
        read_fixed=read_fixed,
        adapt_source=adapt_source,
        source_outputs=dict(compiled.source_outputs),
        observe_frame=observe_local,
        native_prepared=native,
        checked_inputs=checked_inputs,
        retain_inputs=retain_inputs,
    )
