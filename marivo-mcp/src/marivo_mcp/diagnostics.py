from __future__ import annotations

import json
import sys
from typing import Any


def emit_diagnostic(event: str, **fields: Any) -> None:
    """Emit machine-readable diagnostics to stderr only."""
    payload = {"event": event, **fields}
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
