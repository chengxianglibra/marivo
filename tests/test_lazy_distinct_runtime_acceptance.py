"""Three processes prove private membership continuation and cold exact binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marivo.analysis.materialization.targets import S3Access
from tests.lazy_distinct_fixtures import assert_no_raw_keys
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(
    mode: str, kind: str, project: Path, access: S3Access | None, refs: object
) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    if access is not None:
        environment.update(
            {
                "MARIVO_TEST_S3_ENDPOINT": access.endpoint_url,
                "MARIVO_TEST_S3_BUCKET": access.bucket,
                "MARIVO_TEST_S3_ACCESS_KEY": access.access_key_id,
                "MARIVO_TEST_S3_SECRET_KEY": access.secret_access_key,
            }
        )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_distinct_runtime_worker",
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
    assert result.returncode == 0, result.stdout + result.stderr
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ("local", "engine", "object"))
def test_three_process_distinct_recovery_without_origin(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
) -> None:
    access = None
    if kind == "object":
        selected: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(selected, S3Access)
        access = selected
    candidate = _manifest()
    produced = _run("produce", kind, tmp_path, access, {})
    continued = _run("continue", kind, tmp_path, access, produced["refs"])
    cold = _run("cold", kind, tmp_path, access, continued["refs"])
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    assert produced["origin_removed"] is True
    for key in (
        "refs",
        "rows",
        "continued_rows",
        "row_contract",
        "row_set_contract",
        "findings",
        "input_artifact_refs",
        "artifact",
        "evidence_digest",
    ):
        assert continued[key] == cold[key]
    assert cold["before"] == cold["after"] == continued["after"]
    assert_no_raw_keys((produced, continued, cold))
    directory = os.environ.get("MARIVO_SLICE5C_EVIDENCE_DIR")
    if directory:
        assert candidate == _manifest()
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"distinct-recovery-{kind}.json").write_text(
            json.dumps(
                {
                    "schema": "marivo.slice5c.runtime-acceptance/v1",
                    "emitted_at": datetime.now(timezone.utc).isoformat(),
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
