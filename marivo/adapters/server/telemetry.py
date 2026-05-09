from __future__ import annotations

from marivo.contracts.values import TelemetryEvent


class LocalTelemetryAdapter:
    """No-op telemetry adapter; does nothing."""

    def emit(self, event: TelemetryEvent) -> None:
        pass
