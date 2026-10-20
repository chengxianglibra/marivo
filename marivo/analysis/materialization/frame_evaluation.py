"""One runless evaluation over explicit source and completed-data inputs."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from types import TracebackType
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa

from marivo._definition.graph import computation_fingerprint
from marivo._definition.query import FilterValues, PopulationValues, SelectValues, TopValues
from marivo._definition.relations import AttributeKeys, FrameKeys, PopulationProgram
from marivo._errors import MarivoError
from marivo.analysis.compiler.frame_plan import (
    CompiledFramePlan,
    CompiledSourceFrame,
    frame_expression_schema,
    plan_frame,
)
from marivo.analysis.compiler.graph_lowering import LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    QueryPlan,
    SourceRead,
    SourceSchema,
    plan_query,
)
from marivo.analysis.compiler.optimization import InvocationMemo, optimize_query
from marivo.analysis.compiler.parameters import parameter_domains
from marivo.analysis.compiler.queries import endpoint
from marivo.analysis.core.artifact_data import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    IntervalDataAxis,
    OutputEvidence,
)
from marivo.analysis.core.frame_audit import (
    EvaluationInput,
    EvaluationRecord,
    FrameAudit,
    FrameOperationName,
    FrameResultMetadata,
    LiteralInput,
    MemoryInput,
    SourceInput,
    StoredInput,
)
from marivo.analysis.core.frame_calendar import FrozenSourceCalendar
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    LiteralFrameBinding,
    MemoryFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    AttributeContributionValues,
    AttributeFrameValues,
    AttributeInputsValues,
    CompareFrameValues,
    FixedFrameInput,
    FrameExpression,
    FrameOperation,
    SourceFrameInput,
)
from marivo.analysis.core.frame_key_inputs import FrameKeyInput, merge_frame_key_inputs
from marivo.analysis.core.frame_method_facts import FrameMethodFacts, same_method_facts
from marivo.analysis.core.frame_source_facts import captured_source_scopes, source_scope
from marivo.analysis.core.frame_structure import PendingDataColumn, PendingDataSchema, axis_role
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    _query_entities,
    open_query_sources,
    prepare_source_free_populations,
    qualify_query_sources,
    run_frame_plan,
)
from marivo.analysis.materialization.query_runtime_types import effective_frame_schema
from marivo.analysis.methods.frame_method_facts import validate_method_facts
from marivo.semantic.errors import SemanticExpressionError

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute import AttributeInputRequest
    from marivo.analysis.core.attribute import AttributeMethodPayload, AttributeMethodProduction
    from marivo.analysis.dsl.frame import AnalysisFrame


def _attribute_productions(
    compiled: CompiledFramePlan,
    explicit_inputs: tuple[tuple[str, FrameExpression], ...],
    observed: tuple[tuple[str, AttributeMethodPayload], ...],
) -> tuple[AttributeMethodProduction, ...]:
    """Retain actual reachable producers in explicit Frame operand order."""
    from marivo.analysis.core.attribute import AttributeMethodProduction

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
    payloads = dict(observed)
    result: list[AttributeMethodProduction] = []
    seen: set[str] = set()
    for index, (input_id, expression) in enumerate(explicit_inputs):
        identifier = by_expression.get(id(expression))
        if (
            identifier is None
            or identifier not in payloads
            or identifier not in reachable
            or identifier in seen
        ):
            continue
        seen.add(identifier)
        result.append(
            AttributeMethodProduction(
                identifier,
                input_id,
                index,
                identifier == compiled.lowered.plan.publish,
                payloads[identifier],
            )
        )
    assert seen == set(payloads) & reachable
    return tuple(result)


def _plain(value: datetime) -> datetime:
    return datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())


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


def _evaluate_frame(
    frame: AnalysisFrame, request: AttributeInputRequest | None = None
) -> CompletedFrameResult | StageOutput:
    """Evaluate once, close all sources, then return independent data and facts."""
    source_frames: dict[str, AnalysisFrame] = {}
    fixed_frames: dict[str, AnalysisFrame] = {}
    completed_inputs: dict[str, CompletedFrameResult] = {}
    expressions: dict[str, FrameExpression] = {}
    inherited: list[OutputEvidence] = []
    operations: list[FrameOperationName] = []

    method_productions: list[tuple[str, AttributeMethodPayload]] = []
    collected: dict[str, FrameKeyInput] = {}
    from marivo.analysis.binding.attribute_rules import AttributeRule

    contribution_requests: dict[
        str, tuple[int, tuple[tuple[str, AttributeRule], ...], AttributeFrameValues]
    ] = {}

    def complete_input(input_id: str) -> CompletedFrameResult:
        previous = completed_inputs.get(input_id)
        if previous is not None:
            return previous
        current = fixed_frames[input_id]
        backing = current._backing
        if isinstance(backing, MemoryFrameBacking):
            result = backing.result
        else:
            assert isinstance(backing, StoredFrameBacking)
            result = current._local.read_complete(backing)
        for facts in result.method_facts:
            validate_method_facts(facts)
        completed_inputs[input_id] = result
        return result

    def collect(current: AnalysisFrame) -> FrameExpression:
        if current._local.owner != frame._local.owner:
            reject(
                "explicit inputs from one Store and Session",
                current._input_id,
                "Use inputs owned by this Frame's Session.",
                "frame.compute",
            )
        previous = collected.get(current._input_id)
        carrier = FrameKeyInput(current._input_id, current)
        merge_frame_key_inputs(
            () if previous is None else (previous,), (carrier,), owner=frame._local.owner
        )
        collected.setdefault(current._input_id, carrier)
        existing = expressions.get(current._input_id)
        if existing is not None:
            return existing
        backing = current._backing
        if isinstance(backing, ExpressionFrameBacking):
            root = backing.root
            if isinstance(root, OperationFrameBinding):
                inputs = tuple(collect(child) for child in root.inputs)
                expression: FrameExpression = FrameOperation(inputs, root.operation)
                operation: FrameOperationName = (
                    "attribute"
                    if isinstance(root.operation, AttributeFrameValues)
                    else "align"
                    if isinstance(root.operation, AlignFrameValues)
                    else "compare"
                    if isinstance(root.operation, CompareFrameValues)
                    else "select"
                    if isinstance(root.operation, SelectValues)
                    else "where"
                    if isinstance(root.operation, FilterValues)
                    else "top"
                    if isinstance(root.operation, TopValues)
                    else "reduce"
                )
                if isinstance(root.operation, AttributeContributionValues):
                    operation = "attribute"
                    for side, endpoint_frame in enumerate(root.inputs[1:]):
                        contribution_requests[endpoint_frame._input_id] = (
                            side,
                            root.operation.rules[side],
                            root.operation.mapping,
                        )
                operations.append(operation)
            else:
                if isinstance(root, SourceFrameBinding):
                    for dependency in root.frame_key_inputs:
                        collect(dependency.frame)
                source_frames[current._input_id] = current
                expression = SourceFrameInput(current._input_id, root.graph)
        else:
            assert isinstance(current._schema, DataSchema)
            fixed_frames[current._input_id] = current
            inherited.extend(backing.metadata.audit.source_facts)
            expression = FixedFrameInput(current._input_id, current._schema)
        expressions[current._input_id] = expression
        return expression

    if request is None:
        root_expression = collect(frame)
    else:
        root_expression = FrameOperation(
            tuple(collect(item) for item in (request.parent, *request.endpoints)),
            AttributeInputsValues(request.operation),
        )
    requested = (
        {}
        if request is None
        else {
            endpoint._input_id: (side, rules)
            for side, (endpoint, rules) in enumerate(
                zip(request.endpoints, request.rules, strict=True)
            )
        }
    )
    for input_id, (side, rules, _) in contribution_requests.items():
        requested[input_id] = (side, rules)
    prepared_by_input: dict[str, PreparedSources] = {}
    source_plans: dict[str, CompiledSourceFrame] = {}
    source_schemas: dict[str, DataSchema] = {}
    key_schema_cache: dict[int, DataSchema] = {}
    inputs: list[EvaluationInput] = []
    source_facts: list[OutputEvidence] = list(inherited)
    checks: list[OutputEvidence] = []
    scopes: dict[str, tuple[str, ...]] = {}
    evaluated_keys: dict[str, str] = {}
    with _SourceContexts() as stack:
        for input_id, current in source_frames.items():
            backing = current._backing
            assert isinstance(backing, ExpressionFrameBacking)
            binding = backing.root
            assert isinstance(binding, (SourceFrameBinding, LiteralFrameBinding))
            memo = InvocationMemo()
            prepared = None
            schemas: tuple[SourceSchema, ...] = ()
            if isinstance(binding, SourceFrameBinding):
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
                from marivo.analysis.binding.attribute_rules import component_requests
                from marivo.analysis.compiler.attribute_inputs import (
                    bundle_attribute_plan,
                    input_failure,
                )
                from marivo.analysis.materialization.graph_source_execution import (
                    prepare_attribute_sources,
                    qualify_original_source,
                )

                assert prepared is not None and isinstance(binding, SourceFrameBinding)
                side, rules = requested[input_id]
                mapping = (
                    request.operation if request is not None else contribution_requests[input_id][2]
                )
                positions = mapping.left_parents if side == 0 else mapping.right_parents
                prepared, scope_columns = prepare_attribute_sources(
                    prepared, binding.graph, binding.state, len(positions)
                )
                prepared_by_input[input_id] = prepared
                schemas = prepared.schemas
                original_plan = plan_query(binding.graph, schemas, memo=memo, require_original=True)
                original_reads = tuple(
                    stage
                    for stage in original_plan.stages
                    if isinstance(stage, SourceRead) and stage.component_layout is not None
                )
                for root in dict.fromkeys(stage.source_id for stage in original_reads):
                    group = tuple(stage for stage in original_reads if stage.source_id == root)
                    for bound in prepared.bound.values():
                        issue = qualify_original_source(prepared, group, bound.relation)
                        if issue is not None:
                            input_failure("unqualified_contribution_route", issue)
                parent_keys = tuple(
                    key for position in positions for key in original_plan.output_groups[position]
                )
                plan = bundle_attribute_plan(
                    original_plan,
                    tuple(item for _, rule in rules for item in component_requests(rule)),
                    parent_keys,
                    scope_columns,
                    mapping.left_projection if side == 0 else mapping.right_projection,
                    "left" if side == 0 else "right",
                    tuple(name for name, _ in rules),
                )
            else:
                plan = optimize_query(plan_query(binding.graph, schemas, memo=memo))
            lowered = lower_query(plan, {} if prepared is None else prepared.relations)
            if input_id in requested:
                effective_reads = {item.stage.stage_id: item.stage for item in lowered.reads}
                lowered = replace(
                    lowered,
                    plan=replace(
                        lowered.plan,
                        stages=tuple(
                            effective_reads.get(stage.stage_id, stage)
                            for stage in lowered.plan.stages
                        ),
                    ),
                )
            if prepared is not None and input_id not in requested:
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
            source_plans[input_id] = CompiledSourceFrame(lowered, schema)
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
        compiled = plan_frame(root_expression, source_plans, frame_inputs=expressions)
        source_stage_ids = dict(compiled.source_stages)
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
            return complete_input(stage.input_id).data

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
            return StageOutput.from_frame(
                completed.copy_rows(),
                schema.columns,
                schema.key_fields,
                receipts=output.receipts,
                key_groups=tuple(tuple(c.slot_id for c in axis.columns) for axis in schema.axes),
                frame_attachments=completed.copy_attachments(),
                attribute_inputs=output.attribute_inputs,
                frame_schema=schema,
            )

        def observe_read(request: LoweredRead, output: StageOutput) -> None:
            input_id = source_stage_ids[request.stage.stage_id]
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
            for logical in request.stage.native_reads or (request.stage,):
                period = logical.period
                facts = OutputEvidence(
                    "read",
                    "Actual explicit source read; source completeness and cross-source snapshot consistency were not audited.",
                    (
                        ("input_id", input_id),
                        ("source", logical.source_id),
                        ("physical_stage", request.stage.stage_id),
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
                    ),
                )
                source_facts.append(facts)
            for name, check in request.numeric_checks:
                if not any(column.slot_id == name for column in request.stage.columns):
                    fact = OutputEvidence(
                        "check",
                        "Actual source numeric check passed.",
                        (
                            ("input_id", input_id),
                            ("check", name),
                            ("scope", request.stage.stage_id),
                            ("expected", check.expected),
                            ("result", "passed"),
                        ),
                    )
                    source_facts.append(fact)
                    checks.append(fact)

        def observe_local(stage: LocalCompute | FrameCompute, output: StageOutput) -> None:
            method_productions.extend(
                (stage.stage_id, payload) for payload in output.attribute_methods
            )
            from marivo.analysis.core.attribute import AttributeProjectProgram

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
                and stage.result is not None
                and isinstance(stage.result.operation, PopulationValues)
            ):
                definition = stage.result.operation.definition
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
    # Inert producer associations survive source cleanup. P4 attaches origins here,
    # preserving distinct ancestor producers before constructing the final result.
    completed_method_productions = _attribute_productions(
        compiled,
        tuple((identifier, expressions[identifier]) for identifier in collected),
        tuple(method_productions),
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
    own_facts: dict[str, FrameMethodFacts] = {}
    for production in completed_method_productions:
        payload = production.payload
        facts = FrameMethodFacts(
            kind="attribute",
            origin_result_id=metadata.result_id if production.is_final else "result_" + uuid4().hex,
            output_schema=payload.output_schema,
            coordinates=payload.coordinates,
            parent_axis_count=payload.parent_axis_count,
            parents=payload.parents,
        )
        validate_method_facts(facts)
        own_facts[production.input_id] = facts

    histories: dict[str, tuple[FrameMethodFacts, ...]] = {}

    def history(current: AnalysisFrame) -> tuple[FrameMethodFacts, ...]:
        previous = histories.get(current._input_id)
        if previous is not None:
            return previous
        backing = current._backing
        if not isinstance(backing, ExpressionFrameBacking):
            result = complete_input(current._input_id).method_facts
        else:
            binding = backing.root
            dependencies = (
                binding.inputs
                if isinstance(binding, OperationFrameBinding)
                else tuple(item.frame for item in binding.frame_key_inputs)
                if isinstance(binding, SourceFrameBinding)
                else ()
            )
            merged: dict[str, FrameMethodFacts] = {}
            own = own_facts.get(current._input_id)
            values = (() if own is None else (own,)) + tuple(
                facts for child in dependencies for facts in history(child)
            )
            for facts in values:
                known = merged.get(facts.origin_result_id)
                if known is not None and not same_method_facts(known, facts):
                    reject(
                        "identical complete method payloads for one origin",
                        facts.origin_result_id,
                        "Preserve the exact original method facts on every explicit input.",
                        "frame.method_facts",
                    )
                merged.setdefault(facts.origin_result_id, facts)
            result = tuple(merged.values())
        histories[current._input_id] = result
        return result

    return CompletedFrameResult(data, metadata, history(frame))


def evaluate_frame(frame: AnalysisFrame) -> CompletedFrameResult:
    result = _evaluate_frame(frame)
    assert isinstance(result, CompletedFrameResult)
    return result


def evaluate_attribute_inputs(request: AttributeInputRequest) -> StageOutput:
    """Run private preparation in the ordinary Frame plan and source schedule."""
    result = _evaluate_frame(request.parent, request)
    assert isinstance(result, StageOutput)
    return result
