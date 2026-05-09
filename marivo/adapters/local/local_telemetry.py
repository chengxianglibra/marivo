from __future__ import annotations

import json
from pathlib import Path

from marivo.contracts.values import TelemetryEvent


class LocalTelemetry:
    """Local telemetry: no-op by default, JSONL file when sink='file'."""

    def __init__(self, sink: str = "none", log_path: Path | None = None) -> None:
        self._sink = sink
        self._path = log_path

    def emit(self, event: TelemetryEvent) -> None:
        if self._sink != "file" or self._path is None:
            return
        try:
            data = event.model_dump() if isinstance(event, TelemetryEvent) else event
            line = json.dumps(data, default=str, sort_keys=True)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
