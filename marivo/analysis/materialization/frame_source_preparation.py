"""Prepare captured Frame sources under the caller-owned source context stack."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from marivo.analysis.compiler.frame_plan import (
    CompiledSourceFrame,
    frame_expression_schema,
)
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import (
    QueryPlan,
    QueryStage,
    SourceRead,
    SourceSchema,
    plan_query,
)
from marivo.analysis.compiler.optimization import InvocationMemo, optimize_query
from marivo.analysis.compiler.queries import endpoint
from marivo.analysis.core.artifact_data import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    IntervalDataAxis,
)
from marivo.analysis.core.frame_audit import (
    EvaluationInput,
    FrameOperationName,
    LiteralInput,
    MemoryInput,
    SourceInput,
    StoredInput,
)
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    LiteralFrameBinding,
    MemoryFrameBacking,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_source_facts import captured_source_scopes
from marivo.analysis.core.frame_structure import PendingDataColumn, PendingDataSchema, axis_role
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_inputs import CapturedFrameInputs
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    _query_entities,
    open_query_sources,
    prepare_attribute_endpoint,
    prepare_source_free_populations,
    qualify_query_sources,
)

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


@dataclass(frozen=True, slots=True)
class PreparedFrameSources:
    """Preparation products consumed together by this invocation's schedule."""

    prepared: dict[str, PreparedSources]
    plans: dict[str, CompiledSourceFrame]
    schemas: dict[str, DataSchema]
    scopes: dict[str, tuple[str, ...]]
    inputs: tuple[EvaluationInput, ...]
    operations: tuple[FrameOperationName, ...]


def _schema(frame: AnalysisFrame, plan: QueryPlan) -> DataSchema:
    graph = (
        frame._backing.root.graph
        if isinstance(frame._backing, ExpressionFrameBacking)
        and isinstance(frame._backing.root, (SourceFrameBinding, LiteralFrameBinding))
        else None
    )
    if graph is None or not isinstance(frame._schema, PendingDataSchema):
        reject(
            "a captured pending source structure",
            frame._input_id,
            "Capture the explicit source before preparing it.",
            "frame.prepare",
        )
    layout = endpoint(graph, plan)
    columns = {column.slot_id: column for column in layout.columns}
    axes: list[DataAxis] = []
    if len(layout.key_groups) != len(frame._schema.axes):
        reject(
            "one complete prepared key group per captured axis",
            repr(layout.key_groups),
            "Preserve complete source coordinate groups.",
            "frame.prepare",
        )
    for captured, keys in zip(frame._schema.axes, layout.key_groups, strict=True):
        role = axis_role(captured)
        constraints: tuple[DataColumn | PendingDataColumn, ...] = captured.columns
        if len(keys) != len(captured.columns):
            reject(
                "every captured complete key component",
                repr(keys),
                "Preserve the full source identity.",
                "frame.prepare",
            )
        physical = tuple(
            replace(
                columns[key],
                name=constraint.name,
                nullable=False
                if role in ("identity", "calendar", "interval")
                else columns[key].nullable,
            )
            for key, constraint in zip(keys, constraints, strict=True)
        )
        if isinstance(captured, (CalendarDataAxis, IntervalDataAxis)):
            axes.append(replace(captured, columns=physical))
        else:
            axes.append(
                DataAxis(
                    captured.token,
                    physical,
                    kind="member" if role == "identity" else "field",
                    name=captured.name,
                    at=captured.at,
                )
            )
    values = tuple(
        replace(column, name=constraint.name, unit=constraint.unit)
        for column, constraint in zip(layout.values, frame._schema.values, strict=True)
    )
    schema = DataSchema(tuple(axes), values, value_paths=frame._schema.value_paths)
    return frame._schema.complete(schema)


