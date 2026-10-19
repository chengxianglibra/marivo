"""One runless evaluation over explicit source and completed-data inputs."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from types import TracebackType
from typing import TYPE_CHECKING
from uuid import uuid4

from marivo._definition.query import FilterValues, SelectValues, TopValues
from marivo.analysis.compiler.frame_plan import CompiledSourceFrame, plan_frame
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
from marivo.analysis.compiler.queries import endpoint
from marivo.analysis.compiler.query_program import QueryContext
from marivo.analysis.core.artifact_data import (
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
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
    CompareFrameValues,
    FixedFrameInput,
    FrameExpression,
    FrameOperation,
    SourceFrameInput,
)
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
from marivo.analysis.materialization.query_correspondence import prepare_context

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


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
                nullable=False if role in ("identity", "calendar") else columns[key].nullable,
            )
            for key, constraint in zip(keys, constraints, strict=True)
        )
        if isinstance(captured, CalendarDataAxis):
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
                help_target="frame.compute",
            ) from error


def evaluate_frame(frame: AnalysisFrame) -> CompletedFrameResult:
    """Evaluate once, close all sources, then return independent data and facts."""
    source_frames: dict[str, AnalysisFrame] = {}
    fixed_frames: dict[str, AnalysisFrame] = {}
    expressions: dict[str, FrameExpression] = {}
    inherited: list[OutputEvidence] = []
    operations: list[FrameOperationName] = []

    def collect(current: AnalysisFrame) -> FrameExpression:
        if current._local.owner != frame._local.owner:
            reject(
                "explicit inputs from one Store and Session",
                current._input_id,
                "Use inputs owned by this Frame's Session.",
                "frame.compute",
            )
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
                    "align"
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
                operations.append(operation)
            else:
                source_frames[current._input_id] = current
                expression = SourceFrameInput(current._input_id, root.graph)
        else:
            assert isinstance(current._schema, DataSchema)
            fixed_frames[current._input_id] = current
            inherited.extend(backing.metadata.audit.source_facts)
            expression = FixedFrameInput(current._input_id, current._schema)
        expressions[current._input_id] = expression
        return expression

    root_expression = collect(frame)
    prepared_by_input: dict[str, PreparedSources] = {}
    source_plans: dict[str, CompiledSourceFrame] = {}
    source_schemas: dict[str, DataSchema] = {}
    inputs: list[EvaluationInput] = []
    source_facts: list[OutputEvidence] = list(inherited)
    checks: list[OutputEvidence] = []
    scopes: dict[str, tuple[str, ...]] = {}
    with _SourceContexts() as stack:
        for input_id, current in source_frames.items():
            backing = current._backing
            assert isinstance(backing, ExpressionFrameBacking)
            binding = backing.root
            assert isinstance(binding, (SourceFrameBinding, LiteralFrameBinding))
            memo = InvocationMemo()
            prepared = None
            context = QueryContext()
            schemas: tuple[SourceSchema, ...] = ()
            if isinstance(binding, SourceFrameBinding):
                if _query_entities(binding.graph, binding.state):
                    service = stack.enter_context(binding.connection.operation())
                    prepared = stack.enter_context(
                        open_query_sources(service, binding.graph, binding.state, memo=memo)
                    )
                    prepared_by_input[input_id] = prepared
                    schemas, context = prepared.schemas, prepared.context
                else:
                    schemas = prepare_source_free_populations(binding.graph, binding.state)
                    context = prepare_context(binding.graph, binding.state)
                scopes[input_id] = captured_source_scopes(binding.graph)
            plan = optimize_query(plan_query(binding.graph, schemas, context=context, memo=memo))
            lowered = lower_query(plan, {} if prepared is None else prepared.relations)
            if prepared is not None:
                from marivo.analysis.materialization.query_key_fusion import (
                    fuse_query_keys,
                    fuse_query_terminal,
                )
                from marivo.analysis.materialization.source_original import fuse_source_original
                from marivo.analysis.materialization.source_original_dependent import (
                    fuse_dependent_original,
                )

                lowered = fuse_dependent_original(prepared, lowered)
                lowered = fuse_source_original(prepared, lowered)
                lowered = fuse_query_keys(prepared, lowered)
                lowered = fuse_query_terminal(prepared, lowered)
                qualify_query_sources(prepared, lowered)
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
        compiled = plan_frame(root_expression, source_plans)
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
            backing = fixed_frames[stage.input_id]._backing
            if isinstance(backing, MemoryFrameBacking):
                return backing.data
            reject(
                "the full stored Frame reader",
                stage.input_id,
                "Stored input evaluation joins the Store 12 reader before public cutover.",
                "frame.compute",
            )

        def adapt_source(input_id: str, output: StageOutput) -> StageOutput:
            current = source_frames[input_id]
            backing = current._backing
            assert isinstance(backing, ExpressionFrameBacking)
            binding = backing.root
            attachments = (
                tuple(calendar.directory for calendar in binding.calendars)
                if isinstance(binding, SourceFrameBinding)
                else ()
            )
            schema = source_schemas[input_id]
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
            )

        def observe_read(request: LoweredRead, output: StageOutput) -> None:
            input_id = source_stage_ids[request.stage.stage_id]
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
                        ("source_scopes", source_scope(scopes.get(input_id, ()))),
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

        output = run_frame_plan(
            compiled,
            source_prepared=prepared_by_input,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=observe_read,
            observe_local=observe_local,
        )
        data = CompletedFrameData(compiled.schema, output.table, output.frame_attachments)
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
    return CompletedFrameResult(data, metadata)
