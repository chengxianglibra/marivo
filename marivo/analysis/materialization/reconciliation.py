"""Guarded cold reconciliation never resumes computation or publishes staging."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.contracts import RunFailure
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
) -> None:
    """Resolve only the guarded Session's admissions and exact resource obligations."""
    event("reconciliation")
    entries = store.recovery_snapshot(session_ref)
    # The read transaction is closed before any external proof or deletion.
    for entry in entries:
        run = entry.run
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
