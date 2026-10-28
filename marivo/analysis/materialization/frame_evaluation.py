"""One runless evaluation over explicit source and completed-data inputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from types import TracebackType
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa

from marivo._definition.graph import computation_fingerprint
from marivo._definition.query import PopulationValues
from marivo._definition.relations import AttributeKeys, FrameKeys, PopulationProgram
from marivo._errors import MarivoError
from marivo.analysis.compiler.frame_plan import (
    CompiledFramePlan,
    plan_frame,
)
from marivo.analysis.compiler.frame_top import bound_pending_frame_top
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    SourceRead,
    read_attribute,
)
from marivo.analysis.compiler.parameters import parameter_domains
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.artifact_data import (
    OutputEvidence,
)
from marivo.analysis.core.frame_audit import (
    EvaluationRecord,
    FrameAudit,
    FrameResultMetadata,
)
from marivo.analysis.core.frame_calendar import FrozenSourceCalendar
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
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts, same_method_facts
from marivo.analysis.core.frame_source_facts import source_scope
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.frame_inputs import capture_frame_inputs
from marivo.analysis.materialization.frame_source_preparation import prepare_frame_sources
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    run_frame_plan,
)
from marivo.analysis.materialization.query_runtime_types import effective_frame_schema
from marivo.analysis.methods.frame_method_facts import validate_method_facts
from marivo.semantic.errors import SemanticExpressionError

if TYPE_CHECKING:
    from marivo.analysis.binding.frame_attribute_inputs import AttributeInputRequest
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.dsl.frame import AnalysisFrame


def _attribute_associations(
    compiled: CompiledFramePlan,
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
    frame: AnalysisFrame,
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
    fixed_source_facts: dict[str, tuple[OutputEvidence, ...]] = {}
    method_productions: list[tuple[str, AttributeMethodPayload]] = []

    def read_input(input_id: str) -> CompletedFrameResult:
        current = fixed_frames[input_id]
        backing = current._backing
        if isinstance(backing, MemoryFrameBacking):
            result = backing.result
        else:
            assert isinstance(backing, StoredFrameBacking)
            result = current._local.read_complete(backing)
        fixed_source_facts[input_id] = result.metadata.audit.source_facts
        for facts in result.method_facts:
            validate_method_facts(facts)
        if declaration_keys is not None:
            from marivo.analysis.core.frame_derivation import key_checkpoint

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

    source_facts: list[OutputEvidence] = []
    checks: list[OutputEvidence] = []
    evaluated_keys: dict[str, str] = {}
    with _SourceContexts() as stack:
        prepared = prepare_frame_sources(captured, stack)
        prepared_by_input = prepared.prepared
        source_plans = prepared.plans
        source_schemas = prepared.schemas
        scopes = prepared.scopes
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
        source_stage_ids = dict(compiled.source_stages)
        operation_inputs = {
            id(expression): identifier for identifier, expression in expressions.items()
        }
        operation_stages = {
            stage: operation_inputs[id(expression)]
            for stage, expression in compiled.frame_operations
            if id(expression) in operation_inputs
        }
        applicable: dict[str, tuple[str, ...]] = {}
        for stage in compiled.lowered.plan.stages:
            if isinstance(stage, FrameInput):
                applicable[stage.stage_id] = (stage.input_id,)
            elif stage.stage_id in source_stage_ids:
                applicable[stage.stage_id] = (source_stage_ids[stage.stage_id],)
            else:
                applicable[stage.stage_id] = tuple(
                    dict.fromkeys(
                        item for dependency in stage.inputs for item in applicable[dependency]
                    )
                )

        def read_fixed(stage: FrameInput) -> CompletedFrameData:
            return read_input(stage.input_id).data

        def adapt_source(input_id: str, output: StageOutput) -> StageOutput:
            current = source_frames[input_id]
            backing = current._backing
            assert isinstance(backing, ExpressionFrameBacking)
            binding = backing.root
            attachments = (
                tuple(
                    attachment
                    for calendar in binding.calendars
                    for attachment in (
                        calendar.attachments
                        if isinstance(calendar, FrozenSourceCalendar)
                        else (calendar.directory,)
                    )
                )
                if isinstance(binding, SourceFrameBinding)
                else ()
            )
            schema = effective_frame_schema(source_schemas[input_id], output.columns)
            completed = CompletedFrameData(
                schema, output.table.loc[:, [c.slot_id for c in schema.columns]], attachments
            )
            if declaration_keys is not None:
                from marivo.analysis.core.frame_derivation import key_checkpoint

                declaration_keys[input_id] = key_checkpoint(completed)
            return replace(
                StageOutput.from_completed(completed),
                receipts=output.receipts,
                attribute_inputs=output.attribute_inputs,
            )

        def observe_read(stage: SourceRead, request: LoweredRead, output: StageOutput) -> None:
            input_id = source_stage_ids[stage.stage_id]
            attribute_layout = read_attribute(stage)
            binding = source_frames[input_id]._backing
            assert isinstance(binding, ExpressionFrameBacking)
            dynamic = (
                tuple(
                    evaluated_keys[computation_fingerprint(domain)]
                    for domain in parameter_domains(binding.root.graph)
                    if isinstance(domain.root, (FrameKeys, AttributeKeys))
                )
                if isinstance(binding.root, SourceFrameBinding)
                else ()
            )
            for logical in stage.native_reads or (stage,):
                period = logical.period
                facts = OutputEvidence(
                    "read",
                    "Actual explicit source read; source completeness and cross-source snapshot consistency were not audited.",
                    (
                        ("input_id", input_id),
                        ("source", logical.source_id),
                        ("physical_stage", stage.stage_id),
                        ("read_completed_at", datetime.now(timezone.utc)),
                        (
                            "period_start",
                            None
                            if period is None or period.start is None
                            else _plain(period.start),
                        ),
                        ("period_end", None if period is None else _plain(period.end)),
                        ("timezone", None if period is None else period.timezone),
                        ("filters", source_scope(logical.predicates)),
                        ("source_scopes", source_scope((*scopes.get(input_id, ()), *dynamic))),
                        (
                            "returned_rows",
                            output.source_rows
                            if output.source_rows is not None
                            else len(output.table),
                        ),
                        *(
                            (
                                ("attribute_read_role", attribute_layout.scope),
                                ("attribute_read_policy", "independent_reads/v1"),
                            )
                            if attribute_layout is not None
                            else ()
                        ),
                    ),
                )
                source_facts.append(facts)
            for name, check in request.numeric_checks:
                if not any(column.slot_id == name for column in stage.columns):
                    fact = OutputEvidence(
                        "check",
                        "Actual source numeric check passed.",
                        (
                            ("input_id", input_id),
                            ("check", name),
                            ("scope", stage.stage_id),
                            ("expected", check.expected),
                            ("result", "passed"),
                        ),
                    )
                    source_facts.append(fact)
                    checks.append(fact)
            if attribute_layout is not None:
                numeric_checks = {name for name, _ in request.numeric_checks}
                for receipt in output.receipts:
                    if receipt.check_id in numeric_checks:
                        continue
                    fact = OutputEvidence(
                        "check",
                        "Attribute operation constraint passed for this independent source read.",
                        (
                            ("input_id", input_id),
                            ("check", receipt.check_id),
                            ("stage", receipt.stage_id),
                            ("attribute_read_role", attribute_layout.scope),
                            ("policy", "independent_reads/v1"),
                            ("result", "passed"),
                        ),
                    )
                    source_facts.append(fact)
                    checks.append(fact)

        def observe_local(stage: LocalCompute | FrameCompute, output: StageOutput) -> None:
            if (
                declaration_keys is not None
                and stage.stage_id in operation_stages
                and output.frame_schema is not None
            ):
                from marivo.analysis.core.frame_derivation import key_checkpoint

                declaration_keys[operation_stages[stage.stage_id]] = key_checkpoint(
                    CompletedFrameData(output.frame_schema, output.table, output.frame_attachments)
                )
            method_productions.extend(
                (stage.stage_id, payload) for payload in output.attribute_methods
            )
            from marivo.analysis.core.attribute import (
                AttributeContributionProgram,
                AttributeInputProgram,
                AttributeProjectProgram,
            )

            if isinstance(stage, FrameCompute) and isinstance(stage.program, AttributeInputProgram):
                for receipt in output.receipts:
                    checks.append(
                        OutputEvidence(
                            "check",
                            "Independent Attribute role inputs passed complete-key and source-use mapping into the fixed parent domain.",
                            (
                                ("check", receipt.check_id),
                                ("stage", receipt.stage_id),
                                ("policy", "independent_reads/v1"),
                                ("result", "passed"),
                            ),
                        )
                    )

            if isinstance(stage, FrameCompute) and isinstance(
                stage.program, AttributeContributionProgram
            ):
                target = stage.program.operation.target
                assert target is not None
                checks.append(
                    OutputEvidence(
                        "check",
                        "Requested final Attribute target passed closure within the target tolerance against independently read parent totals; source consistency was not audited.",
                        (
                            ("stage", stage.stage_id),
                            ("check", "attribute_closure"),
                            ("policy", "attribute_closure/v2"),
                            ("target", ".".join(target.path)),
                            ("mode", target.mode),
                            (
                                "parent_count",
                                sum(len(payload.parents) for payload in output.attribute_methods),
                            ),
                            ("result", "passed"),
                        ),
                    )
                )

            if isinstance(stage, FrameCompute) and isinstance(
                stage.program, AttributeProjectProgram
            ):
                identifier = stage.stage_id.removeprefix("attribute-bridge:")
                records = tuple(
                    tuple(row.values())
                    for row in pa.Table.from_pandas(output.table, preserve_index=False).to_pylist()
                )
                if not output.columns:
                    records = ((),) if len(output.table) else ()
                evaluated_keys[identifier] = source_scope(
                    (
                        "attribute_parent_selection",
                        stage.program.operation.side,
                        output.columns,
                        records,
                    )
                )
            if (
                isinstance(stage, LocalCompute)
                and stage.stage_id.startswith("bridge:")
                and isinstance(stage.program, ResultProgram)
                and isinstance(stage.program.operation, PopulationValues)
            ):
                definition = stage.program.operation.definition
                assert isinstance(definition, PopulationProgram) and isinstance(
                    definition.root, FrameKeys
                )
                current = collected[definition.root.input_id].frame
                backing = current._backing
                kind = (
                    "expression"
                    if isinstance(backing, ExpressionFrameBacking)
                    else "memory"
                    if isinstance(backing, MemoryFrameBacking)
                    else "stored"
                )
                result_id = (
                    None
                    if isinstance(backing, ExpressionFrameBacking)
                    else backing.metadata.result_id
                )
                # Exact types are recorded in output.columns. Temporal cells
                # use physical ticks so audit cannot alias valid minima to NaT
                # or narrow the accepted range to Python datetime/date.
                arrays = tuple(
                    pa.array(output.table[column.slot_id], from_pandas=True)
                    for column in output.columns
                )
                values = tuple(
                    array.cast(pa.int32()).to_pylist()
                    if pa.types.is_date32(array.type)
                    else array.cast(pa.int64()).to_pylist()
                    if pa.types.is_timestamp(array.type) or pa.types.is_duration(array.type)
                    else array.to_pylist()
                    for array in arrays
                )
                rows = (
                    tuple(zip(*values, strict=True))
                    if values
                    else (((),) if len(output.table) else ())
                )
                evaluated_keys[computation_fingerprint(definition)] = source_scope(
                    (
                        "frame_key_selection",
                        definition.root,
                        kind,
                        result_id,
                        output.columns,
                        rows,
                    )
                )
            checks.append(
                OutputEvidence(
                    "check",
                    "Current kernel output types, key uniqueness and demanded numerical checks passed.",
                    (
                        ("stage", stage.stage_id),
                        ("inputs", source_scope(applicable[stage.stage_id])),
                        ("result", "passed"),
                    ),
                )
            )

        try:
            output = run_frame_plan(
                compiled,
                source_prepared=prepared_by_input,
                read_fixed=read_fixed,
                adapt_source=adapt_source,
                observe_read=observe_read,
                observe_local=observe_local,
            )
        except SemanticExpressionError as error:
            from marivo.analysis.core.attribute import AttributeFailureContext

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
        FrameAudit(
            tuple(dict.fromkeys(source_facts)),
            EvaluationRecord(
                datetime.now(timezone.utc), tuple(inputs), tuple(operations), tuple(checks)
            ),
        ),
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
    inherited = tuple(fact for input_id in fixed_frames for fact in fixed_source_facts[input_id])
    metadata = replace(
        metadata,
        audit=replace(
            metadata.audit, source_facts=tuple(dict.fromkeys((*inherited, *source_facts)))
        ),
    )
    return CompletedFrameResult(data, metadata, method_facts)


def evaluate_frame(
    frame: AnalysisFrame, *, declaration_keys: dict[str, CompletedFrameData] | None = None
) -> CompletedFrameResult:
    result = _evaluate_frame(frame, declaration_keys=declaration_keys)
    assert isinstance(result, CompletedFrameResult)
    return result


def evaluate_attribute_inputs(request: AttributeInputRequest) -> StageOutput:
    """Run private preparation in the ordinary Frame plan and source schedule."""
    result = _evaluate_frame(request.parent, request)
    assert isinstance(result, StageOutput)
    return result
