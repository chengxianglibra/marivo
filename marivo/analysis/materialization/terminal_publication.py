"""Publish one already completed Frame under the original Store transaction."""

from __future__ import annotations

import os
from collections.abc import Callable
from uuid import uuid4

from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    SavedAttachment,
    SavedFrameMethodFacts,
)
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    StoredFrameBacking,
)
from marivo.analysis.core.model import reject
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import RunFailure, RunFailurePhase
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.frame_method_facts import method_payload, saved_method_facts
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.resources import discharge_resources, reserve_output
from marivo.analysis.materialization.storage import _fsync_directory
from marivo.analysis.materialization.store import SessionStore, _new_run_ref, _one, _rows, _text
from marivo.analysis.materialization.terminal_protocol import invalid
from marivo.analysis.materialization.terminal_storage import write_output
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.methods.frame_method_facts import validate_method_facts
from marivo.introspection.live.model import LiveHelpTarget


def _event(name: str) -> None:
    """Private observation/fault seam; never retained by a completed Frame."""


def _failure(phase: RunFailurePhase) -> RunFailure:
    return RunFailure(
        phase=phase,
        kind="process_lost" if phase == "process_lost" else "execution_failed",
        safe_message="Completed result publication failed.",
        safe_location=f"analysis.frame.persist.{phase}",
        expected="complete checked output and one atomic publication",
        received="execution or publication did not complete",
        repair=AnalysisRepair(
            kind="inspect",
            action="Resume the owning Session to resolve publication resources before explicitly resubmitting.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="session.resume"),
        ),
    )


def reconcile(store: SessionStore, session: str, event: Callable[[str], None]) -> None:
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


def publish_completed(
    local: LocalFrameServices, result: CompletedFrameResult
) -> StoredFrameBacking:
    """Publish independent checked output; no evaluation or source access is possible."""
    if type(local) is not LocalFrameServices or type(result) is not CompletedFrameResult:
        reject(
            "local Frame services and one completed result",
            type(result).__name__,
            "Compute through the owning Frame before publishing.",
            "frame.persist",
        )
    # Validate/copy before admission. Publication owns this snapshot even if an
    # invalid caller has retained a private pandas buffer.
    data = CompletedFrameData(
        result.data.schema, result.data.copy_rows(), result.data.copy_attachments()
    )
    terminal_store.validate_result_records(data.schema, result.metadata)
    for facts in result.method_facts:
        validate_method_facts(facts)
    method_tables = tuple(method_payload(facts) for facts in result.method_facts)
    store, session = local.store, local.session_ref
    run_ref: str | None = None
    value: ArtifactManifest | None = None
    committing = False
    phase: RunFailurePhase = "storage_staging"
    with session_writer_guard(store.layout.lock_path(session), session_ref=session):
        local.record()
        reconcile(store, session, _event)
        try:
            run_ref = _new_run_ref()
            terminal_store.admit(store, session, run_ref, result.metadata.result_id)
            _event("publication_admitted")
            nonce = uuid4().hex
            artifact = "artifact_" + nonce
            staging, final, resources = reserve_output(
                store, run_ref=run_ref, session_ref=session, artifact_ref=artifact, nonce=nonce
            )
            primary = write_output(
                store.project_root,
                staging / "primary",
                final / "primary",
                data.schema,
                data.copy_rows(),
            )
            _event("terminal_primary_written")
            attachments: list[SavedAttachment] = []
            for index, attachment in enumerate(data.copy_attachments()):
                locator = f"attachment-{index}"
                handle = write_output(
                    store.project_root,
                    staging / locator,
                    final / locator,
                    attachment.schema,
                    attachment.rows,
                )
                attachments.append(SavedAttachment(attachment.name, attachment.schema, handle))
                _event("terminal_attachment_written")
            saved_methods: list[SavedFrameMethodFacts] = []
            for index, (facts, attachment) in enumerate(
                zip(result.method_facts, method_tables, strict=True)
            ):
                locator = f"method-{index}"
                handle = write_output(
                    store.project_root,
                    staging / locator,
                    final / locator,
                    attachment.schema,
                    attachment.rows,
                )
                method_attachment = SavedAttachment(attachment.name, attachment.schema, handle)
                saved_methods.append(saved_method_facts(facts, method_attachment))
                _event("terminal_method_written")
            value = ArtifactManifest(
                store.store_id,
                artifact,
                session,
                run_ref,
                ArtifactData(data.schema, primary, tuple(attachments)),
                result.metadata,
                tuple(saved_methods),
            )
            _fsync_directory(staging)
            final.parent.mkdir(parents=True, exist_ok=True)
            phase = "storage_finalization"
            os.rename(staging, final)
            _fsync_directory(final.parent)
            _fsync_directory(final.parent.parent)
            _event("terminal_files_published")
            phase = "publication"
            committing = True
            terminal_store.publish(store, value, resources, _event)
            return StoredFrameBacking(
                value.artifact_id, value.data, value.metadata, value.method_facts
            )
        except BaseException as failure:
            if committing:
                try:
                    with store._read() as conn:
                        original = terminal_store.run(store, conn, run_ref or "")
                        if original is None:
                            raise invalid("original publication Run is unavailable")
                        if original.lifecycle == "succeeded":
                            saved = terminal_store.manifest(
                                store, conn, original.output_artifact_ref or ""
                            )
                            if saved is None or saved != value:
                                raise invalid("original publication read-back differs")
                            # manifest validates exact metadata and record projections.
                            return StoredFrameBacking(
                                saved.artifact_id, saved.data, saved.metadata, saved.method_facts
                            )
                        if (
                            value is not None
                            and _one(
                                conn,
                                "SELECT 1 FROM dataset_artifacts WHERE artifact_ref=? OR invocation_id=?",
                                (value.artifact_id, run_ref),
                            )
                            is not None
                        ):
                            raise invalid("original Run has conflicting publication metadata")
                except Exception as error:
                    raise RecoveryPendingError(
                        expected="a confirmed original terminal publication outcome",
                        received="commit acknowledgement or read-back is unavailable",
                        repair=f"Publication may already have succeeded. Call mv.session.resume({session!r}) before explicitly resubmitting; resubmission may create duplicate saved outputs and reevaluation may read changed source data.",
                        stage="terminal_commit_unknown",
                        help_target="session.resume",
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
                raise
            if not isinstance(failure, Exception):
                raise
            raise MaterializationError(
                expected="one atomic publication of the existing completed result",
                received=f"Publication failed during {phase}: {type(failure).__name__}",
                repair=f"Call mv.session.resume({session!r}) before explicitly resubmitting. Reuse the complete memory Frame when available; rebuilding may read changed sources.",
                stage=phase,
                help_target="session.resume",
            ) from failure
