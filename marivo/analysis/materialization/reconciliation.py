"""Guarded cold reconciliation never resumes computation or publishes staging."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from marivo.analysis.materialization.contracts import RunFailure
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.ownership import owns_resource
from marivo.analysis.materialization.resources import discharge_resources
from marivo.analysis.materialization.store import SessionStore

if TYPE_CHECKING:
    from marivo.analysis.materialization.targets import S3Access


def reconcile_session(
    store: SessionStore,
    session_ref: str,
    *,
    event: Callable[[str], None],
    object_bindings: tuple[S3Access, ...] = (),
) -> None:
    """Resolve only the guarded Session's admissions and exact resource obligations."""
    event("reconciliation")
    incomplete = store.incomplete(session_ref)
    if len(incomplete) > 1:
        raise IntegrityError(
            expected="at most one incomplete producer in a serialized Session",
            received="multiple incomplete producers",
            repair="Inspect the selected generation integrity before admitting work.",
            stage="reconciliation",
        )
    resources = store.resources(session_ref)
    for run in incomplete:
        if store.lookup(session_ref, run.execution_key_digest) is not None:
            raise IntegrityError(
                expected="an incomplete producer without a committed output",
                received="contradictory selected publication metadata",
                repair="Inspect the selected generation integrity; do not reconstruct publication.",
                stage="reconciliation",
                run_ref=run.run_ref,
            )
        owned = tuple(item for item in resources if item.run_ref == run.run_ref)
        resolved = discharge_resources(store, owned, object_bindings)
        store.fail(
            run.run_ref,
            RunFailure(
                phase="process_lost",
                kind="process_lost",
                safe_message="The producing process ended before publication.",
                safe_location="dataset.reconciliation",
                expected="a committed output from a completed producer",
                received="an uncommitted producer with proven terminal execution",
                repair="Retry the logical definition after Session recovery completes.",
            ),
            resolved_resources=resolved,
        )
    remaining = store.resources(session_ref)
    run_refs = tuple(dict.fromkeys(item.run_ref for item in remaining))
    for run_ref in run_refs:
        terminal_run = store.run(run_ref)
        if terminal_run is None or terminal_run.lifecycle == "incomplete":
            continue
        owned = tuple(item for item in remaining if item.run_ref == run_ref)
        if terminal_run.output_artifact_ref is not None:
            output = store.artifact(terminal_run.output_artifact_ref)
            if output is None:
                raise IntegrityError(
                    expected="the selected succeeded producer's complete output",
                    received="missing committed output",
                    repair="Inspect the selected generation integrity.",
                    stage="reconciliation",
                    run_ref=run_ref,
                )
            receipts = (
                output.descriptor.storage_receipt,
                *(part.storage_receipt for part in output.descriptor.retained_parts),
            )
            if any(owns_resource(receipt, item) for item in owned for receipt in receipts):
                raise IntegrityError(
                    expected="output ownership transferred in the publication transaction",
                    received="a committed output still reserved for cleanup",
                    repair="Inspect the selected publication metadata; preserve its output.",
                    stage="reconciliation",
                    run_ref=run_ref,
                )
        for resource in discharge_resources(store, owned, object_bindings):
            store.discharge(resource)
