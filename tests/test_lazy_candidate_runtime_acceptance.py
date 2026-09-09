"""Three-process recovery proves original authority and tuple-valued Candidate reasons."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, objective: str, kind: str, project: Path, refs: object) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_candidate_runtime_worker",
            mode,
            objective,
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


@pytest.mark.parametrize(
    "objective,kind",
    [("point_anomalies", "local"), ("interesting_windows", "engine"), ("period_shifts", "local")],
)
def test_three_process_candidate(tmp_path: Path, objective: str, kind: str) -> None:
    candidate = _manifest()
    produced = _run("produce", objective, kind, tmp_path, {})
    continued = _run("continue", objective, kind, tmp_path, produced["refs"])
    cold = _run("cold", objective, kind, tmp_path, continued["refs"])
    assert len({v["pid"] for v in (produced, continued, cold)}) == 3
    assert all(v["origin_removed"] for v in (produced, continued, cold))
    for name in ("rows", "artifact", "findings", "search_evidence"):
        assert produced[name] == continued[name] == cold[name]
    assert continued["refs"] == cold["refs"]
    assert continued["selected_rows"] == cold["selected_rows"]
    assert continued["selected_evidence"] == cold["selected_evidence"]
    assert cold["before"] == cold["after"] == continued["after"]
    original = produced["search_evidence"]
    selected = continued["selected_evidence"]
    assert isinstance(original, dict) and isinstance(selected, dict)
    assert original["definition"] == selected["definition"]
    assert original["evaluation"] == selected["evaluation"]
    assert selected["emitted_finding_count"] == original["emitted_finding_count"] == 0
    assert _manifest() == candidate
    evidence = os.environ.get("MARIVO_SLICE6C_EVIDENCE_DIR")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"candidate-{objective}-{kind}.json").write_text(
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
