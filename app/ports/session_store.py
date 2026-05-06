from __future__ import annotations

from typing import Protocol

from app.contracts.ids import SessionId
from app.contracts.session import SessionEvent


class SessionStore(Protocol):
    def append_event(self, session_id: SessionId, event: SessionEvent) -> None: ...
    def load_events(self, session_id: SessionId) -> list[SessionEvent]: ...
