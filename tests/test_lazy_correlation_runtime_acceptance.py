"""Terminal evidence for Association source-closed execution and cold exact reuse."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.analysis.operators.association_contracts import CorrelationMethod
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


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


@pytest.mark.parametrize(
    "method,kind",
    [
        ("pearson", "engine"),
        ("spearman", "engine"),
        ("pearson", "local"),
        ("spearman", "local"),
        ("kendall", "local"),
        ("pearson", "object"),
        ("spearman", "object"),
        ("kendall", "object"),
    ],
)
def test_three_process_correlation(
    tmp_path: Path, request: pytest.FixtureRequest, method: CorrelationMethod, kind: str
) -> None:
    env = {**os.environ, "MARIVO_TELEMETRY": "off"}
    if kind == "object":
        from marivo.analysis.materialization.targets import S3Access

        access = request.getfixturevalue("object_connection_access")
        assert isinstance(access, S3Access)
        env.update(MARIVO_TEST_S3_ENDPOINT=access.endpoint_url, MARIVO_TEST_S3_BUCKET=access.bucket)
    candidate = _manifest()
    produced = _run("produce", method, kind, tmp_path, {}, env)
    continued = _run("continue", method, kind, tmp_path, produced["refs"], env)
    cold = _run("cold", method, kind, tmp_path, continued["refs"], env)
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
