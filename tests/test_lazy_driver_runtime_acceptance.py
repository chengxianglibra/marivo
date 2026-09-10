"""Three-process driver recovery proves original scope and exact binding authority."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, kind: str, project: Path, refs: object) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_driver_runtime_worker",
            mode,
            kind,
            str(project),
            "--refs",
            json.dumps(refs),
        ],
        text=True,
        capture_output=True,
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    result: object = json.loads(process.stdout)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("kind", ("local", "engine"))
def test_three_process_driver_candidate(tmp_path: Path, kind: str) -> None:
    candidate = _manifest()
    produced = _run("produce", kind, tmp_path, {})
    continued = _run("continue", kind, tmp_path, produced["refs"])
    cold = _run("cold", kind, tmp_path, continued["refs"])
    assert len({result["pid"] for result in (produced, continued, cold)}) == 3
    assert all(result["origin_removed"] for result in (produced, continued, cold))
    for name in ("rows", "artifact", "findings", "search_evidence"):
        assert produced[name] == continued[name] == cold[name]
    assert continued["refs"] == cold["refs"]
    assert continued["selected_rows"] == cold["selected_rows"]
    assert continued["selected_evidence"] == cold["selected_evidence"]
    assert cold["before"] == cold["after"] == continued["after"]
    original, selected = produced["search_evidence"], continued["selected_evidence"]
    assert isinstance(original, dict) and isinstance(selected, dict)
    assert original["definition"] == selected["definition"]
    assert original["evaluation"] == selected["evaluation"]
    assert selected["emitted_finding_count"] == original["emitted_finding_count"] == 0
    assert _manifest() == candidate
    evidence = os.environ.get("MARIVO_SLICE6E_EVIDENCE_DIR")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"driver-{kind}.json").write_text(
            json.dumps(
                {
                    "candidate_before": candidate,
                    "candidate_after": _manifest(),
                    "produce": produced,
                    "continue": continued,
                    "cold": cold,
                },
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
