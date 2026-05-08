from __future__ import annotations

from typing import Any, Protocol

from app.contracts.ids import SessionId, StepId
from app.contracts.session import Step


class StepStore(Protocol):
    """Port for persisting and querying analysis step records."""

    def insert_step(
        self,
        step_id: StepId,
        session_id: SessionId,
        step_type: str,
        summary: str,
        result: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
        semantic_metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def list_steps(self, session_id: SessionId) -> list[Step]: ...
