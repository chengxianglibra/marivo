from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.storage.metadata import MetadataStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionRepository:
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
        if row is None:
            return None
        return dict(row)
