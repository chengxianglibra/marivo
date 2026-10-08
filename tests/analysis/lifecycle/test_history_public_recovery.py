"""Finite public History recovery risks, without a backend or carrier matrix."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
@pytest.mark.parametrize("risk", ("views", "captured_observations"))
def test_history_public_recovery(tmp_path: Path, risk: str) -> None:
    repository = PROJECT_ROOT
    reports: list[Json] = []
    for phase in ("produce", "fixed", "cold"):
        report = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.lifecycle.history_public_recovery_worker",
                str(tmp_path),
                risk,
                phase,
                str(report),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
        if phase == "produce":
            shutil.rmtree(tmp_path / "models")
            for path in tmp_path.glob("source.duckdb*"):
                path.unlink()
    assert len({obj(report)["pid"] for report in reports}) == 3
    fixed, cold = obj(reports[1]), obj(reports[2])
    assert fixed["outputs"] == cold["outputs"]
    assert cold["kernels"] == cold["new_runs"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "public-history-" + risk + ".json").write_bytes(
            encode({"risk": risk, "reports": reports, "manifest": read(tmp_path / "state.json")})
        )
