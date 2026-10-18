"""Query invocation and atomic terminal publication under DatasetRuntime."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pandas as pd

from marivo._definition.types import CalendarCoordinate
from marivo.analysis._scope import predicate_summary, scope_summary
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import LocalCompute, SourceSchema, plan_query
from marivo.analysis.compiler.query_program import QueryContext
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    ArtifactPublication,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataProjection,
    DataSchema,
    OutputAttachment,
    OutputEvidence,
    SavedAttachment,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import RunFailure, RunFailurePhase
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_reading import TerminalReader
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    _query_entities,
    execute_numeric_query,
    open_query_sources,
    prepare_source_free_populations,
    qualify_query_sources,
)
from marivo.analysis.materialization.query_correspondence import prepare_context
from marivo.analysis.materialization.resources import (
    backend_reservation,
    discharge_resources,
    reserve_output,
)
from marivo.analysis.materialization.storage import _fsync_directory
from marivo.analysis.materialization.store import SessionStore, _new_run_ref, _one, _rows, _text
from marivo.analysis.materialization.terminal_protocol import invalid
from marivo.analysis.materialization.terminal_storage import write_output
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.semantic._compiled_state import CompiledSemanticState


def _failure(phase: RunFailurePhase) -> RunFailure:
    return RunFailure(
        phase=phase,
        kind="process_lost" if phase == "process_lost" else "execution_failed",
        safe_message="Query invocation failed before terminal publication.",
        safe_location=f"analysis.query.{phase}",
        expected="complete checked output and one atomic publication",
        received="execution or publication did not complete",
        repair=AnalysisRepair(
            kind="inspect",
            action="Inspect the original Run and its resource obligations before executing a new invocation.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
        ),
    )


def reconcile(
    store: SessionStore, session: str, event: Callable[[str], None], *, run_ref: str | None = None
) -> None:
    """Resolve original transaction outcomes before deleting exactly owned paths."""
    event("reconciliation")
    if store.session(session) is None:
        raise invalid("reconciliation Session is absent")
    with store._read() as conn:
        refs = _rows(
            conn,
            "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND (t.run_ref IS NULL OR EXISTS (SELECT 1 FROM action_resource_journal j WHERE j.run_ref=a.run_ref)) ORDER BY a.admitted_at,a.run_ref",
            (session,),
        )
        runs = tuple(terminal_store.run(store, conn, _text(row, "run_ref")) for row in refs)
        if (
            any(run is None for run in runs)
            or sum(run is not None and run.lifecycle == "incomplete" for run in runs) > 1
        ):
            raise invalid("inconsistent incomplete terminal Runs")
        if run_ref is not None:
            selected = terminal_store.run(store, conn, run_ref)
            if (
                selected is None
                or selected.session_ref != session
                or selected.lifecycle == "succeeded"
            ):
                raise invalid(
                    "reconciliation requires an exact incomplete or failed Run of this Session"
                )
        for run in runs:
            assert run is not None
            if run.lifecycle == "succeeded":
                if (
                    run.output_artifact_ref is None
                    or terminal_store.manifest(store, conn, run.output_artifact_ref) is None
                ):
                    raise invalid("succeeded Run lacks committed terminal output")
            elif (
                _one(
                    conn,
                    "SELECT 1 FROM dataset_artifacts WHERE session_ref=? AND invocation_id=?",
                    (session, run.invocation_id),
                )
                is not None
            ):
                raise invalid("non-success Run has conflicting publication metadata")
    for run in runs:
        assert run is not None
        if run_ref is not None and run.run_ref != run_ref:
            continue
        owned = tuple(
            resource for resource in store.resources(session) if resource.run_ref == run.run_ref
        )
        if run.lifecycle == "succeeded":
            if owned:
                raise invalid("succeeded terminal Run retains cleanup obligations")
            continue
        resolved = discharge_resources(store, owned)
        if run.lifecycle == "incomplete":
            store.fail(run.run_ref, _failure("process_lost"), resolved_resources=resolved)
        else:
            for resource in resolved:
                store.discharge(resource)


def _compute(
    prepared: PreparedSources | None, lowered: LoweredQuery, evidence: list[OutputEvidence]
) -> StageOutput:
    def record_read(request: LoweredRead, output: StageOutput) -> None:
        stage = request.stage
        if prepared is None:
            raise invalid("source read requires prepared connections")
        for logical in stage.native_reads or (stage,):
            period = logical.period
            filters = "; ".join(predicate_summary(item) for item in logical.predicates)
            evidence.append(
                OutputEvidence(
                    "read",
                    "Actual contribution read; source completeness and cross-source snapshot consistency were not audited.",
                    (
                        ("source", logical.source_id),
                        (
                            "datasource",
                            prepared.sources.get(
                                logical.source_id, prepared.source
                            ).datasource.name,
                        ),
                        ("timezone", None if period is None else period.timezone),
                        ("contribution", logical.stage_id),
                        ("physical_stage", stage.stage_id),
                        *(
                            (
                                (
                                    "logical_contributions",
                                    ";".join(member.stage_id for member in stage.common_reads),
                                ),
                            )
                            if stage.common_reads
                            else ()
                        ),
                        ("time_constraint", "none added" if period is None else "half-open period"),
                        (
                            "period_start",
                            None
                            if period is None or period.start is None
                            else datetime.fromisoformat(period.start.isoformat()),
                        ),
                        (
                            "period_end",
                            None
                            if period is None
                            else datetime.fromisoformat(period.end.isoformat()),
                        ),
                        ("filters", filters[:4096] if filters else "none added"),
                        (
                            "returned_rows",
                            output.source_rows
                            if output.source_rows is not None
                            else len(output.table),
                        ),
                        ("row_count_scope", "physical source submission"),
                        ("fact_source", "actual source execution"),
                    ),
                )
            )
        for name, check in request.numeric_checks:
            if any(column.slot_id == name for column in stage.columns):
                # These checks travel with their values. Only the local formula
                # consumer knows which coordinate/branch actually demands them.
                continue
            evidence.append(
                OutputEvidence(
                    "check",
                    check.expected.replace("\n", " ")[:4096],
                    (
                        ("version", 1),
                        ("check", name),
                        ("scope", stage.stage_id),
                        ("result", "passed"),
                    ),
                )
            )
        if stage.native_original_finish is not None:
            evidence.append(
                OutputEvidence(
                    "check",
                    "Demanded original formulas and sample checks passed in the existing numeric kernel.",
                    (
                        ("version", 1),
                        ("check", "original_finish"),
                        ("scope", stage.stage_id),
                        ("result", "passed"),
                    ),
                )
            )
        if stage.native_query_finish is not None:
            evidence.append(
                OutputEvidence(
                    "check",
                    "Complete endpoint keys, presence and demanded Query relation checks passed.",
                    (
                        ("version", 1),
                        ("check", "query_relation_finish"),
                        ("scope", stage.stage_id),
                        ("result", "passed"),
                    ),
                )
            )

    def record_local(stage: LocalCompute, output: StageOutput) -> None:
        for member in stage.fused_steps or (stage,):
            evidence.append(
                OutputEvidence(
                    "check",
                    "Demanded numeric values and branch-scoped checks passed; output types validated.",
                    (("version", 1), ("scope", member.stage_id), ("result", "passed")),
                )
            )

    return execute_numeric_query(
        prepared, lowered, observe_read=record_read, observe_local=record_local
    )


def _publication(
    graph: BoundGraph,
    output: StageOutput,
    artifact: str,
    run: str,
    evidence: tuple[OutputEvidence, ...],
) -> ArtifactPublication:
    key_columns = tuple(column for column in output.columns if column.slot_id in output.keys)
    layout = output.layout
    values = (
        layout.values
        if layout is not None
        else tuple(column for column in output.columns if column.slot_id not in output.keys)
    )
    presence = (
        tuple(layout.column(item.column) for item in layout.presence) if layout is not None else ()
    )
    projection_columns = tuple(
        column
        for column in output.columns
        if column.slot_id not in output.keys and column not in values and column not in presence
    )
    projections: list[DataProjection] = []
    if layout is not None:
        from marivo._definition.correspondence import CorrespondenceAxis

        for projection in layout.projections:
            candidates = tuple(
                index
                for index, axis in enumerate(layout.axes)
                if axis == projection.axis
                or (
                    isinstance(axis, CorrespondenceAxis)
                    and projection.axis in axis.projections.values()
                )
            )
            axis_index = candidates[0] if len(candidates) == 1 else 0
            axis = projection.axis
            projections.append(
                DataProjection(
                    f"axis.{axis_index}",
                    projection.columns,
                    projection.path,
                    "calendar"
                    if isinstance(axis, CalendarCoordinate)
                    else "correspondence"
                    if isinstance(axis, CorrespondenceAxis)
                    else "identity"
                    if axis.field is None
                    else "field",
                    layout.column(projection.columns[0]).name,
                    axis.timezone if isinstance(axis, CalendarCoordinate) else None,
                    tuple(
                        (
                            datetime.fromisoformat(cell[0].isoformat()),
                            datetime.fromisoformat(cell[1].isoformat()),
                            datetime.fromisoformat(cell[2].isoformat()),
                            datetime.fromisoformat(cell[3].isoformat()),
                        )
                        for cell in axis.cells
                    )
                    if isinstance(axis, CalendarCoordinate)
                    else (),
                )
            )
    attachments: list[OutputAttachment] = []
    from marivo._definition.query import (
        FilterValues,
        ResultQuery,
        SelectValues,
        ShareValues,
        TopValues,
    )

    def ordered(root: object) -> bool:
        return isinstance(root, ResultQuery) and (
            isinstance(root.operation, TopValues)
            or (
                isinstance(root.operation, (SelectValues, FilterValues, ShareValues))
                and ordered(root.inputs[0].query)
            )
        )

    if ordered(graph.root) and output.keys:
        canonical = output.table.sort_values(list(output.keys), kind="stable", na_position="first")
        positions = {int(original): index for index, original in enumerate(canonical.index)}
        position = DataColumn("position", "position", ScalarDataType("int64"), False)
        row_index = DataColumn("row_index", "row_index", ScalarDataType("int64"), False)
        order_schema = DataSchema((DataAxis("display_position", (position,)),), (row_index,))
        attachments.append(
            OutputAttachment(
                "display_order",
                order_schema,
                pd.DataFrame(
                    {
                        "position": range(len(output.table)),
                        "row_index": pd.Series(
                            [positions[index] for index in range(len(output.table))],
                            dtype="int64[pyarrow]",
                        ),
                    }
                ),
            )
        )
    axis_groups = output.key_groups or tuple((column.slot_id,) for column in key_columns)
    axis_columns = tuple(
        tuple(next(column for column in key_columns if column.slot_id == key) for key in group)
        for group in axis_groups
    )
    if len(axis_columns) != len(graph.signature.axes):
        raise invalid("output key groups do not match the declared logical axes")
    from marivo._definition.correspondence import CorrespondenceAxis
    from marivo._definition.types import AxisBinding

    def axis_kind(index: int) -> Literal["field", "member", "correspondence"]:
        axis = graph.signature.axes[index]
        return (
            "correspondence"
            if isinstance(axis, CorrespondenceAxis)
            else "member"
            if isinstance(axis, AxisBinding) and axis.field is None
            else "field"
        )

    axes = tuple(
        CalendarDataAxis(f"axis.{index}", columns, logical.timezone)
        if isinstance(logical, CalendarCoordinate)
        else DataAxis(f"axis.{index}", columns, kind=axis_kind(index))
        for index, (columns, logical) in enumerate(
            zip(axis_columns, graph.signature.axes, strict=True)
        )
    )
    for saved_axis, calendar in zip(axes, graph.signature.axes, strict=True):
        if not isinstance(calendar, CalendarCoordinate):
            continue
        if len(saved_axis.columns) != 1:
            raise invalid("calendar output must preserve exactly one complete bucket key")
        calendar_key = saved_axis.columns[0].slot_id
        bounds = tuple(
            DataColumn(name, name, TimestampDataType("us", "UTC"), False)
            for name in ("nominal_start", "nominal_end", "actual_start", "actual_end")
        )
        schema = DataSchema((saved_axis,), bounds)
        rows = pd.DataFrame(
            {
                calendar_key: pd.Series(range(len(calendar.cells)), dtype="int64[pyarrow]"),
                **{
                    name: pd.Series(
                        [cell[index] for cell in calendar.cells],
                        dtype="timestamp[us, tz=UTC][pyarrow]",
                    )
                    for index, name in enumerate(column.slot_id for column in bounds)
                },
            }
        )
        attachments.append(OutputAttachment(f"calendar_bounds.{saved_axis.token}", schema, rows))
    return ArtifactPublication(
        artifact,
        graph.signature.owner.session_id,
        run,
        DataSchema(axes, values, presence, projection_columns),
        output.table.loc[
            :,
            [
                column.slot_id
                for column in DataSchema(axes, values, presence, projection_columns).columns
            ],
        ],
        tuple(attachments),
        evidence=evidence,
        coordinate_projections=tuple(projections),
    )


def execute(
    runtime: DatasetRuntime, graph: BoundGraph, state: CompiledSemanticState | None
) -> Artifact:
    store, session, event = runtime.store, runtime.session_ref, runtime._event
    if (
        graph.signature.owner.session_id != session
        or graph.signature.owner.owner_id != store.store_id
    ):
        raise invalid("Query belongs to another Session or Store")
    run_ref: str | None = None
    committing = False
    value: ArtifactManifest | None = None
    phase: RunFailurePhase = "stage_execution"
    with session_writer_guard(store.layout.lock_path(session), session_ref=session):
        reconcile(store, session, event)
        try:
            with ExitStack() as stack:
                from marivo.analysis.compiler.optimization import (
                    InvocationMemo,
                    compilation_key,
                    optimize_query,
                )

                memo = InvocationMemo()
                observation = graph.signature.observation
                from marivo._definition.query import ResultQuery

                has_sources = isinstance(graph.root, ResultQuery) or (
                    observation is not None
                    and bool(
                        observation.contributions or observation.coordinates.population is not None
                    )
                )
                prepared = None
                local_schemas: tuple[SourceSchema, ...] = ()
                if has_sources:
                    if state is None:
                        raise invalid("source Query requires its prepared semantic scope")
                    if _query_entities(graph, state):
                        prepared = stack.enter_context(
                            open_query_sources(
                                runtime.connection_service(), graph, state, memo=memo
                            )
                        )
                    else:
                        local_schemas = prepare_source_free_populations(graph, state)
                schemas = local_schemas if prepared is None else prepared.schemas
                context = (
                    (prepare_context(graph, state) if state is not None else QueryContext())
                    if prepared is None
                    else prepared.context
                )
                relations = {} if prepared is None else prepared.relations
                plan = optimize_query(
                    plan_query(
                        graph,
                        schemas,
                        context=context,
                        memo=memo,
                    )
                )
                lowered = memo.lower(
                    compilation_key(
                        graph,
                        schemas,
                        context,
                        required=tuple(
                            f"{item.stage_id}:{slot}"
                            for item in plan.required_slots
                            for slot in item.columns
                        ),
                        relations=relations,
                    ),
                    lambda: lower_query(plan, relations),
                )
                if lowered.plan.checks:
                    raise invalid("Query check route is not implemented; no business row was read")
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
                run_ref = _new_run_ref()
                terminal_store.admit(store, session, run_ref)
                runtime.last_run_ref = run_ref
                event("query_admitted")
                backends = tuple(
                    backend_reservation(run_ref, f"query-source:{name}")
                    for name in (() if prepared is None else prepared.sources)
                )
                for resource in backends:
                    store.reserve(resource)
                evidence: list[OutputEvidence] = [
                    OutputEvidence(
                        "declaration",
                        "Captured logical input scope; bounded summary, not audited source completeness or an executable definition.",
                        (
                            ("definition", graph.fingerprint),
                            ("scope", scope_summary(graph.root, observation)),
                        ),
                    )
                ]
                output = _compute(prepared, lowered, evidence)
            # Source owners have closed successfully before any output is published.
            for resource in backends:
                store.discharge(resource)
            nonce = uuid4().hex
            artifact = "artifact_" + nonce
            publication = _publication(graph, output, artifact, run_ref, tuple(evidence))
            del output
            phase = "storage_staging"
            staging, final, resources = reserve_output(
                store, run_ref=run_ref, session_ref=session, artifact_ref=artifact, nonce=nonce
            )
            primary = write_output(
                store.project_root,
                staging / "primary",
                final / "primary",
                publication.schema,
                publication.rows,
            )
            event("terminal_primary_written")
            attachments: list[SavedAttachment] = []
            for index, attachment in enumerate(publication.attachments):
                locator = f"attachment-{index}"
                handle = write_output(
                    store.project_root,
                    staging / locator,
                    final / locator,
                    attachment.schema,
                    attachment.rows,
                )
                attachments.append(SavedAttachment(attachment.name, attachment.schema, handle))
                event("terminal_attachment_written")
            value = ArtifactManifest(
                artifact,
                session,
                run_ref,
                ArtifactData(
                    publication.schema,
                    primary,
                    coordinate_projections=publication.coordinate_projections,
                    attachments=tuple(attachments),
                ),
            )
            _fsync_directory(staging)
            final.parent.mkdir(parents=True, exist_ok=True)
            phase = "storage_finalization"
            os.rename(staging, final)
            _fsync_directory(final.parent)
            _fsync_directory(final.parent.parent)
            event("terminal_files_published")
            phase = "publication"
            committing = True
            terminal_store.publish(
                store, value, publication.findings, publication.evidence, resources, event
            )
            return Artifact._from_manifest(value, TerminalReader(store))
        except BaseException as failure:
            if committing:
                try:
                    with store._read() as conn:
                        original = terminal_store.run(store, conn, run_ref or "")
                        if original is None:
                            raise invalid("original terminal Run is unavailable")
                        if original.lifecycle == "succeeded":
                            saved = terminal_store.manifest(
                                store, conn, original.output_artifact_ref or ""
                            )
                            if saved != value or saved is None:
                                raise invalid("original terminal publication read-back differs")
                            terminal_store.confirm_records(
                                conn, saved.artifact_id, publication.findings, publication.evidence
                            )
                            return Artifact._from_manifest(saved, TerminalReader(store))
                        if (
                            value is not None
                            and _one(
                                conn,
                                "SELECT 1 FROM dataset_artifacts WHERE artifact_ref=? OR invocation_id=?",
                                (value.artifact_id, run_ref),
                            )
                            is not None
                        ):
                            raise invalid(
                                "original terminal Run has conflicting publication metadata"
                            )
                except Exception as error:
                    raise RecoveryPendingError(
                        expected="a confirmed original terminal publication outcome",
                        received="commit acknowledgement or read-back is unavailable",
                        repair="Preserve the original Run and journal; reconcile without replaying computation.",
                        stage="terminal_commit_unknown",
                        run_ref=run_ref,
                    ) from error
            if run_ref is not None:
                try:
                    with store._read() as conn:
                        original = terminal_store.run(store, conn, run_ref)
                    if original is not None and original.lifecycle == "incomplete":
                        owned = tuple(
                            resource
                            for resource in store.resources(session)
                            if resource.run_ref == run_ref
                        )
                        resolved = discharge_resources(store, owned)
                        store.fail(run_ref, _failure(phase), resolved_resources=resolved)
                except BaseException as cleanup_error:
                    raise failure from cleanup_error
            if isinstance(failure, AnalysisError):
                if failure.run_ref is None:
                    failure.run_ref = run_ref
                raise
            from marivo.semantic.errors import SemanticExpressionError

            if isinstance(failure, SemanticExpressionError):
                assert (
                    failure.expected is not None
                    and failure.received is not None
                    and failure.repair is not None
                )
                raise MaterializationError(
                    expected=failure.expected,
                    received=failure.received,
                    repair=failure.repair.action,
                    stage="numeric_value",
                    run_ref=run_ref,
                    help_target="Query.execute",
                ) from failure
            if not isinstance(failure, Exception):
                raise
            raise MaterializationError(
                expected="complete checked pandas output and an atomic terminal publication",
                received=f"Query failed during {phase}: {type(failure).__name__}",
                repair="Inspect the original Run and resource obligations, correct the failure, then execute a new invocation.",
                stage=phase,
                run_ref=run_ref,
                help_target="session.get_run" if run_ref else "Query.execute",
            ) from failure
