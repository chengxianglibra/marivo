"""One shared fixed numerical goal consumes the archived native C08 receipts."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.r9_qualification_requirements import Json, encode, obj, read
from tests.test_r94_archived_domain_recovery import restore_project


@pytest.mark.runtime
def test_archived_reference_nonzero_subset_fixed_and_cold(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    directory = (
        repository
        / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-reference-subset-bindings-01"
    )
    archive_sha = restore_project(directory, "reference-projects.json", "duckdb", tmp_path)
    reports: list[Json] = []
    for phase in ("fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_reference_subset_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    fixed, cold = [obj(report) for report in reports]
    assert len({os.getpid(), fixed["pid"], cold["pid"]}) == 3
    assert fixed["outputs"] == cold["outputs"] and fixed["parts"] == cold["parts"]
    assert cold["new_runs"] == cold["kernels"] == fixed["resources"] == cold["resources"] == 0
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "reference-nonzero-subset.json").write_bytes(
            encode({"archive_sha256": archive_sha, "parent_pid": os.getpid(), "reports": reports})
        )
