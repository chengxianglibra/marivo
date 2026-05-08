from __future__ import annotations

from typing import Any, Protocol

from app.contracts.ids import SessionId, StepId


class StepStore(Protocol):
    """Port for persisting analysis step records."""

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
