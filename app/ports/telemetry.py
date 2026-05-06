from __future__ import annotations

from typing import Protocol

from app.contracts.values import TelemetryEvent


class Telemetry(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...
