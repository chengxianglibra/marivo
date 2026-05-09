from __future__ import annotations

from typing import Protocol

from marivo.contracts.values import TelemetryEvent


class Telemetry(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...
