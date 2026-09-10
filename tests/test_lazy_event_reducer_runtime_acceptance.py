"""A fixed executable candidate spans Event reducer production and cold recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, project: Path, refs: object = None) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_event_reducer_runtime_worker",
            mode,
            str(project),
            "--refs",
            json.dumps({} if refs is None else refs),
        ],
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


def test_event_reducers_and_metric_loop_recover_in_three_processes(tmp_path: Path) -> None:
    before = _manifest()
    producer = _run("produce", tmp_path)
    continuation = _run("continue", tmp_path, producer["refs"])
    cold = _run("cold", tmp_path, continuation["refs"])
    assert len({producer["pid"], continuation["pid"], cold["pid"]}) == 3
    assert producer["after"] == continuation["before"]
    assert continuation["after"] == cold["before"] == cold["after"]
    assert continuation["artifacts"] == cold["artifacts"]
    assert continuation["terminal_sha256"] == cold["terminal_sha256"]
    after = _manifest()
    assert before == after
    evidence = {
        "schema": "marivo.slice7b.runtime/v1",
        "candidate_before": before,
        "candidate_after": after,
        "producer": producer,
        "continuation": continuation,
        "cold": cold,
    }
    retained = os.environ.get("MARIVO_SLICE7B_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / "slice-7b-engine-runtime.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
