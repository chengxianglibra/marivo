"""Optional test receipts for native source-read and cancellation witnesses."""

import json
import os
from pathlib import Path


def receipt(name: str, payload: dict[str, object]) -> None:
    directory = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
    if directory:
        Path(directory, name + ".json").write_text(json.dumps(payload, sort_keys=True))
