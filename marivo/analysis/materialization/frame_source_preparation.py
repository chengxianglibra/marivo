"""Prepare captured Frame sources under the caller-owned source context stack."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from marivo.analysis.binding.windows import actual_window_axis, require_finite_attribution_scope
from marivo.analysis.compiler.frame_plan import (
    CompiledSourceFrame,
    frame_expression_schema,
)
from marivo.analysis.compiler.graph_lowering import LoweredQuery, lower_query
from marivo.analysis.compiler.graph_plan import (
    QueryPlan,
    QueryStage,
    SourceReadStage,
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
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    LiteralFrameBinding,
    MemoryFrameBacking,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_interpretation import FrameSourceTime, merge_source_times
from marivo.analysis.core.frame_metadata import (
    EvaluationInput,
    FrameOperationName,
    LiteralInput,
    MemoryInput,
    SourceInput,
    StoredInput,
)
from marivo.analysis.core.frame_structure import PendingDataColumn, PendingDataSchema, axis_role
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_inputs import CapturedFrameInputs
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    _query_entities,
    open_query_sources,
    prepare_attribute_endpoint,
    prepare_source_free_populations,
    qualify_query_sources,
    qualify_source_inputs,
)
from marivo.datasource.json_source import JsonResponseCache

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame
    from marivo.datasource.runtime import DatasourceConnectionService


@dataclass(frozen=True, slots=True)
class PreparedFrameSources:
    """Preparation products consumed together by this invocation's schedule."""

    prepared: dict[str, PreparedSources]
    plans: dict[str, CompiledSourceFrame[StageOutput]]
    schemas: dict[str, DataSchema]
    inputs: tuple[EvaluationInput, ...]
    operations: tuple[FrameOperationName, ...]
    graphs: dict[str, BoundGraph]


RunIntervalDomain: TypeAlias = Callable[
    [
        str,
        CompiledSourceFrame[StageOutput],
        BoundGraph,
        PreparedSources | None,
        dict[str, CompiledSourceFrame[StageOutput]],
        dict[str, DataSchema],
        dict[str, BoundGraph],
        dict[str, PreparedSources],
    ],
    StageOutput,
]


