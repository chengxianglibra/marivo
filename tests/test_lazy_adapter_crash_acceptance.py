"""Actual producer death, native adapter state and fresh-process cold recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _start(
    mode: str,
    kind: str,
    project: Path,
    point: str,
    occurrence: int = 1,
) -> subprocess.Popen[str]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_adapter_crash_worker",
            mode,
            kind,
            str(project),
            "--point",
            point,
            "--occurrence",
            str(occurrence),
        ],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _finish(process: subprocess.Popen[str], expected: int) -> str:
    try:
        stdout, stderr = process.communicate(timeout=60)
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise
    assert process.returncode == expected, stdout + stderr
    return stdout


def _recover(project: Path, kind: str) -> dict[str, object]:
    value: object = json.loads(_finish(_start("recover", kind, project, ""), 0))
    assert isinstance(value, dict)
    return value


def _evidence(
    project: Path,
    name: str,
    value: dict[str, object],
    candidate_before: dict[str, object],
) -> None:
    candidate_after = _manifest()
    assert candidate_before == candidate_after
    value.update(
        schema="marivo.slice4c.adapter_crash/v1",
        candidate_before=candidate_before,
        candidate_after=candidate_after,
    )
    destination = project / (name + ".json")
    destination.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    retained = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / destination.name).write_bytes(destination.read_bytes())


@pytest.mark.parametrize(
    ("kind", "point", "occurrence"),
    [
        ("engine", "output_reserved", 1),
        ("engine", "parquet_payload_create", 1),
        ("engine", "parquet_payload_create", 2),
        ("engine", "before_rename", 1),
        ("engine", "after_rename", 1),
        *(
            (kind, point, 1)
            for kind in ("engine",)
            for point in (
                "insert_terminal",
                "after_commit",
                "readback_unavailable",
            )
        ),
    ],
)
def test_adapter_crash_reconciles_exact_uncommitted_outputs_or_preserves_commit(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
    point: str,
    occurrence: int,
) -> None:
    candidate_before = _manifest()
    _finish(_start("produce", kind, tmp_path, point, occurrence), 73)
    producer: object = json.loads((tmp_path / "crash.json").read_text())
    assert isinstance(producer, dict)
    recovery = _recover(tmp_path, kind)
    assert recovery["pending"] is False
    if point == "after_commit":
        assert isinstance(recovery["independent"], dict)
    else:
        assert "independent" not in recovery
    recovered = recovery["recovered"]
    assert isinstance(recovered, dict)
    assert recovered["pid"] != producer["pid"]
    assert recovered["resources"] == []
    counts = recovered["counts"]
    assert isinstance(counts, dict)
    committed = point in ("after_commit", "readback_unavailable")
    assert producer["artifacts"] == recovered["artifacts"]
    assert counts == {
        "analysis_action_runs": 2,
        "analysis_action_run_terminals": 2,
        "dataset_artifacts": 2 if committed else 1,
        "dataset_evidence": 2 if committed else 1,
        "analysis_action_run_inputs": 0,
        "action_resource_journal": 0,
    }
    runs = recovered["runs"]
    assert isinstance(runs, list)
    assert sorted(item["lifecycle"] for item in runs) == (
        ["succeeded", "succeeded"] if committed else ["failed", "succeeded"]
    )
    stats = recovered["statistics"]
    assert isinstance(stats, dict)
    assert stats["events"] == {"reconciliation": 1}
    assert stats["primary_queries"] == 0 and stats["local_executions"] == 0
    _evidence(
        tmp_path,
        f"slice-4c-crash-{kind}-{point}-{occurrence}",
        {"producer": producer, "recovery": recovery},
        candidate_before,
    )
