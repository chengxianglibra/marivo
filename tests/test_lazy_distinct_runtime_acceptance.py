"""Three processes prove private membership continuation and cold exact binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.lazy_distinct_fixtures import assert_no_raw_keys
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, kind: str, project: Path, refs: object) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
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


@pytest.mark.parametrize("kind", ["local"])
def test_three_process_distinct_recovery_without_origin(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
) -> None:
    candidate = _manifest()
    produced = _run("produce", kind, tmp_path, {})
    continued = _run("continue", kind, tmp_path, produced["refs"])
    cold = _run("cold", kind, tmp_path, continued["refs"])
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
