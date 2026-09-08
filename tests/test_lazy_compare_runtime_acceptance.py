"""Three real interpreters bind Delta behavior and recovery to one candidate."""

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
    mode: str, kind: str, project: Path, *, access: S3Access | None, refs: object
) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    if access is not None:
        environment["MARIVO_TEST_S3_ENDPOINT"] = access.endpoint_url
        environment["MARIVO_TEST_S3_BUCKET"] = access.bucket
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_compare_runtime_worker",
            mode,
            kind,
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
    result: object = json.loads(process.stdout)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("kind", ["local", "engine", "object"])
def test_fresh_delta_publication_continuation_and_cold_binding(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str
) -> None:
    access = None
    if kind == "object":
        selected: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(selected, S3Access)
        access = selected
    candidate = _manifest()
    produced = _run("produce", kind, tmp_path, access=access, refs={})
    continued = _run("continue", kind, tmp_path, access=access, refs=produced["refs"])
    cold = _run("cold", kind, tmp_path, access=access, refs=continued["refs"])
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    for field in (
        "rows",
        "columns",
        "row_contract",
        "row_set_contract",
        "findings",
        "input_artifact_refs",
    ):
        assert produced[field] == continued[field] == cold[field]
    assert produced["findings"]
    assert continued["refs"] == cold["refs"]
    assert cold["before"] == cold["after"] == continued["after"]
    assert candidate == _manifest()
    retained = os.environ.get("MARIVO_SLICE5A_EVIDENCE_DIR")
    if retained:
        directory = Path(retained)
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "schema": "marivo.slice5a.runtime-acceptance/v1",
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "candidate_before": candidate,
            "candidate_after": _manifest(),
            "produce": produced,
            "continue": continued,
            "cold": cold,
        }
        (directory / f"delta-{kind}.json").write_text(
            json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
