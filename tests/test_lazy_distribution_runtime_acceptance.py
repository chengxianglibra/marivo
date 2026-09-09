"""Three-process exact and approximate distribution acceptance on admitted sinks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.semantic._quantile import QuantileMethod
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(
    mode: str,
    kind: str,
    method: QuantileMethod,
    project: Path,
    refs: object,
) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_distribution_runtime_worker",
            mode,
            kind,
            method,
            str(project),
            "--refs",
            json.dumps(refs),
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ["local"])
@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_three_process_distribution_recovery(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str, method: QuantileMethod
) -> None:
    manifest = _manifest()
    produced = _run("produce", kind, method, tmp_path, {})
    continued = _run("continue", kind, method, tmp_path, produced["refs"])
    cold = _run("cold", kind, method, tmp_path, continued["refs"])
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    assert produced["origin_removed"] is True
    for key in (
        "refs",
        "rows",
        "selected_rows",
        "row_contract",
        "row_set_contract",
        "findings",
        "artifact",
        "evidence_digest",
    ):
        assert continued[key] == cold[key]
    assert cold["before"] == cold["after"] == continued["after"]
    directory = os.environ.get("MARIVO_SLICE5D_EVIDENCE_DIR")
    if directory:
        assert manifest == _manifest()
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"distribution-{kind}-{method.split('@')[0]}.json").write_text(
            json.dumps(
                {
                    "candidate_before": manifest,
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
