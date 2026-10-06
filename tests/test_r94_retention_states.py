"""Three-valued retention through canonical public source/offline/cold Sessions."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

from scripts.r9_qualification_requirements import Json, encode, obj, read


def _retention_recovery(
    tmp_path: Path, subject_kind: Literal["i", "s"], occurrence_kind: Literal["i", "s"]
) -> None:
    repository = Path(__file__).resolve().parents[1]
    reports: list[Json] = []
    for phase in ("produce", "fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_retention_states_worker",
                str(tmp_path),
                phase,
                str(output),
                subject_kind,
                occurrence_kind,
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
        if phase == "produce":
            shutil.rmtree(tmp_path / "models")
            for file in tmp_path.glob("source.sqlite*"):
                file.unlink()
    assert len({obj(report)["pid"] for report in reports}) == 3
    fixed, cold = obj(reports[1]), obj(reports[2])
    assert fixed["outputs"] == cold["outputs"]
    assert cold["new_runs"] == cold["kernels"] == cold["resources"] == 0
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        filename = (
            "public-retention-states.json"
            if (subject_kind, occurrence_kind) == ("i", "i")
            else "public-retention-states-" + subject_kind + "-" + occurrence_kind + ".json"
        )
        Path(destination, filename).write_bytes(encode({"reports": reports}))


@pytest.mark.runtime
def test_public_sqlite_nonempty_retention_states_fixed_and_cold(tmp_path: Path) -> None:
    _retention_recovery(tmp_path, "i", "i")


@pytest.mark.runtime
@pytest.mark.parametrize("subject_kind", ("i", "s"))
def test_public_sqlite_string_occurrence_retention_fixed_and_cold(
    tmp_path: Path, subject_kind: Literal["i", "s"]
) -> None:
    _retention_recovery(tmp_path, subject_kind, "s")
