"""Adversarial acceptance through the public Session and Dataset surface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_public_read_worker import definition, seed_public_project

pytestmark = pytest.mark.runtime
ROOT = Path(__file__).resolve().parents[1]


def test_public_findings_cross_session_and_scoped_cold_reads(
    authoring_evidence_project: Path,
) -> None:
    project = authoring_evidence_project
    reports: list[dict[str, object]] = []
    for phase in ("produce", "continue", "cold"):
        output = project / f"public-{phase}.json"
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "tests.lazy_public_read_worker",
                phase,
                str(project),
                str(output),
            ],
            cwd=ROOT,
            env={**os.environ, "MARIVO_TELEMETRY": "off", "MARIVO_PERSIST_CREDENTIALS": "0"},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        reports.append(json.loads(output.read_text()))
    assert len({report["pid"] for report in reports}) == 3
    for key in ("owner", "artifact", "evidence", "findings", "finding_owners", "deltas"):
        assert reports[0][key] == reports[1][key] == reports[2][key]
    assert reports[0]["finding_owners"] == [reports[0]["owner"]] * 2
    for key in ("consumer", "downstream", "run_ids"):
        assert reports[1][key] == reports[2][key]
    assert reports[2]["source_fences"] == [0, 0]
    destination = os.environ.get("MARIVO_SLICE9C_EVIDENCE_DIR")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "public-read-journey.json").write_text(json.dumps(reports, indent=2) + "\n")
        shutil.copytree(project, directory / "public-project")


@pytest.mark.parametrize(
    "point", ["profile_resolution", "backend_compile", "insert_findings", "before_commit"]
)
def test_public_failure_has_one_run_and_no_partial_findings(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    seed_public_project(authoring_evidence_project)
    owner = mv.session.get_or_create("public-failure", report_timezone="UTC")
    logical = definition(owner)
    observed: list[str] = []

    def fault(event: str) -> None:
        if event == point:
            runs = owner.runs().items
            assert len(runs) == 1 and isinstance(runs[0], mv.IncompleteRun)
            observed.append(runs[0].run_id)
            raise RuntimeError("private-public-failure-canary")

    monkeypatch.setattr(owner._runtime, "_hook", fault)
    with pytest.raises(MaterializationError) as error:
        logical.execute()
    assert "private-public-failure-canary" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None
    runs = owner.runs().items
    assert len(runs) == 1 and isinstance(runs[0], mv.FailedRun)
    assert observed == [runs[0].run_id]
    assert owner.get_run(runs[0].run_id) == runs[0]
    assert owner.graph().artifacts == ()
    state = snapshot(owner._runtime)
    counts = state["counts"]
    assert isinstance(counts, dict)
    assert all(
        counts[table] == 0
        for table in (
            "dataset_artifacts",
            "dataset_evidence",
            "findings",
            "action_resource_journal",
        )
    )
