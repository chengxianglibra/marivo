"""Three fresh processes verify Entity identity authority without origin replay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, project: Path, refs: object) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_entity_candidate_runtime_worker",
            mode,
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


def test_three_process_entity_candidate_membership(tmp_path: Path) -> None:
    candidate = _manifest()
    produced = _run("produce", tmp_path, {})
    continued = _run("continue", tmp_path, produced["refs"])
    cold = _run("cold", tmp_path, continued["refs"])
    assert len({v["pid"] for v in (produced, continued, cold)}) == 3
    assert all(v["origin_removed"] and v["identity_verified"] for v in (produced, continued, cold))
    for name in ("score", "artifact", "search_evidence"):
        assert produced[name] == continued[name] == cold[name]
    assert continued["total"] == cold["total"] == 40.0
    assert continued["refs"] == cold["refs"]
    assert continued["selected_evidence"] == cold["selected_evidence"]
    assert cold["before"] == cold["after"] == continued["after"]
    original, selected = produced["search_evidence"], continued["selected_evidence"]
    assert isinstance(original, dict) and isinstance(selected, dict)
    assert original["definition"] == selected["definition"]
    assert original["evaluation"] == selected["evaluation"]
    assert original["emitted_finding_count"] == selected["emitted_finding_count"] == 0
    assert _manifest() == candidate
    evidence = os.environ.get("MARIVO_SLICE6D_EVIDENCE_DIR")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True)
        (root / "entity-candidate-membership-engine.json").write_text(
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
