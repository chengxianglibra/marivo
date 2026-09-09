"""Journey J: all models retain certified nominal prediction authority across processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, model: str, kind: str, project: Path, refs: object) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_forecast_runtime_worker",
            mode,
            model,
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
    "model,kind", [("naive", "local"), ("drift", "engine"), ("seasonal_naive", "local")]
)
def test_three_process_forecast(tmp_path: Path, model: str, kind: str) -> None:
    candidate = _manifest()
    produced = _run("produce", model, kind, tmp_path, {})
    continued = _run("continue", model, kind, tmp_path, produced["refs"])
    cold = _run("cold", model, kind, tmp_path, continued["refs"])
    assert len({v["pid"] for v in (produced, continued, cold)}) == 3
    assert all(v["origin_removed"] for v in (produced, continued, cold))
    for name in ("rows", "artifact", "findings"):
        assert produced[name] == continued[name] == cold[name]
    assert continued["refs"] == cold["refs"]
    assert continued["selected_rows"] == cold["selected_rows"]
    assert cold["before"] == cold["after"] == continued["after"]
    assert _manifest() == candidate
    evidence = os.environ.get("MARIVO_SLICE6B_EVIDENCE_DIR")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"forecast-{model}-{kind}.json").write_text(
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
