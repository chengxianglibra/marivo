from __future__ import annotations

import json
import sys
from pathlib import Path

from marivo.contracts.values import AuditEntry


class FileAuditLog:
    """Append-only JSONL audit log for local mode."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: AuditEntry) -> None:
        try:
            data = entry.model_dump() if isinstance(entry, AuditEntry) else entry
            line = json.dumps(data, default=str, sort_keys=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            print(f"[audit-log-fallback] {e}", file=sys.stderr)
