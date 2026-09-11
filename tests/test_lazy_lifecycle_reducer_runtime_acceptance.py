"""Three fresh processes bind Lifecycle continuations to one executable candidate."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def run(mode: str, project: Path, sink: str, refs: object = None) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_lifecycle_reducer_worker",
            mode,
            str(project),
            sink,
            "--refs",
            json.dumps({} if refs is None else refs),
        ],
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("sink", ["local", "engine"])
def test_three_process_recovered_history_continuation(tmp_path: Path, sink: str) -> None:
    before = _manifest()
    producer = run("produce", tmp_path, sink)
    continuation = run("continue", tmp_path, sink, producer["refs"])
    cold = run("cold", tmp_path, sink, continuation["refs"])
    assert len({producer["pid"], continuation["pid"], cold["pid"]}) == 3
    assert producer["after"] == continuation["before"]
    assert continuation["after"] == cold["before"] == cold["after"]
    assert continuation["artifacts"] == cold["artifacts"]
    assert continuation["terminal_sha256"] == cold["terminal_sha256"]
    after = _manifest()
    assert before == after
    output = os.environ.get("MARIVO_SLICE7E_EVIDENCE_DIR")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"slice-7e-{sink}.json").write_text(
            json.dumps(
                {
                    "candidate_before": before,
                    "candidate_after": after,
                    "producer": producer,
                    "continuation": continuation,
                    "cold": cold,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
