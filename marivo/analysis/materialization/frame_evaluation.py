"""One runless evaluation over explicit source and completed-data inputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from types import TracebackType
from typing import TYPE_CHECKING, TypeAlias
from uuid import uuid4

from typing_extensions import override

from marivo._kernel.errors import MarivoError
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan, CompiledSourceFrame
from marivo.analysis.compiler.frame_plan import plan_frame
from marivo.analysis.compiler.lowered_query import LoweredRead
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    SourceReadStage,
)
from marivo.analysis.core.attachments import OutputAttachment
from marivo.analysis.core.attribute import AttributeMethodProduction
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
    SourceFrameBinding,
    StoredFrameBacking,
    key_checkpoint,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts, same_method_facts
from marivo.analysis.core.frame_operations import FrameExpression, SourceFrameInput
from marivo.analysis.materialization.frame_inputs import CapturedFrameInputs, capture_frame_inputs
from marivo.analysis.materialization.frame_source_preparation import (
    PreparedFrameSources,
    prepare_frame_sources,
)
from marivo.analysis.materialization.frame_terminal_fusion import fuse_frame_terminal
from marivo.analysis.materialization.graph_source_execution import run_frame_plan
from marivo.analysis.materialization.native_calendar import freeze_output_calendars
from marivo.analysis.materialization.optimization import optimize_compiled_frame
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_runtime_types import effective_frame_schema
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_method_facts import validate_method_facts
from marivo.analysis.model.attribute_failures import AttributeFailureContext
from marivo.analysis.model.data_schema import CalendarDataAxis, DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.frame_contracts import frame_backing, frame_input_id, frame_local
from marivo.analysis.model.frame_metadata import (
    EvaluationRecord,
    FrameResultMetadata,
)
from marivo.analysis.model.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
    merge_source_reads,
)
from marivo.analysis.model.optimization_policy import DEFAULT_OPTIMIZATION_POLICY
from marivo.datasource.spi import SourceSession
from marivo.semantic.errors import SemanticExpressionError

if TYPE_CHECKING:
    from marivo.analysis.core.attribute import AttributeMethodPayload
    from marivo.analysis.core.frame_attribute_inputs import AttributeInputRequest
    from marivo.analysis.core.frame_data import FrameNode
    from marivo.analysis.model.optimization_policy import OptimizationPolicy


def _attribute_associations(
    compiled: CompiledFramePlan[StageOutput],
    explicit_inputs: tuple[tuple[str, FrameExpression], ...],
) -> tuple[tuple[tuple[str, str, int, bool], ...], frozenset[str]]:
    """Detach stage/input/index/final associations and reachability from the plan."""
    stages = {stage.stage_id: stage for stage in compiled.lowered.plan.stages}
    reachable: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in reachable:
            return
        reachable.add(identifier)
        for dependency in stages[identifier].inputs:
            visit(dependency)

    visit(compiled.lowered.plan.publish)
    by_expression = {id(expression): stage for stage, expression in compiled.frame_operations}
    associations = tuple(
        (identifier, input_id, index, identifier == compiled.lowered.plan.publish)
        for index, (input_id, expression) in enumerate(explicit_inputs)
        if (identifier := by_expression.get(id(expression))) is not None
    )
    return associations, frozenset(reachable)


def _attribute_productions(
    associations: tuple[tuple[str, str, int, bool], ...],
    reachable: frozenset[str],
    observed: tuple[tuple[str, AttributeMethodPayload], ...],
) -> tuple[AttributeMethodProduction, ...]:
    """After cleanup, retain reachable payloads in explicit Frame operand order."""

    payloads = dict(observed)
    result: list[AttributeMethodProduction] = []
    seen: set[str] = set()
    for identifier, input_id, index, is_final in associations:
        if identifier not in payloads or identifier not in reachable or identifier in seen:
            continue
        seen.add(identifier)
        result.append(
            AttributeMethodProduction(identifier, input_id, index, is_final, payloads[identifier])
        )
    assert seen == set(payloads) & reachable
    return tuple(result)


def _plain(value: datetime) -> datetime:
    return datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())


class _SourceContexts(ExitStack):
    """Translate failures raised by owned context cleanup, preserving their chain."""

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        except Exception as error:
            raise MaterializationError(
                expected="successful source cleanup before a completed memory result",
                received=type(error).__name__,
                repair="Inspect the source cleanup failure; no result was returned or published.",
                stage="frame_compute",
                help_target="analysis_frame.compute",
            ) from error


def _fold_method_facts(
    publish: str,
    dependencies: Mapping[str, tuple[str, ...]],
    fixed_inputs: frozenset[str],
    productions: tuple[AttributeMethodProduction, ...],
    result_id: str,
    fixed_facts: Callable[[str], tuple[FrameMethodFacts, ...]],
) -> tuple[FrameMethodFacts, ...]:
    """Fold inert producer facts in original operand and source-sidecar order."""
    own_facts: dict[str, FrameMethodFacts] = {}
    for production in productions:
        payload = production.payload
        assert payload.parent_schema is not None
        facts = FrameMethodFacts(
            kind="attribute",
            origin_result_id=result_id if production.is_final else "result_" + uuid4().hex,
            output_schema=payload.output_schema,
            coordinates=payload.coordinates,
            parent_axis_count=payload.parent_axis_count,
            parents=payload.parents,
            targets=payload.targets,
            parent_schema=payload.parent_schema,
        )
        validate_method_facts(facts)
        own_facts[production.input_id] = facts

    histories: dict[str, tuple[FrameMethodFacts, ...]] = {}

    def history(identifier: str) -> tuple[FrameMethodFacts, ...]:
        previous = histories.get(identifier)
        if previous is not None:
            return previous
        if identifier in fixed_inputs:
            result = fixed_facts(identifier)
        else:
            merged: dict[str, FrameMethodFacts] = {}
            own = own_facts.get(identifier)
            values = (() if own is None else (own,)) + tuple(
                facts for child in dependencies[identifier] for facts in history(child)
            )
            for facts in values:
                known = merged.get(facts.origin_result_id)
                if known is not None and not same_method_facts(known, facts):
                    reject(
                        "identical complete method payloads for one origin",
                        facts.origin_result_id,
                        "Preserve the exact original method facts on every explicit input.",
                        "frame.method_facts",
                        canonical_id="analysis_frame.method_facts",
                    )
                merged.setdefault(facts.origin_result_id, facts)
            result = tuple(merged.values())
        histories[identifier] = result
        return result

    return history(publish)


_CalendarOwners: TypeAlias = dict[str, dict[str, dict[datetime, SourceSession]]]


@dataclass(slots=True)
class _FrameEvaluation:
    captured: CapturedFrameInputs
    declaration_keys: dict[str, CompletedFrameData] | None
    policy: OptimizationPolicy
    fixed_method_facts: dict[str, tuple[FrameMethodFacts, ...]] = field(default_factory=dict)
    fixed_source_reads: dict[str, tuple[FrameSourceRead, ...]] = field(default_factory=dict)
    method_productions: list[tuple[str, AttributeMethodPayload]] = field(default_factory=list)
    source_reads: list[FrameSourceRead] = field(default_factory=list)
    completed_frames: dict[str, StageOutput] = field(default_factory=dict)
    terminal_sources: dict[str, PreparedSources] = field(default_factory=dict)
    operation_inputs: dict[int, str] = field(init=False)
    protected_inputs: frozenset[int] = field(init=False)

    def __post_init__(self) -> None:
        expressions = self.captured.expressions
        self.operation_inputs = {
            id(expression): identifier for identifier, expression in expressions.items()
        }
        self.protected_inputs = frozenset(
            self.operation_inputs
            if self.declaration_keys is not None
            else (
                id(expressions[dependency])
                for identifier in self.captured.source_frames
                for dependency in self.captured.dependencies[identifier]
            )
        )

    def read_input(self, input_id: str) -> CompletedFrameResult:
        current = self.captured.fixed_frames[input_id]
        backing = frame_backing(current)
        if isinstance(backing, MemoryFrameBacking):
            result = backing.result
        else:
            assert isinstance(backing, StoredFrameBacking)
            result = frame_local(current).read_complete(backing)
        self.fixed_source_reads[input_id] = result.metadata.source_reads
        for facts in result.method_facts:
            validate_method_facts(facts)
        if self.declaration_keys is not None:
            self.declaration_keys[input_id] = key_checkpoint(result.data)
        self.fixed_method_facts[input_id] = result.method_facts
        return result

    def input_facts(self, input_id: str) -> tuple[FrameMethodFacts, ...]:
        previous = self.fixed_method_facts.get(input_id)
        if previous is not None:
            return previous
        # Preserve the existing history cache-miss full read and validation.
        # Scheduled inputs already supplied facts; retain no data for this late path.
        return self.read_input(input_id).method_facts

    def checkpoint_operations(
        self,
        plan: CompiledFramePlan[StageOutput],
    ) -> Callable[[LocalCompute | FrameCompute | SourceReadStage, StageOutput], None]:
        operation_stages = {
            stage: self.operation_inputs[id(expression)]
            for stage, expression in plan.frame_operations
            if id(expression) in self.operation_inputs
        }

        def checkpoint(
            stage: LocalCompute | FrameCompute | SourceReadStage, output: StageOutput
        ) -> None:
            input_id = operation_stages.get(stage.stage_id)
            if self.declaration_keys is None or input_id is None or output.frame_schema is None:
                return

            self.declaration_keys[input_id] = key_checkpoint(
                CompletedFrameData(
                    output.frame_schema,
                    output.table,
                    output.frame_attachments,
                    unique_keys=output.unique_keys,
                )
            )

        return checkpoint

    def run_domain(
        self,
        input_id: str,
        domain_plan: CompiledSourceFrame[StageOutput],
        domain_graph: BoundGraph,
        domain_sources: PreparedSources | None,
        plans: dict[str, CompiledSourceFrame[StageOutput]],
        schemas: dict[str, DataSchema],
        graphs: dict[str, BoundGraph],
        sources: dict[str, PreparedSources],
    ) -> StageOutput:
        domain_input = SourceFrameInput(input_id, domain_graph)
        compiled_domain = plan_frame(
            domain_input,
            {**plans, input_id: domain_plan},
            frame_inputs=self.captured.expressions,
            policy=self.policy,
        )
        compiled_domain = optimize_compiled_frame(
            compiled_domain, {}, self.protected_inputs, self.policy, include_top=False
        )
        prepared_domain = {**sources, **({input_id: domain_sources} if domain_sources else {})}

        def adapt_domain(identifier: str, output: StageOutput) -> StageOutput:
            if identifier == input_id:
                return output
            return self.adapt_completed_source(
                identifier, output, schemas, graphs, prepared_domain, {}
            )

        retained: dict[str, StageOutput] = {}
        result = run_frame_plan(
            compiled_domain,
            source_prepared=prepared_domain,
            read_fixed=lambda stage: self.read_input(stage.input_id).data,
            adapt_source=adapt_domain,
            observe_read=lambda stage, request, output: self.record_read(stage, output),
            observe_local=self.checkpoint_operations(compiled_domain),
            completed_frames=self.completed_frames,
            retain_inputs=retained,
        )
        self.completed_frames.update(
            (identifier, output)
            for identifier, output in retained.items()
            if identifier != input_id
        )
        return result

    def adapt_completed_source(
        self,
        input_id: str,
        output: StageOutput,
        schemas: Mapping[str, DataSchema],
        graphs: Mapping[str, BoundGraph],
        sources: Mapping[str, PreparedSources],
        calendar_owners: _CalendarOwners,
    ) -> StageOutput:
        current = self.captured.source_frames[input_id]
        backing = frame_backing(current)
        assert isinstance(backing, ExpressionFrameBacking)
        schema = effective_frame_schema(schemas[input_id], output.columns)
        attachments: tuple[OutputAttachment, ...] = ()
        if isinstance(backing.root, SourceFrameBinding):
            owner = sources.get(input_id)
            schema, attachments = freeze_output_calendars(
                graphs[input_id],
                schema,
                output.table,
                owner.source if owner is not None else None,
                calendar_owners.get(input_id, {}),
            )
        completed = CompletedFrameData(
            schema,
            output.table.loc[:, [c.slot_id for c in schema.columns]],
            attachments,
            unique_keys=output.unique_keys,
        )
        if self.declaration_keys is not None:
            self.declaration_keys[input_id] = key_checkpoint(completed)
        return replace(
            StageOutput.from_completed(completed),
            receipts=output.receipts,
            attribute_inputs=output.attribute_inputs,
            attribute_frequencies=output.attribute_frequencies,
            attribute_state_captures=output.attribute_state_captures,
        )

    def record_read(self, stage: SourceReadStage, output: StageOutput) -> None:
        scopes = tuple(
            dict.fromkeys(
                FrameSourceReadScope(
                    logical.source_id,
                    None
                    if logical.period is None
                    else FrameSourceReadPeriod(
                        None if logical.period.start is None else _plain(logical.period.start),
                        _plain(logical.period.end),
                        logical.period.timezone,
                    ),
                )
                for logical in stage.native_reads or (stage,)
            )
        )
        self.source_reads.append(
            FrameSourceRead(uuid4().hex, datetime.now(timezone.utc), scopes, output.source_rows)
        )

    def compile(self, prepared: PreparedFrameSources) -> CompiledFramePlan[StageOutput]:
        planner = (
            plan_frame
            if self.policy == DEFAULT_OPTIMIZATION_POLICY
            else partial(plan_frame, policy=self.policy)
        )
        compiled = planner(
            self.captured.root,
            prepared.plans,
            frame_inputs=self.captured.expressions,
        )
        compiled, self.terminal_sources = fuse_frame_terminal(
            compiled, prepared.prepared, self.protected_inputs
        )
        return optimize_compiled_frame(
            compiled,
            {
                input_id: source.source.provider.name
                for input_id, source in prepared.prepared.items()
            },
            self.protected_inputs,
            self.policy,
        )

    def execute(
        self, prepared: PreparedFrameSources, compiled: CompiledFramePlan[StageOutput]
    ) -> StageOutput:
        prepared_by_input, source_schemas = prepared.prepared, prepared.schemas
        checkpoint = self.checkpoint_operations(compiled)
        stage_inputs = dict(compiled.source_stages)
        calendar_owners: _CalendarOwners = {}

        def read_fixed(stage: FrameInput) -> CompletedFrameData:
            return self.read_input(stage.input_id).data

        def adapt_source(input_id: str, output: StageOutput) -> StageOutput:
            return self.adapt_completed_source(
                input_id,
                output,
                source_schemas,
                prepared.graphs,
                prepared_by_input,
                calendar_owners,
            )

        def observe_read(stage: SourceReadStage, request: LoweredRead, output: StageOutput) -> None:
            input_id = stage_inputs.get(stage.stage_id)
            # Native attribution coalitions consume fixed parent keys; they do
            # not introduce reporting calendar positions for a source Frame.
            if input_id is not None and input_id in prepared_by_input:
                prepared_source = prepared_by_input[input_id]
                source = prepared_source.sources.get(stage.source_id, prepared_source.source)
                for axis in source_schemas[input_id].axes:
                    if not isinstance(axis, CalendarDataAxis):
                        continue
                    slot = axis.columns[0].slot_id
                    if slot in output.table:
                        owners = calendar_owners.setdefault(input_id, {}).setdefault(axis.token, {})
                        for value in output.table[slot].dropna().unique():
                            owners.setdefault(_plain(value), source)
            self.record_read(stage, output)

        def observe_local(
            stage: LocalCompute | FrameCompute | SourceReadStage, output: StageOutput
        ) -> None:
            checkpoint(stage, output)
            self.method_productions.extend(
                (stage.stage_id, payload) for payload in output.attribute_methods
            )

        try:
            return run_frame_plan(
                compiled,
                source_prepared={**prepared_by_input, **self.terminal_sources},
                read_fixed=read_fixed,
                adapt_source=adapt_source,
                observe_read=observe_read,
                observe_local=observe_local,
                completed_frames=self.completed_frames,
            )
        except SemanticExpressionError as error:
            attribute_context = error.details.get("attribute_context")
            assert (
                error.expected is not None
                and error.received is not None
                and error.repair is not None
            )
            raise MaterializationError(
                expected=error.expected,
                received=error.received,
                repair=error.repair.action,
                stage="numeric_value",
                help_target="analysis_frame.compute",
                attribute_context=attribute_context
                if isinstance(attribute_context, AttributeFailureContext)
                else None,
            ) from error
        except MarivoError:
            raise
        except Exception as error:
            raise MaterializationError(
                expected="successful execution of the checked Frame plan",
                received="an unexpected execution failure before result completion",
                repair="Inspect the source connection and input data, correct the execution failure, then retry frame.compute(); no Run was admitted.",
                stage="frame_compute",
                help_target="analysis_frame.compute",
            ) from error


def _evaluate_frame(
    frame: FrameNode,
    request: AttributeInputRequest | None = None,
    declaration_keys: dict[str, CompletedFrameData] | None = None,
    *,
    policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY,
) -> CompletedFrameResult | StageOutput:
    """Evaluate once, close all sources, then return independent data and facts."""
    captured = capture_frame_inputs(frame, request)
    state = _FrameEvaluation(captured, declaration_keys, policy)
    with _SourceContexts() as stack:
        prepare = (
            prepare_frame_sources
            if policy == DEFAULT_OPTIMIZATION_POLICY
            else partial(prepare_frame_sources, policy=policy)
        )
        if state.protected_inputs:
            prepare = partial(prepare, protected_inputs=state.protected_inputs)
        prepared = prepare(captured, stack, run_interval_domain=state.run_domain)
        compiled = state.compile(prepared)
        output = state.execute(prepared, compiled)
        if request is not None:
            return output
        data = CompletedFrameData(
            output.frame_schema or effective_frame_schema(compiled.schema, output.columns),
            output.table,
            output.frame_attachments,
            unique_keys=output.unique_keys,
        )
        associations, reachable = _attribute_associations(
            compiled,
            tuple(
                (identifier, captured.expressions[identifier]) for identifier in captured.collected
            ),
        )
    completed_method_productions = _attribute_productions(
        associations, reachable, tuple(state.method_productions)
    )
    assert len({production.stage_id for production in completed_method_productions}) == len(
        state.method_productions
    )
    # Every owned source context has closed before result identity/success.
    metadata = FrameResultMetadata(
        "result_" + uuid4().hex,
        EvaluationRecord(datetime.now(timezone.utc), prepared.inputs, prepared.operations),
        tuple(state.source_reads),
    )
    metadata = replace(metadata, lineage=frame.lineage)
    method_facts = _fold_method_facts(
        frame_input_id(frame),
        captured.dependencies,
        frozenset(captured.fixed_frames),
        completed_method_productions,
        metadata.result_id,
        state.input_facts,
    )
    inherited = tuple(
        state.fixed_source_reads[input_id]
        for input_id in captured.fixed_frames
        if input_id in state.fixed_source_reads
    )
    metadata = replace(
        metadata,
        source_reads=merge_source_reads(*inherited, tuple(state.source_reads)),
    )
    return CompletedFrameResult(data, metadata, method_facts)


def evaluate_frame(
    frame: FrameNode,
    *,
    declaration_keys: dict[str, CompletedFrameData] | None = None,
    policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY,
) -> CompletedFrameResult:
    result = _evaluate_frame(frame, declaration_keys=declaration_keys, policy=policy)
    assert isinstance(result, CompletedFrameResult)
    return result


def evaluate_attribute_inputs(request: AttributeInputRequest) -> StageOutput:
    """Run private preparation in the ordinary Frame plan and source schedule."""
    result = _evaluate_frame(request.parent, request)
    assert isinstance(result, StageOutput)
    return result
