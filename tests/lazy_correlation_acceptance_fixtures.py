"""Terminal evidence for Association source-closed execution and cold exact reuse."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from marivo.analysis.operators.association_contracts import CorrelationMethod
from tests.test_lazy_adapter_runtime_acceptance import _manifest


def _run(
    mode: str,
    method: CorrelationMethod,
    kind: str,
    project: Path,
    refs: object,
    environment: dict[str, str],
) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_correlation_runtime_worker",
            mode,
            method,
            kind,
            str(project),
            "--refs",
            json.dumps(refs),
        ],
        text=True,
        capture_output=True,
        env=environment,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    body: object = json.loads(result.stdout)
    assert isinstance(body, dict)
    return body


def correlation_journey(
    project: Path, method: CorrelationMethod, kind: str, environment: dict[str, str]
) -> None:
    candidate = _manifest()
    produced = _run("produce", method, kind, project, {}, environment)
    continued = _run("continue", method, kind, project, produced["refs"], environment)
    cold = _run("cold", method, kind, project, continued["refs"], environment)
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    for key in ("refs", "rows", "selected_rows", "artifact", "findings"):
        assert continued[key] == cold[key]
    assert cold["before"] == cold["after"] == continued["after"]
    assert _manifest() == candidate
    evidence = os.environ.get("MARIVO_SLICE6A_EVIDENCE_DIR")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"correlation-{method}-{kind}.json").write_text(
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
