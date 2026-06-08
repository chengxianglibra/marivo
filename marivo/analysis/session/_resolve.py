"""Resolve a live Session object from a frame's stored session_id and project_root."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session


def resolve_frame_session(session_id: str, project_root: str) -> Session:
    """Return a Session for the given session_id, preferring the active session if it matches.

    Strategy:
    1. If active() session has the same id, reuse it (fast path, no I/O).
    2. Otherwise, construct a Session from the on-disk metadata using
       the project session index.
    """
    from marivo.analysis.errors import NoActiveSessionError
    from marivo.analysis.session.attach import _lookup_session_by_id, active

    try:
        active_session = active()
        if active_session.id == session_id:
            return active_session
    except NoActiveSessionError:
        pass

    return _lookup_session_by_id(Path(project_root), session_id)
