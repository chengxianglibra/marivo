"""Guarded Store 8 reconciliation never replays origins or publishes staging."""

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
    from marivo.analysis.materialization.graph_publication import _reconcile_graph

    _reconcile_graph(store, session_ref, event, run_ref=run_ref)