def _schema(
    frame: AnalysisFrame,
    plan: QueryPlan,
    source_times: tuple[FrameSourceTime, ...] = (),
    *,
    graph: BoundGraph | None = None,
    keys_only: bool = False,
) -> DataSchema:
    graph = graph or (
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
    values = (
        ()
        if keys_only
        else tuple(
            replace(column, name=constraint.name, unit=constraint.unit)
            for column, constraint in zip(layout.values, frame._schema.values, strict=True)
        )
    )
    schema = DataSchema(
        tuple(axes),
        values,
        value_paths=() if keys_only else frame._schema.value_paths,
        source_times=source_times,
    )
    return schema if keys_only else frame._schema.complete(schema)


def prepare_frame_sources(
    captured: CapturedFrameInputs,
    stack: ExitStack,
    *,
    run_interval_domain: RunIntervalDomain | None = None,
) -> PreparedFrameSources:
    """Open, prepare, lower and qualify sources in captured discovery order."""
    source_frames = captured.source_frames
    fixed_frames = captured.fixed_frames
    expressions = captured.expressions
    requested = captured.requested
    # Validate every dependency before any HTTP source can acquire business data.
    for current in source_frames.values():
        backing = current._backing
        if isinstance(backing, ExpressionFrameBacking) and isinstance(
            backing.root, SourceFrameBinding
        ):
            qualify_source_inputs(backing.root.graph, backing.root.state)
    connection_services: dict[tuple[Path, int], DatasourceConnectionService] = {}
    json_responses: dict[tuple[Path, int], dict[str, JsonResponseCache]] = {}
    prepared_by_input: dict[str, PreparedSources] = {}
    source_plans: dict[str, CompiledSourceFrame[StageOutput]] = {}
    source_schemas: dict[str, DataSchema] = {}
    source_graphs: dict[str, BoundGraph] = {}
    key_schema_cache: dict[int, DataSchema] = {}
    inputs: list[EvaluationInput] = []
    operations = list(captured.operations)
    for input_id, current in source_frames.items():
        backing = current._backing
        assert isinstance(backing, ExpressionFrameBacking)
        binding = backing.root
        assert isinstance(binding, (SourceFrameBinding, LiteralFrameBinding))
        active_graph = binding.graph
        memo = InvocationMemo()
        prepared = None
        key_schemas: dict[str, DataSchema] = {}
        schemas: tuple[SourceSchema, ...] = ()
        checked_inputs: dict[str, StageOutput] = {}
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
                            repair="Use a qualified native table-backed source for attribution, or inspect the original observations. No result was completed or published.",
                            target="analysis_frame.attribute",
                        )
            key_schemas = {
                item.input_id: frame_expression_schema(
                    expressions[item.input_id], source_schemas, memo=key_schema_cache
                )
                for item in binding.frame_key_inputs
            }
            observation = active_graph.signature.observation
            domain_sources = None
            if observation is not None and observation.pending_interval is not None:
                from marivo.analysis.materialization.interval_reports import (
                    domain_graph,
                    prune_domain_plan,
                    resolve_interval,
                    retain_domain,
                )

                assert run_interval_domain is not None
                domain = domain_graph(active_graph)
                if _query_entities(domain, binding.state):
                    scope = (binding.connection.project_root, id(binding.connection.resolver))
                    service = connection_services.get(scope)
                    if service is None:
                        service = stack.enter_context(binding.connection.operation())
                        connection_services[scope] = service
                    domain_sources = stack.enter_context(
                        open_query_sources(
                            service,
                            domain,
                            binding.state,
                            consumption_timezone=current._local.record().report_timezone_name,
                            frame_schemas=key_schemas,
                            json_responses=json_responses.setdefault(scope, {}),
                        )
                    )
                    if domain_sources.graph is not None:
                        domain = domain_sources.graph
                    domain_schemas = domain_sources.schemas
                else:
                    from marivo.analysis.materialization.native_calendar import (
                        bind_pandas_calendars,
                    )

                    domain = bind_pandas_calendars(domain)
                    domain_schemas = prepare_source_free_populations(
                        domain,
                        binding.state,
                        frame_schemas=key_schemas,
                    )
                assert observation.coordinates.population is not None
                domain_plan = optimize_query(
                    prune_domain_plan(
                        plan_query(domain, domain_schemas), observation.coordinates.population
                    )
                )
                domain_lowered = lower_query(
                    domain_plan,
                    {} if domain_sources is None else domain_sources.relations,
                    None if domain_sources is None else domain_sources.population_checks,
                )
                if domain_sources is not None:
                    from marivo.analysis.materialization.query_key_fusion import (
                        fuse_query_keys,
                        fuse_query_terminal,
                    )

                    domain_lowered = fuse_query_keys(domain_sources, domain_lowered)
                    domain_lowered = fuse_query_terminal(domain_sources, domain_lowered)
                    qualify_query_sources(domain_sources, domain_lowered)
                domain_schema = _schema(current, domain_lowered.plan, graph=domain, keys_only=True)
                output = run_interval_domain(
                    input_id,
                    CompiledSourceFrame(domain_lowered, domain_schema),
                    domain,
                    domain_sources,
                    source_plans,
                    source_schemas,
                    source_graphs,
                    prepared_by_input,
                )
                active_graph = resolve_interval(active_graph, output)
                assert observation.coordinates.population is not None
                retain_domain(observation.coordinates.population, output, checked_inputs)
            if _query_entities(active_graph, binding.state):
                scope = (binding.connection.project_root, id(binding.connection.resolver))
                service = connection_services.get(scope)
                if service is None:
                    service = stack.enter_context(binding.connection.operation())
                    connection_services[scope] = service
                prepared = stack.enter_context(
                    open_query_sources(
                        service,
                        active_graph,
                        binding.state,
                        memo=memo,
                        consumption_timezone=current._local.record().report_timezone_name,
                        frame_schemas=key_schemas,
                        json_responses=json_responses.setdefault(scope, {}),
                        reuse_sources=domain_sources,
                    )
                )
                prepared_by_input[input_id] = prepared
                schemas = prepared.schemas
            else:
                from marivo.analysis.materialization.native_calendar import bind_pandas_calendars

                active_graph = bind_pandas_calendars(active_graph)
                schemas = prepare_source_free_populations(
                    active_graph, binding.state, frame_schemas=key_schemas
                )
        active_graph = (
            prepared.graph if prepared is not None and prepared.graph is not None else active_graph
        )
        from marivo.analysis.materialization.actual_windows import prepare_actual_window_plan
        from marivo.analysis.materialization.sample_windows import (
            has_sample_windows,
            prepare_sample_window_plan,
        )

        sample_windows = has_sample_windows(active_graph)
        sparse_window = actual_window_axis(active_graph)
        if sparse_window is not None and (input_id in requested or input_id in captured.games):
            require_finite_attribution_scope(active_graph)
        native_reads: tuple[SourceReadStage, ...] = ()
        native_value_slots: list[str] = []
        lowered: LoweredQuery | None = None
        plan: QueryPlan | None = None
        if sample_windows:
            assert prepared is not None and isinstance(binding, SourceFrameBinding)
            lowered, prepared = prepare_sample_window_plan(
                active_graph,
                prepared,
                binding.state,
                current._local.record().report_timezone_name,
                key_schemas,
                checked_inputs=checked_inputs,
            )
            prepared_by_input[input_id] = prepared
        elif sparse_window is not None:
            assert prepared is not None and isinstance(binding, SourceFrameBinding)
            lowered, prepared = prepare_actual_window_plan(
                active_graph,
                prepared,
                binding.state,
                sparse_window,
                current._local.record().report_timezone_name,
                key_schemas,
            )
            prepared_by_input[input_id] = prepared
        elif input_id in requested:
            assert prepared is not None and isinstance(binding, SourceFrameBinding)
            side, rules, mapping = requested[input_id]
            prepared, plan = prepare_attribute_endpoint(
                prepared, active_graph, binding.state, mapping, side, rules, memo=memo
            )
            prepared_by_input[input_id] = prepared
        else:
            plan = plan_query(active_graph, schemas, memo=memo)
            observation = active_graph.signature.observation
            if checked_inputs or (observation is not None and observation.empty_interval):
                from marivo.analysis.materialization.interval_reports import restrict_plan

                plan = restrict_plan(
                    plan, checked_inputs, empty=bool(observation and observation.empty_interval)
                )
            native_reads = tuple(
                replace(stage, native_relation_id=stage.stage_id)
                for stage in plan.stages
                if isinstance(stage, SourceReadStage)
            )
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
                    if not isinstance(stage, SourceReadStage):
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
            if not checked_inputs:
                plan = optimize_query(plan)
        if lowered is None:
            assert plan is not None
            lowered = lower_query(
                plan,
                {} if prepared is None else prepared.relations,
                None if prepared is None else prepared.population_checks,
            )
        if (
            prepared is not None
            and input_id not in requested
            and input_id not in captured.games
            and sparse_window is None
            and not sample_windows
            and not checked_inputs
        ):
            from marivo.analysis.materialization.query_key_fusion import (
                fuse_query_keys,
                fuse_query_terminal,
            )
            from marivo.analysis.materialization.sample_fusion import fuse_sample_reductions

            lowered = fuse_sample_reductions(prepared, lowered)
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
        schema = _schema(
            current,
            lowered.plan,
            merge_source_times(
                () if prepared is None else prepared.source_times,
                *(schema.source_times for schema in key_schemas.values()),
            ),
        )
        if isinstance(binding, SourceFrameBinding):
            from marivo.analysis.binding.value_time import observation_time_semantics

            schema = replace(
                schema, value_time_semantics=observation_time_semantics(active_graph, binding.state)
            )
        source_graphs[input_id] = active_graph
        source_schemas[input_id] = schema
        source_plans[input_id] = CompiledSourceFrame(
            lowered,
            schema,
            bool(native_value_slots) if input_id not in requested else False,
            native_reads if input_id not in requested else (),
            tuple(native_value_slots) if input_id not in requested else (),
            tuple(checked_inputs.items()),
        )
        has_reads = any(isinstance(stage, SourceReadStage) for stage in lowered.plan.stages)
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
        prepared_by_input,
        source_plans,
        source_schemas,
        tuple(inputs),
        tuple(operations),
        source_graphs,
    )
