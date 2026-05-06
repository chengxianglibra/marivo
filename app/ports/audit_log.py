from __future__ import annotations

from typing import Protocol

from app.contracts.values import AuditEntry


class AuditLog(Protocol):
    def record(self, entry: AuditEntry) -> None: ...
