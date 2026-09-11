"""Guarded cold reconciliation never resumes computation or publishes staging."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.contracts import RunFailure
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.resources import discharge_resources
from marivo.analysis.materialization.store import SessionStore
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.materialization.targets import S3Access


def reconcile_session(
    store: SessionStore,
    session_ref: str,
    *,
    event: Callable[[str], None],
    object_bindings: tuple[S3Access, ...] = (),
    run_ref: str | None = None,
) -> None:
    """Resolve guarded Session obligations, optionally selecting one exact Run.

    The caller owns the Session writer guard. Selection never bypasses backend
    terminal/fencing proof or admits a successful publication.
    """
    event("reconciliation")
    entries = store.recovery_snapshot(session_ref)
    if run_ref is not None:
        selected = store.run(run_ref)
        if (
            selected is None
            or selected.session_ref != session_ref
            or selected.lifecycle == "succeeded"
        ):
            raise MaterializationError(
                expected="an incomplete or failed Run belonging to the selected Session",
                received="unknown or foreign Run"
                if selected is None or selected.session_ref != session_ref
                else "committed success",
                repair="Inspect this Session's Runs and select an incomplete or failed Run for recovery.",
                stage="reconciliation",
                run_ref=run_ref,
            )
        # A failed Run with no remaining obligations is an idempotent success.
    # The read transaction is closed before any external proof or deletion.
    for entry in entries:
        run = entry.run
        if run_ref is not None and run.run_ref != run_ref:
            continue
        resolved = discharge_resources(store, entry.resources, object_bindings)
        if run.lifecycle == "incomplete":
            store.fail(
                run.run_ref,
                RunFailure(
                    phase="process_lost",
                    kind="process_lost",
                    safe_message="The producing process ended before publication.",
                    safe_location="dataset.reconciliation",
                    expected="a committed output from a completed producer",
                    received="an uncommitted producer with proven terminal execution",
                    repair=AnalysisRepair(
                        kind="inspect",
                        action="Retry the logical definition after Session recovery completes.",
                        help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
                    ),
                ),
                resolved_resources=resolved,
            )
        else:
            for resource in resolved:
                store.discharge(resource)
