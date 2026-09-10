"""Three interpreters bind private Event acceptance to one unchanged candidate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, kind: str, project: Path, refs: object = None) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_event_runtime_worker",
            mode,
            kind,
            str(project),
            "--refs",
            json.dumps({} if refs is None else refs),
        ],
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_event_membership_journey_and_cold_binding_in_three_processes(
    tmp_path: Path, kind: str
) -> None:
    before = _manifest()
    producer = _run("produce", kind, tmp_path)
    continuation = _run("continue", kind, tmp_path, producer["refs"])
    cold = _run("cold", kind, tmp_path, continuation["refs"])
    assert len({producer["pid"], continuation["pid"], cold["pid"]}) == 3
    assert producer["after"] == continuation["before"]
    assert continuation["after"] == cold["before"] == cold["after"]
    assert continuation["artifact"] == cold["artifact"]
    assert continuation["event_evidence"] == cold["event_evidence"]
    assert continuation["terminal_sha256"] == cold["terminal_sha256"]
    after = _manifest()
    assert before == after
    evidence = {
        "schema": "marivo.slice7a.runtime/v1",
        "kind": kind,
        "candidate_before": before,
        "candidate_after": after,
        "producer": producer,
        "continuation": continuation,
        "cold": cold,
    }
    retained = os.environ.get("MARIVO_SLICE7A_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"slice-7a-{kind}-runtime.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
