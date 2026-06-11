"""Resolve a live Session object from a frame's stored session_id and project_root."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session


def resolve_frame_session(session_id: str, project_root: str) -> Session:
    """Return a Session for the given session_id, preferring the current session if it matches.

    Strategy:
    1. If current() session has the same id, reuse it (fast path, no I/O).
    2. Otherwise, construct a Session from the on-disk metadata using
       the session store.
    """
    from marivo.analysis.session._runtime import current

    current_session = current()
    if current_session is not None and current_session.id == session_id:
        return current_session

    from marivo.analysis.session._runtime import _compile_backend_factory, _session_from_row
    from marivo.analysis.session._store import SessionStore

    store = SessionStore(project_root=Path(project_root))
    row = store.get_session_by_id(session_id)
    if row is None:
        from marivo.analysis.errors import NoActiveSessionError

        raise NoActiveSessionError(
            message=f"session {session_id!r} not found in project index",
            hint="The session may have been deleted. Re-create it with mv.session.get_or_create().",
        )
    factory = _compile_backend_factory(None, None, use_datasources=True)
    return _session_from_row(store, row, factory)
