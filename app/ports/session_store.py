from __future__ import annotations

from typing import Any, Protocol

from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent, SessionState


class SessionStore(Protocol):
    def append_event(self, session_id: SessionId, event: SessionEvent) -> None: ...

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        """Return all events for `session_id` ordered by sequence.

        Raises NotFoundError(SESSION_NOT_FOUND) when the session does not exist.
        Does NOT return an empty list for a missing session.
        """
        ...

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        """Return all sessions owned by `owner`, projected to current SessionState.

        Implementations are free to fold events on demand or maintain a
        projection index. Implementations MUST reuse
        `core.session.rebuild.rebuild_session_state` so projection logic does
        not diverge.

        Owner invariant: a session's owner is the actor of its `session_created`
        event. Implementations index `actor` for `session_created` rows.
        """
        ...

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status.

        Raises KeyError when the proposition is not found in the session.
        """
        ...

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        """Return a paginated list of sessions (server-mode only).

        Accepts keyword arguments for filtering and pagination:
        status, session_id, limit, page_token.
        Returns a dict with session list and pagination metadata.
        """
        ...
