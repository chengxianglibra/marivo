"""Public Session resume restores History without its project or source."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT


@pytest.mark.slow
def test_public_resume_restores_history_without_source(tmp_path: Path) -> None:
    repository = PROJECT_ROOT
    reports: list[Json] = []
    for phase in ("produce", "restore"):
        report = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.lifecycle.history_public_recovery_worker",
                str(tmp_path),
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
            for path in (*tmp_path.glob("source.duckdb*"), *tmp_path.glob("*.parquet")):
                path.unlink()
    producer, cold = obj(reports[0]), obj(reports[1])
    assert producer["pid"] != cold["pid"]
    assert producer["history"] == cold["history"]
    assert producer["resources"] == cold["resources"] == 0
    assert cold["kernels"] == cold["new_runs"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "public-history-resume.json").write_bytes(
            encode(
                {
                    "recovery_path": "public_session_resume",
                    "reports": reports,
                    "manifest": read(tmp_path / "state.json"),
                }
            )
        )
