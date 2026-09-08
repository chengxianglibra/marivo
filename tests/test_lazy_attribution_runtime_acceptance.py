"""Three-process acceptance for both attribution methods and every receipt family."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marivo.analysis.materialization.targets import S3Access
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(
    mode: str, kind: str, method: str, project: Path, access: S3Access | None, refs: object
) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    if access is not None:
        environment["MARIVO_TEST_S3_ENDPOINT"] = access.endpoint_url
        environment["MARIVO_TEST_S3_BUCKET"] = access.bucket
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_attribution_runtime_worker",
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
    assert result.returncode == 0, result.stdout + result.stderr
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ["local", "engine", "object"])
@pytest.mark.parametrize("method", ["additive", "component"])
def test_retained_attribution_without_origin_and_cold_binding(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str, method: str
) -> None:
    access = None
    if kind == "object":
        selected: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(selected, S3Access)
        access = selected
    candidate = _manifest()
    produced = _run("produce", kind, method, tmp_path, access, {})
    continued = _run("continue", kind, method, tmp_path, access, produced["refs"])
    cold = _run("cold", kind, method, tmp_path, access, continued["refs"])
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    for key in (
        "refs",
        "rows",
        "continued_rows",
        "row_contract",
        "row_set_contract",
        "findings",
        "input_artifact_refs",
    ):
        assert continued[key] == cold[key]
    assert cold["before"] == cold["after"] == continued["after"]
    assert candidate == _manifest()
    directory = os.environ.get("MARIVO_SLICE5B_EVIDENCE_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        record = {
            "schema": "marivo.slice5b.runtime-acceptance/v1",
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "candidate_before": candidate,
            "candidate_after": _manifest(),
            "produce": produced,
            "continue": continued,
            "cold": cold,
        }
        (path / f"attribution-{method}-{kind}.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
