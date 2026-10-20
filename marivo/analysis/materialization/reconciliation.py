"""Guarded Store 15 reconciliation never replays origins or publishes staging."""

from collections.abc import Callable

from marivo.analysis.materialization.store import SessionStore


def reconcile_session(
    store: SessionStore,
    session_ref: str,
    *,
    event: Callable[[str], None],
    run_ref: str | None = None,
) -> None:
    """Resolve the selected Session obligations under its owning writer guard."""
    from marivo.analysis.materialization.terminal_publication import reconcile

    reconcile(store, session_ref, event, run_ref=run_ref)
