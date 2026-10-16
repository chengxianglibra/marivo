"""Query invocation and atomic terminal publication under DatasetRuntime."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime
from typing import TYPE_CHECKING, NoReturn
from uuid import uuid4

import pandas as pd

from marivo._definition.expressions import (
    FieldCompare,
    FieldIn,
    FieldIsNull,
    FieldPredicate,
    PredAnd,
    PredNot,
    PredOr,
    RowCompare,
)
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import LocalCompute, plan_query
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    ArtifactPublication,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    OutputAttachment,
    OutputEvidence,
    SavedAttachment,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.errors import AnalysisError, AnalysisRepair, AnalysisStageUnavailableError
from marivo.analysis.evidence._dataset_types import ArtifactDigest, FindingPage
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import RunFailure, RunFailurePhase
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    execute_numeric_query,
    open_query_sources,
)
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


def reading_pending(target: str) -> NoReturn:
    raise AnalysisStageUnavailableError(
        stage="artifact_reading",
        expected="the R2.6 terminal output reader",
        received="committed R2.5 output; public reading is not delivered",
        repair="Keep the exact Artifact ID. R2.6 will provide metadata opening, preview, export and output record reading.",
        target=target,
    )


class _PendingReader:
    def show(self, manifest: ArtifactManifest, *, n: int) -> None:
        reading_pending("Artifact.show")

    def to_pandas(self, manifest: ArtifactManifest) -> pd.DataFrame:
        reading_pending("Artifact.to_pandas")

    def findings(
        self, manifest: ArtifactManifest, *, limit: int, cursor: str | None
    ) -> FindingPage:
        reading_pending("Artifact.findings")

    def evidence_digest(self, manifest: ArtifactManifest) -> ArtifactDigest:
        reading_pending("Artifact.evidence_digest")


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


def _predicate_summary(predicate: FieldPredicate) -> str:
    if isinstance(predicate, FieldCompare):
        return f"{predicate.field.ref.path} {predicate.op} {predicate.value!r}"
    if isinstance(predicate, FieldIsNull):
        return f"{predicate.field.ref.path} is null"
    if isinstance(predicate, FieldIn):
        return f"{predicate.field.ref.path} in explicit selection ({len(predicate.values)} values)"
    if isinstance(predicate, PredNot):
        return f"not ({_predicate_summary(predicate.operand)})"
    if isinstance(predicate, (PredAnd, PredOr)):
        children = (" and " if isinstance(predicate, PredAnd) else " or ").join(
            _predicate_summary(item) for item in predicate.operands
        )
        return f"({children})"
    assert isinstance(predicate, RowCompare)
    return f"derived row condition {predicate.op} {predicate.value!r}"


def _compute(
    prepared: PreparedSources | None, lowered: LoweredQuery, evidence: list[OutputEvidence]
) -> StageOutput:
    def record_read(request: LoweredRead, output: StageOutput) -> None:
        stage = request.stage
        if prepared is None:
            raise invalid("source read requires prepared connections")
        period = stage.period
        filters = "; ".join(_predicate_summary(item) for item in stage.predicates)
        evidence.append(
            OutputEvidence(
                "read",
                "Actual contribution read; source completeness and cross-source snapshot consistency were not audited.",
                (
                    ("source", stage.source_id),
                    (
                        "datasource",
                        prepared.sources.get(stage.source_id, prepared.source).datasource.name,
                    ),
                    ("timezone", None if period is None else period.timezone),
                    ("contribution", stage.stage_id),
                    ("time_constraint", "none added" if period is None else "half-open period"),
                    (
                        "period_start",
                        None
                        if period is None
                        else datetime.fromisoformat(period.start.isoformat()),
                    ),
                    (
                        "period_end",
                        None if period is None else datetime.fromisoformat(period.end.isoformat()),
                    ),
                    ("filters", filters[:4096] if filters else "none added"),
                    ("returned_rows", len(output.table)),
                    ("fact_source", "actual source execution"),
                ),
            )
        )
        for name, check in request.numeric_checks:
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

    def record_local(stage: LocalCompute, output: StageOutput) -> None:
        evidence.append(
            OutputEvidence(
                "check",
                "Local numeric evaluation and output type validation completed.",
                (("version", 1), ("scope", stage.stage_id), ("result", "passed")),
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
    values = tuple(column for column in output.columns if column.slot_id not in output.keys)
    attachments: list[OutputAttachment] = []
    calendar = next(
        (axis for axis in graph.signature.axes if isinstance(axis, CalendarCoordinate)), None
    )
    axes = tuple(
        CalendarDataAxis(f"axis.{index}", (column,), calendar.timezone)
        if calendar is not None and column.slot_id == "key.0"
        else DataAxis(f"axis.{index}", (column,))
        for index, column in enumerate(key_columns)
    )
    if calendar is not None:
        columns = (
            DataColumn("key.0", f"calendar:{calendar.grain}", ScalarDataType("int64"), False),
        )
        bounds = tuple(
            DataColumn(name, name, TimestampDataType("us", "UTC"), False)
            for name in ("nominal_start", "nominal_end", "actual_start", "actual_end")
        )
        schema = DataSchema((CalendarDataAxis("axis.0", columns, calendar.timezone),), bounds)
        rows = pd.DataFrame(
            {
                "key.0": pd.Series(range(len(calendar.cells)), dtype="int64[pyarrow]"),
                **{
                    name: pd.Series(
                        [cell[index] for cell in calendar.cells],
                        dtype="timestamp[us, tz=UTC][pyarrow]",
                    )
                    for index, name in enumerate(column.slot_id for column in bounds)
                },
            }
        )
        attachments.append(OutputAttachment("calendar_bounds", schema, rows))
    return ArtifactPublication(
        artifact,
        graph.signature.owner.session_id,
        run,
        DataSchema(axes, values),
        output.table,
        tuple(attachments),
        evidence=evidence,
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
                observation = graph.signature.observation
                has_sources = observation is not None and bool(observation.contributions)
                prepared = None
                if has_sources:
                    if state is None:
                        raise invalid("source Query requires its prepared semantic scope")
                    prepared = stack.enter_context(
                        open_query_sources(runtime.connection_service(), graph, state)
                    )
                lowered = lower_query(
                    plan_query(graph, () if prepared is None else prepared.schemas),
                    {} if prepared is None else prepared.relations,
                )
                if lowered.plan.checks:
                    raise invalid("Query check route is not implemented; no business row was read")
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
                evidence: list[OutputEvidence] = []
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
                ArtifactData(publication.schema, primary, attachments=tuple(attachments)),
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
            return Artifact._from_manifest(value, _PendingReader())
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
                            return Artifact._from_manifest(saved, _PendingReader())
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
