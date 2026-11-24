"""Prepare captured Frame sources under the caller-owned source context stack."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from marivo._definition.expressions import SourceAggregate
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.binding.value_time import observation_time_semantics
from marivo.analysis.binding.windows import actual_window_axis, require_finite_attribution_scope
from marivo.analysis.compiler.attribute_frequencies import frequency_read, share_frequency_reads
from marivo.analysis.compiler.attribute_inputs import input_failure
from marivo.analysis.compiler.compiled_frame import CompiledSourceFrame
from marivo.analysis.compiler.frame_plan import frame_expression_schema
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.lowered_query import LoweredQuery
from marivo.analysis.compiler.optimization import InvocationMemo, optimize_query
from marivo.analysis.compiler.queries import endpoint
from marivo.analysis.compiler.query_planning import plan_query
from marivo.analysis.compiler.query_stages import (
    QueryPlan,
    QueryStage,
    SourceReadStage,
    SourceSchema,
    read_reduction,
)
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    LiteralFrameBinding,
    MemoryFrameBacking,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.materialization.actual_windows import prepare_actual_window_plan
from marivo.analysis.materialization.attribute_source_preparation import prepare_attribute_endpoint
from marivo.analysis.materialization.frame_inputs import CapturedFrameInputs
from marivo.analysis.materialization.interval_reports import (
    domain_graph,
    prune_domain_plan,
    resolve_interval,
    restrict_plan,
    retain_domain,
)
from marivo.analysis.materialization.key_predicate_pushdown import push_source_key_predicate
from marivo.analysis.materialization.native_calendar import bind_pandas_calendars
from marivo.analysis.materialization.optimization import optimize_lowered_source
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_source_dependencies import _query_entities
from marivo.analysis.materialization.query_sources import (
    open_query_sources,
    prepare_source_free_populations,
    qualify_source_inputs,
)
from marivo.analysis.materialization.sample_windows import (
    has_sample_windows,
    prepare_sample_window_plan,
)
from marivo.analysis.materialization.source_qualification import qualify_attribute_game_sources
from marivo.analysis.materialization.source_reads import qualify_query_sources
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.data_schema import (
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    IntervalDataAxis,
)
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.errors import reject
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import (
    frame_backing,
    frame_input_id,
    frame_local,
    frame_logical_schema,
)
from marivo.analysis.model.frame_metadata import (
    EvaluationInput,
    FrameOperationName,
    LiteralInput,
    MemoryInput,
    SourceInput,
    StoredInput,
)
from marivo.analysis.model.frame_structure import PendingDataColumn, PendingDataSchema, axis_role
from marivo.analysis.model.optimization_policy import DEFAULT_OPTIMIZATION_POLICY
from marivo.analysis.model.time_facts import FrameSourceTime, merge_source_times
from marivo.datasource.spi import JsonResponseCache, TableSourceIR
from marivo.semantic.spi import normalize_target_entity

if TYPE_CHECKING:
    from marivo.analysis.core.frame_data import FrameNode
    from marivo.analysis.model.optimization_policy import OptimizationPolicy
    from marivo.datasource.spi import DatasourceConnectionService


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
    frame: FrameNode,
    plan: QueryPlan,
    source_times: tuple[FrameSourceTime, ...] = (),
    *,
    graph: BoundGraph | None = None,
    keys_only: bool = False,
) -> DataSchema:
    backing = frame_backing(frame)
    logical = frame_logical_schema(frame)
    graph = graph or (
        backing.root.graph
        if isinstance(backing, ExpressionFrameBacking)
        and isinstance(backing.root, (SourceFrameBinding, LiteralFrameBinding))
        else None
    )
    if graph is None or not isinstance(logical, PendingDataSchema):
        reject(
            "a captured pending source structure",
            frame_input_id(frame),
            "Capture the explicit source before preparing it.",
            "frame.prepare",
        )
    layout = endpoint(graph, plan)
    columns = {column.slot_id: column for column in layout.columns}
    axes: list[DataAxis] = []
    if len(layout.key_groups) != len(logical.axes):
        reject(
            "one complete prepared key group per captured axis",
            repr(layout.key_groups),
            "Preserve complete source coordinate groups.",
            "frame.prepare",
        )
    for captured, keys in zip(logical.axes, layout.key_groups, strict=True):
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
            for column, constraint in zip(layout.values, logical.values, strict=True)
        )
    )
    schema = DataSchema(
        tuple(axes),
        values,
        value_paths=() if keys_only else logical.value_paths,
        source_times=source_times,
    )
    return schema if keys_only else logical.complete(schema)


def prepare_frame_sources(
    captured: CapturedFrameInputs,
    stack: ExitStack,
    *,
    run_interval_domain: RunIntervalDomain | None = None,
    policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY,
    protected_inputs: frozenset[int] = frozenset(),
) -> PreparedFrameSources:
    """Open, prepare, lower and qualify sources in captured discovery order."""
    # Validate every dependency before any HTTP source can acquire business data.
    for current in captured.source_frames.values():
        backing = frame_backing(current)
        if isinstance(backing, ExpressionFrameBacking) and isinstance(
            backing.root, SourceFrameBinding
        ):
            qualify_source_inputs(backing.root.graph, backing.root.state)
    state = _FrameSourcePreparation(
        captured, stack, run_interval_domain, list(captured.operations), policy, protected_inputs
    )
    for input_id, current in captured.source_frames.items():
        state.prepare(input_id, current)
    for input_id, current in captured.fixed_frames.items():
        backing = frame_backing(current)
        if isinstance(backing, MemoryFrameBacking):
            state.inputs.append(MemoryInput(input_id, backing.metadata.result_id))
        elif isinstance(backing, StoredFrameBacking):
            state.inputs.append(
                StoredInput(
                    input_id,
                    backing.metadata.result_id,
                    frame_local(current).owner.owner_id,
                    frame_local(current).owner.session_id,
                    backing.artifact_id,
                )
            )
    return PreparedFrameSources(
        state.prepared_by_input,
        state.source_plans,
        state.source_schemas,
        tuple(state.inputs),
        tuple(state.operations),
        state.source_graphs,
    )


@dataclass(slots=True)
class _FrameSourcePreparation:
    captured: CapturedFrameInputs
    stack: ExitStack
    run_interval_domain: RunIntervalDomain | None
    operations: list[FrameOperationName]
    policy: OptimizationPolicy
    protected_inputs: frozenset[int]
    connection_services: dict[tuple[Path, int], DatasourceConnectionService] = field(
        default_factory=dict
    )
    json_responses: dict[tuple[Path, int], dict[str, JsonResponseCache]] = field(
        default_factory=dict
    )
    prepared_by_input: dict[str, PreparedSources] = field(default_factory=dict)
    source_plans: dict[str, CompiledSourceFrame[StageOutput]] = field(default_factory=dict)
    source_schemas: dict[str, DataSchema] = field(default_factory=dict)
    source_graphs: dict[str, BoundGraph] = field(default_factory=dict)
    key_schema_cache: dict[int, DataSchema] = field(default_factory=dict)
    inputs: list[EvaluationInput] = field(default_factory=list)

    def prepare(self, input_id: str, current: FrameNode) -> None:
        backing = frame_backing(current)
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
            active_graph, prepared, key_schemas, schemas = self._bind_source(
                input_id,
                current,
                binding,
                memo,
                checked_inputs,
            )
        active_graph = (
            prepared.graph if prepared is not None and prepared.graph is not None else active_graph
        )
        lowered, prepared, native_reads, native_value_slots = self._compile_source(
            input_id,
            current,
            binding,
            active_graph,
            memo,
            prepared,
            key_schemas,
            schemas,
            checked_inputs,
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
            schema = replace(
                schema, value_time_semantics=observation_time_semantics(active_graph, binding.state)
            )
        self.source_graphs[input_id] = active_graph
        self.source_schemas[input_id] = schema
        self.source_plans[input_id] = CompiledSourceFrame(
            lowered,
            schema,
            bool(native_value_slots) if input_id not in self.captured.requested else False,
            native_reads if input_id not in self.captured.requested else (),
            tuple(native_value_slots) if input_id not in self.captured.requested else (),
            tuple(checked_inputs.items()),
        )
        has_reads = any(isinstance(stage, SourceReadStage) for stage in lowered.plan.stages)
        self.inputs.append(SourceInput(input_id) if has_reads else LiteralInput(input_id))
        self.operations.insert(0, "source" if has_reads else "literal")

    def _bind_source(
        self,
        input_id: str,
        current: FrameNode,
        binding: SourceFrameBinding,
        memo: InvocationMemo,
        checked_inputs: dict[str, StageOutput],
    ) -> tuple[BoundGraph, PreparedSources | None, dict[str, DataSchema], tuple[SourceSchema, ...]]:
        if input_id in self.captured.games:
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
                self.captured.expressions[item.input_id],
                self.source_schemas,
                memo=self.key_schema_cache,
            )
            for item in binding.frame_key_inputs
        }
        active_graph, domain_sources = self._resolve_pending_interval(
            input_id, current, binding, key_schemas, checked_inputs
        )
        prepared = None
        if _query_entities(active_graph, binding.state):
            prepared = self._open(
                current, binding, active_graph, key_schemas, memo=memo, reuse_sources=domain_sources
            )
            self.prepared_by_input[input_id] = prepared
            schemas = prepared.schemas
        else:
            active_graph = bind_pandas_calendars(active_graph)
            schemas = prepare_source_free_populations(
                active_graph, binding.state, frame_schemas=key_schemas
            )
        return active_graph, prepared, key_schemas, schemas

    def _open(
        self,
        current: FrameNode,
        binding: SourceFrameBinding,
        graph: BoundGraph,
        key_schemas: dict[str, DataSchema],
        *,
        memo: InvocationMemo | None = None,
        reuse_sources: PreparedSources | None = None,
    ) -> PreparedSources:
        scope = (binding.connection.project_root, id(binding.connection.resolver))
        service = self.connection_services.get(scope)
        if service is None:
            service = self.stack.enter_context(binding.connection.operation())
            self.connection_services[scope] = service
        prepared = self.stack.enter_context(
            open_query_sources(
                service,
                graph,
                binding.state,
                memo=memo,
                consumption_timezone=frame_local(current).report_timezone_name,
                frame_schemas=key_schemas,
                json_responses=self.json_responses.setdefault(scope, {}),
                reuse_sources=reuse_sources,
                policy=self.policy,
            )
        )
        return prepared

    def _resolve_pending_interval(
        self,
        input_id: str,
        current: FrameNode,
        binding: SourceFrameBinding,
        key_schemas: dict[str, DataSchema],
        checked_inputs: dict[str, StageOutput],
    ) -> tuple[BoundGraph, PreparedSources | None]:
        active_graph = binding.graph
        observation = active_graph.signature.observation
        domain_sources = None
        if observation is not None and observation.pending_interval is not None:
            assert self.run_interval_domain is not None
            domain = domain_graph(active_graph)
            if _query_entities(domain, binding.state):
                domain_sources = self._open(current, binding, domain, key_schemas)
                if domain_sources.graph is not None:
                    domain = domain_sources.graph
                domain_schemas = domain_sources.schemas
            else:
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
                ),
                policy=self.policy,
            )
            domain_lowered = lower_query(
                domain_plan,
                {} if domain_sources is None else domain_sources.relations,
                None if domain_sources is None else domain_sources.population_checks,
            )
            if domain_sources is not None:
                domain_lowered = optimize_lowered_source(
                    domain_sources, domain_lowered, self.policy, include_sample=False
                )
                qualify_query_sources(domain_sources, domain_lowered)
            domain_schema = _schema(current, domain_lowered.plan, graph=domain, keys_only=True)
            output = self.run_interval_domain(
                input_id,
                CompiledSourceFrame(domain_lowered, domain_schema),
                domain,
                domain_sources,
                self.source_plans,
                self.source_schemas,
                self.source_graphs,
                self.prepared_by_input,
            )
            active_graph = resolve_interval(active_graph, output)
            assert observation.coordinates.population is not None
            retain_domain(observation.coordinates.population, output, checked_inputs)
        return active_graph, domain_sources

    def _compile_source(
        self,
        input_id: str,
        current: FrameNode,
        binding: SourceFrameBinding | LiteralFrameBinding,
        active_graph: BoundGraph,
        memo: InvocationMemo,
        prepared: PreparedSources | None,
        key_schemas: dict[str, DataSchema],
        schemas: tuple[SourceSchema, ...],
        checked_inputs: dict[str, StageOutput],
    ) -> tuple[LoweredQuery, PreparedSources | None, tuple[SourceReadStage, ...], list[str]]:
        sample_windows = has_sample_windows(active_graph)
        sparse_window = actual_window_axis(active_graph)
        if sparse_window is not None and (
            input_id in self.captured.requested or input_id in self.captured.games
        ):
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
                frame_local(current).report_timezone_name,
                key_schemas,
                checked_inputs=checked_inputs,
            )
            self.prepared_by_input[input_id] = prepared
        elif sparse_window is not None:
            assert prepared is not None and isinstance(binding, SourceFrameBinding)
            lowered, prepared = prepare_actual_window_plan(
                active_graph,
                prepared,
                binding.state,
                sparse_window,
                frame_local(current).report_timezone_name,
                key_schemas,
            )
            self.prepared_by_input[input_id] = prepared
        elif input_id in self.captured.requested:
            assert prepared is not None and isinstance(binding, SourceFrameBinding)
            side, rules, mapping = self.captured.requested[input_id]
            prepared, plan = prepare_attribute_endpoint(
                prepared, active_graph, binding.state, mapping, side, rules, memo=memo
            )
            self.prepared_by_input[input_id] = prepared
        else:
            plan = plan_query(active_graph, schemas, memo=memo)
            observation = active_graph.signature.observation
            if checked_inputs or (observation is not None and observation.empty_interval):
                plan = restrict_plan(
                    plan, checked_inputs, empty=bool(observation and observation.empty_interval)
                )
            native_reads = tuple(
                replace(stage, native_relation_id=stage.stage_id)
                for stage in plan.stages
                if isinstance(stage, SourceReadStage)
            )
            if input_id in self.captured.games:
                assert prepared is not None
                plan, native_value_slots = _qualify_game(prepared, plan, self.policy)
            if not checked_inputs:
                plan = optimize_query(plan, policy=self.policy)
        if lowered is None:
            assert plan is not None
            lowered = lower_query(
                replace(plan, optimization_policy=self.policy),
                {} if prepared is None else prepared.relations,
                None if prepared is None else prepared.population_checks,
            )
        if (
            prepared is not None
            and input_id not in self.captured.requested
            and input_id not in self.captured.games
            and sparse_window is None
            and not sample_windows
            and not checked_inputs
        ):
            lowered = optimize_lowered_source(prepared, lowered, self.policy)
        if prepared is not None:
            lowered = push_source_key_predicate(
                self.captured,
                input_id,
                active_graph,
                lowered,
                prepared,
                self.protected_inputs,
                self.policy,
            )
            try:
                qualify_query_sources(prepared, lowered)
            except MaterializationError as error:
                if input_id not in self.captured.requested:
                    raise

                input_failure(
                    "unqualified_contribution_route", f"{error.expected}; {error.received}"
                )
        return lowered, prepared, native_reads, native_value_slots


def _qualify_game(
    prepared: PreparedSources, plan: QueryPlan, policy: OptimizationPolicy
) -> tuple[QueryPlan, list[str]]:
    native_value_slots: list[str] = []
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
    plan = replace(plan, stages=tuple(qualified_stages))
    if policy.allows("attribute_frequency_sharing"):
        plan = share_frequency_reads(plan)
    return plan, native_value_slots
