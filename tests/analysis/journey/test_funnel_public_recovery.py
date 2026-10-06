"""Nonempty Funnel Findings through canonical public Session recovery."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("table", "parquet"))
def test_public_funnel_nonempty_findings_recovery(tmp_path: Path, form: str) -> None:
    repository = PROJECT_ROOT
    environment = dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off")
    reports: list[Json] = []
    for phase in ("produce", "fixed", "cold"):
        report = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.journey.funnel_public_recovery_worker",
                str(tmp_path),
                phase,
                str(report),
                form,
            ],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
        if phase == "produce":
            shutil.rmtree(tmp_path / "models")
            for path in tmp_path.glob("source.duckdb*"):
                path.unlink()
            for path in tmp_path.glob("*.parquet"):
                path.unlink()
    assert len({obj(report)["pid"] for report in reports}) == 3
    fixed, cold = obj(reports[1]), obj(reports[2])
    assert fixed["outputs"] == cold["outputs"]
    assert cold["kernels"] == cold["new_runs"] == 0
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "public-funnel-" + form + ".json").write_bytes(
            encode(
                {
                    "form": form,
                    "reports": reports,
                    "manifest": json.loads((tmp_path / "r94-funnel.json").read_text()),
                }
            )
        )