def prepare_frame_sources(captured: CapturedFrameInputs, stack: ExitStack) -> PreparedFrameSources:
    """Open, prepare, lower and qualify sources in captured discovery order."""
    source_frames = captured.source_frames
    fixed_frames = captured.fixed_frames
    expressions = captured.expressions
    requested = captured.requested
    prepared_by_input: dict[str, PreparedSources] = {}
    source_plans: dict[str, CompiledSourceFrame] = {}
    source_schemas: dict[str, DataSchema] = {}
    scopes: dict[str, tuple[str, ...]] = {}
    key_schema_cache: dict[int, DataSchema] = {}
    inputs: list[EvaluationInput] = []
    operations = list(captured.operations)
    for input_id, current in source_frames.items():
        backing = current._backing
        assert isinstance(backing, ExpressionFrameBacking)
        binding = backing.root
        assert isinstance(binding, (SourceFrameBinding, LiteralFrameBinding))
        memo = InvocationMemo()
        prepared = None
        schemas: tuple[SourceSchema, ...] = ()
        if isinstance(binding, SourceFrameBinding):
            if input_id in captured.games:
                from marivo.analysis.errors import AnalysisStageUnavailableError
                from marivo.datasource.ir import TableSourceIR
                from marivo.semantic.validator import normalize_target_entity

                for entity in _query_entities(binding.graph, binding.state):
                    physical = normalize_target_entity(binding.state.registry, entity).source
                    if not isinstance(physical, TableSourceIR):
                        raise AnalysisStageUnavailableError(
                            stage="attribute_state_binding",
                            expected="a native physical table source for replacement-game capture",
                            received=f"{entity}: {type(physical).__name__}",
                            repair="Use a qualified native table-backed source for attribution, or inspect the original observations. No business row was read.",
                            target="analysis_frame.attribute",
                        )
            key_schemas = {
                item.input_id: frame_expression_schema(
                    expressions[item.input_id], source_schemas, memo=key_schema_cache
                )
                for item in binding.frame_key_inputs
            }
            if _query_entities(binding.graph, binding.state):
                service = stack.enter_context(binding.connection.operation())
                prepared = stack.enter_context(
                    open_query_sources(
                        service,
                        binding.graph,
                        binding.state,
                        memo=memo,
                        frame_schemas=key_schemas,
                    )
                )
                prepared_by_input[input_id] = prepared
                schemas = prepared.schemas
            else:
                schemas = prepare_source_free_populations(
                    binding.graph, binding.state, frame_schemas=key_schemas
                )
            scopes[input_id] = captured_source_scopes(binding.graph)
        if input_id in requested:
            assert prepared is not None and isinstance(binding, SourceFrameBinding)
            side, rules, mapping = requested[input_id]
            prepared, plan = prepare_attribute_endpoint(
                prepared, binding.graph, binding.state, mapping, side, rules, memo=memo
            )
            prepared_by_input[input_id] = prepared
        else:
            plan = plan_query(binding.graph, schemas, memo=memo)
            native_reads = tuple(
                replace(stage, native_relation_id=stage.stage_id)
                for stage in plan.stages
                if isinstance(stage, SourceRead)
            )
            native_value_slots: list[str] = []
            if input_id in captured.games:
                from marivo._definition.expressions import SourceAggregate
                from marivo.analysis.compiler.attribute_frequencies import (
                    frequency_read,
                    share_frequency_reads,
                )
                from marivo.analysis.compiler.graph_plan import read_reduction

                assert prepared is not None
                from marivo.analysis.errors import AnalysisStageUnavailableError
                from marivo.analysis.materialization.graph_source_execution import (
                    qualify_attribute_game_sources,
                )

                qualify_attribute_game_sources(prepared, plan)
                qualified_stages: list[QueryStage] = []
                for stage in plan.stages:
                    if not isinstance(stage, SourceRead):
                        qualified_stages.append(stage)
                        continue
                    reduction = read_reduction(stage)
                    try:
                        candidate = replace(
                            frequency_read(stage)
                            if reduction is not None
                            and isinstance(reduction.node, SourceAggregate)
                            and reduction.node.reducer in ("median", "quantile", "count_distinct")
                            else stage,
                            attribute_state=True,
                        )
                        if (
                            reduction is not None
                            and isinstance(reduction.node, SourceAggregate)
                            and reduction.node.reducer.startswith("approx_")
                        ):
                            raise AnalysisStageUnavailableError(
                                stage="attribute_state_binding",
                                expected="a qualified mergeable native approximation state",
                                received=reduction.node.reducer,
                                repair="Use original native coalition queries.",
                                target="analysis_frame.attribute",
                            )
                        lower_query(replace(plan, stages=(candidate,)), prepared.relations)
                    except AnalysisStageUnavailableError as error:
                        if error.stage != "attribute_state_binding":
                            raise
                        candidate = replace(stage, attribute_native=True)
                        if reduction is not None:
                            native_value_slots.append(reduction.value_slot)
                    qualified_stages.append(candidate)
                plan = share_frequency_reads(replace(plan, stages=tuple(qualified_stages)))
            plan = optimize_query(plan)
        lowered = lower_query(plan, {} if prepared is None else prepared.relations)
        if prepared is not None and input_id not in requested and input_id not in captured.games:
            from marivo.analysis.materialization.query_key_fusion import (
                fuse_query_keys,
                fuse_query_terminal,
            )

            lowered = fuse_query_keys(prepared, lowered)
            lowered = fuse_query_terminal(prepared, lowered)
        if prepared is not None:
            try:
                qualify_query_sources(prepared, lowered)
            except MaterializationError as error:
                if input_id not in requested:
                    raise
                from marivo.analysis.compiler.attribute_inputs import input_failure

                input_failure(
                    "unqualified_contribution_route", f"{error.expected}; {error.received}"
                )
        schema = _schema(current, lowered.plan)
        source_schemas[input_id] = schema
        source_plans[input_id] = CompiledSourceFrame(
            lowered,
            schema,
            bool(native_value_slots) if input_id not in requested else False,
            native_reads if input_id not in requested else (),
            tuple(native_value_slots) if input_id not in requested else (),
        )
        has_reads = any(isinstance(stage, SourceRead) for stage in lowered.plan.stages)
        inputs.append(SourceInput(input_id) if has_reads else LiteralInput(input_id))
        operations.insert(0, "source" if has_reads else "literal")
    for input_id, current in fixed_frames.items():
        backing = current._backing
        if isinstance(backing, MemoryFrameBacking):
            inputs.append(MemoryInput(input_id, backing.metadata.result_id))
        elif isinstance(backing, StoredFrameBacking):
            inputs.append(
                StoredInput(
                    input_id,
                    backing.metadata.result_id,
                    current._local.store.store_id,
                    current._local.session_ref,
                    backing.artifact_id,
                )
            )
    return PreparedFrameSources(
        prepared_by_input, source_plans, source_schemas, scopes, tuple(inputs), tuple(operations)
    )
