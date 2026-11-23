"""One runless evaluation over explicit source and completed-data inputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from types import TracebackType
from typing import TYPE_CHECKING
from uuid import uuid4

from marivo._kernel.errors import MarivoError
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.compiler.frame_filter_fusion import fuse_frame_filters
from marivo.analysis.compiler.frame_plan import (
    CompiledFramePlan,
    CompiledSourceFrame,
    plan_frame,
)
from marivo.analysis.compiler.frame_top import bound_pending_frame_top
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    SourceReadStage,
)
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    MemoryFrameBacking,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_expression import (
    FrameExpression,
    SourceFrameInput,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts, same_method_facts
from marivo.analysis.materialization.frame_inputs import capture_frame_inputs
from marivo.analysis.materialization.frame_source_preparation import prepare_frame_sources
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    run_frame_plan,
)
from marivo.analysis.materialization.query_runtime_types import effective_frame_schema
from marivo.analysis.methods.carriers.attachments import OutputAttachment
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.methods.frame_method_facts import validate_method_facts
from marivo.analysis.model.data_schema import CalendarDataAxis, DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.execution_errors import MaterializationError
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
from marivo.datasource.spi import SourceSession
from marivo.semantic.errors import SemanticExpressionError

if TYPE_CHECKING:
    from marivo.analysis.binding.frame_attribute_inputs import AttributeInputRequest
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.core.frame_data import FrameNode


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
    from marivo.analysis.core.attribute import AttributeMethodProduction

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


def _evaluate_frame(
    frame: FrameNode,
    request: AttributeInputRequest | None = None,
    declaration_keys: dict[str, CompletedFrameData] | None = None,
) -> CompletedFrameResult | StageOutput:
    """Evaluate once, close all sources, then return independent data and facts."""
    captured = capture_frame_inputs(frame, request)
    source_frames = captured.source_frames
    fixed_frames = captured.fixed_frames
    expressions = captured.expressions
    collected = captured.collected
    fixed_method_facts: dict[str, tuple[FrameMethodFacts, ...]] = {}
    fixed_source_reads: dict[str, tuple[FrameSourceRead, ...]] = {}
    method_productions: list[tuple[str, AttributeMethodPayload]] = []

    def read_input(input_id: str) -> CompletedFrameResult:
        current = fixed_frames[input_id]
        backing = current._backing
        if isinstance(backing, MemoryFrameBacking):
            result = backing.result
        else:
            assert isinstance(backing, StoredFrameBacking)
            result = current._local.read_complete(backing)
        fixed_source_reads[input_id] = result.metadata.source_reads
        for facts in result.method_facts:
            validate_method_facts(facts)
        if declaration_keys is not None:
            from marivo.analysis.core.frame_data import key_checkpoint

            declaration_keys[input_id] = key_checkpoint(result.data)
        fixed_method_facts[input_id] = result.method_facts
        return result

    def input_facts(input_id: str) -> tuple[FrameMethodFacts, ...]:
        previous = fixed_method_facts.get(input_id)
        if previous is not None:
            return previous
        # Preserve the existing history cache-miss full read and validation.
        # Scheduled inputs already supplied facts; retain no data for this late path.
        return read_input(input_id).method_facts

    source_reads: list[FrameSourceRead] = []
    completed_frames: dict[str, StageOutput] = {}
    operation_inputs = {
        id(expression): identifier for identifier, expression in expressions.items()
    }
    protected_inputs = frozenset(
        operation_inputs
        if declaration_keys is not None
        else (
            id(expressions[dependency])
            for identifier in source_frames
            for dependency in captured.dependencies[identifier]
        )
    )

    def checkpoint_operations(
        plan: CompiledFramePlan[StageOutput],
    ) -> Callable[[LocalCompute | FrameCompute | SourceReadStage, StageOutput], None]:
        operation_stages = {
            stage: operation_inputs[id(expression)]
            for stage, expression in plan.frame_operations
            if id(expression) in operation_inputs
        }

        def checkpoint(
            stage: LocalCompute | FrameCompute | SourceReadStage, output: StageOutput
        ) -> None:
            input_id = operation_stages.get(stage.stage_id)
            if declaration_keys is None or input_id is None or output.frame_schema is None:
                return
            from marivo.analysis.core.frame_data import key_checkpoint

            declaration_keys[input_id] = key_checkpoint(
                CompletedFrameData(
                    output.frame_schema,
                    output.table,
                    output.frame_attachments,
                    unique_keys=output.unique_keys,
                )
            )

        return checkpoint

    with _SourceContexts() as stack:

        def run_domain(
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
                domain_input, {**plans, input_id: domain_plan}, frame_inputs=expressions
            )
            compiled_domain = fuse_frame_filters(compiled_domain, protected_inputs=protected_inputs)
            prepared_domain = {**sources, **({input_id: domain_sources} if domain_sources else {})}

            def adapt_domain(identifier: str, output: StageOutput) -> StageOutput:
                if identifier == input_id:
                    return output
                return adapt_completed_source(
                    identifier, output, schemas, graphs, prepared_domain, {}
                )

            retained: dict[str, StageOutput] = {}
            result = run_frame_plan(
                compiled_domain,
                source_prepared=prepared_domain,
                read_fixed=lambda stage: read_input(stage.input_id).data,
                adapt_source=adapt_domain,
                observe_read=lambda stage, request, output: record_read(stage, output),
                observe_local=checkpoint_operations(compiled_domain),
                completed_frames=completed_frames,
                retain_inputs=retained,
            )
            completed_frames.update(
                (identifier, output)
                for identifier, output in retained.items()
                if identifier != input_id
            )
            return result

        def adapt_completed_source(
            input_id: str,
            output: StageOutput,
            schemas: Mapping[str, DataSchema],
            graphs: Mapping[str, BoundGraph],
            sources: Mapping[str, PreparedSources],
            calendar_owners: dict[str, dict[str, dict[datetime, SourceSession]]],
        ) -> StageOutput:
            current = source_frames[input_id]
            backing = current._backing
            assert isinstance(backing, ExpressionFrameBacking)
            schema = effective_frame_schema(schemas[input_id], output.columns)
            attachments: tuple[OutputAttachment, ...] = ()
            if isinstance(backing.root, SourceFrameBinding):
                from marivo.analysis.materialization.native_calendar import freeze_output_calendars

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
            if declaration_keys is not None:
                from marivo.analysis.core.frame_data import key_checkpoint

                declaration_keys[input_id] = key_checkpoint(completed)
            return replace(
                StageOutput.from_completed(completed),
                receipts=output.receipts,
                attribute_inputs=output.attribute_inputs,
                attribute_frequencies=output.attribute_frequencies,
                attribute_state_captures=output.attribute_state_captures,
            )

        def record_read(stage: SourceReadStage, output: StageOutput) -> None:
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
            source_reads.append(
                FrameSourceRead(uuid4().hex, datetime.now(timezone.utc), scopes, output.source_rows)
            )

        prepared = prepare_frame_sources(captured, stack, run_interval_domain=run_domain)
        prepared_by_input = prepared.prepared
        source_plans = prepared.plans
        source_schemas = prepared.schemas
        inputs = prepared.inputs
        operations = prepared.operations
        compiled = plan_frame(captured.root, source_plans, frame_inputs=expressions)
        compiled = bound_pending_frame_top(
            compiled,
            {
                input_id: source.source.provider.name
                for input_id, source in prepared_by_input.items()
            },
        )
        compiled = fuse_frame_filters(compiled, protected_inputs=protected_inputs)
        checkpoint = checkpoint_operations(compiled)
        stage_inputs = dict(compiled.source_stages)
        calendar_owners: dict[str, dict[str, dict[datetime, SourceSession]]] = {}

        def read_fixed(stage: FrameInput) -> CompletedFrameData:
            return read_input(stage.input_id).data

        def adapt_source(input_id: str, output: StageOutput) -> StageOutput:
            return adapt_completed_source(
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
            if input_id is not None:
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
            record_read(stage, output)

        def observe_local(
            stage: LocalCompute | FrameCompute | SourceReadStage, output: StageOutput
        ) -> None:
            checkpoint(stage, output)
            method_productions.extend(
                (stage.stage_id, payload) for payload in output.attribute_methods
            )

        try:
            output = run_frame_plan(
                compiled,
                source_prepared=prepared_by_input,
                read_fixed=read_fixed,
                adapt_source=adapt_source,
                observe_read=observe_read,
                observe_local=observe_local,
                completed_frames=completed_frames,
            )
        except SemanticExpressionError as error:
            from marivo.analysis.model.attribute_failures import AttributeFailureContext

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
        if request is not None:
            return output
        data = CompletedFrameData(
            output.frame_schema or effective_frame_schema(compiled.schema, output.columns),
            output.table,
            output.frame_attachments,
            unique_keys=output.unique_keys,
        )
        associations, reachable = _attribute_associations(
            compiled, tuple((identifier, expressions[identifier]) for identifier in collected)
        )
    completed_method_productions = _attribute_productions(
        associations, reachable, tuple(method_productions)
    )
    assert len({production.stage_id for production in completed_method_productions}) == len(
        method_productions
    )
    # Every owned source context has closed before result identity/success.
    metadata = FrameResultMetadata(
        "result_" + uuid4().hex,
        EvaluationRecord(datetime.now(timezone.utc), tuple(inputs), tuple(operations)),
        tuple(source_reads),
    )
    metadata = replace(metadata, lineage=frame.lineage)
    method_facts = _fold_method_facts(
        frame._input_id,
        captured.dependencies,
        frozenset(fixed_frames),
        completed_method_productions,
        metadata.result_id,
        input_facts,
    )
    inherited = tuple(
        fixed_source_reads[input_id] for input_id in fixed_frames if input_id in fixed_source_reads
    )
    metadata = replace(
        metadata,
        source_reads=merge_source_reads(*inherited, tuple(source_reads)),
    )
    return CompletedFrameResult(data, metadata, method_facts)


def evaluate_frame(
    frame: FrameNode, *, declaration_keys: dict[str, CompletedFrameData] | None = None
) -> CompletedFrameResult:
    result = _evaluate_frame(frame, declaration_keys=declaration_keys)
    assert isinstance(result, CompletedFrameResult)
    return result


def evaluate_attribute_inputs(request: AttributeInputRequest) -> StageOutput:
    """Run private preparation in the ordinary Frame plan and source schedule."""
    result = _evaluate_frame(request.parent, request)
    assert isinstance(result, StageOutput)
    return result
