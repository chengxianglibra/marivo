"""Candidate-bound evidence for actual private Attribution runtime journeys."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from tests.test_lazy_adapter_runtime_acceptance import _manifest


def record(
    name: str,
    before: dict[str, object],
    payload: dict[str, object],
    *,
    kind: Literal["operand", "source"] = "operand",
) -> None:
    directory = os.environ.get("MARIVO_SLICE5B_EVIDENCE_DIR")
    if directory is None:
        return
    after = _manifest()
    assert before == after
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.json").write_text(
        json.dumps(
            {
                "schema": f"marivo.slice5b.{kind}-runtime/v1",
                "emitted_at": datetime.now(timezone.utc).isoformat(),
                "candidate_before": before,
                "candidate_after": after,
                **payload,
            },
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
