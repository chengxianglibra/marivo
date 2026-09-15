"""Three real processes prove immutable adapter continuation and exact cold reuse."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime


def _manifest() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    paths = sorted(
        {
            *root.joinpath("marivo").rglob("*.py"),
            *root.joinpath("tests").rglob("*.py"),
            *(root / name for name in ("pyproject.toml", "Makefile", ".importlinter")),
        }
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(
            path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0"
        )
    return {"file_count": len(paths), "sha256": digest.hexdigest()}


def _run(
    mode: str,
    kind: str,
    project: Path,
    *,
    session: str = "",
    artifact: str = "",
) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_adapter_runtime_worker",
            mode,
            kind,
            str(project),
            "--session",
            session,
            "--artifact",
            artifact,
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ["engine"])
def test_fresh_adapter_journey_and_cold_binding(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str
) -> None:
    before = _manifest()
    produced = _run("produce", kind, tmp_path)
    continued = _run(
        "continue",
        kind,
        tmp_path,
        session=str(produced["session"]),
        artifact=str(produced["artifact"]),
    )
    cold = _run(
        "cold",
        kind,
        tmp_path,
        session=str(produced["session"]),
        artifact=str(produced["artifact"]),
    )
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    assert continued["artifact"] == cold["artifact"]
    assert cold["before"] == cold["after"] == continued["after"]
    assert continued["after"] == {
        "analysis_action_runs": 2,
        "analysis_action_run_terminals": 2,
        "dataset_artifacts": 2,
        "dataset_evidence": 2,
        "analysis_action_run_inputs": 1,
        "action_resource_journal": 0,
    }
    assert continued["rows"] == (
        [[[1], 40.0], [[2], 100.0], [[3], 7.0], [[4], None]]
        if kind == "engine"
        else [[[3], 100.0], [[2], 30.0]]
    )
    stats = continued["statistics"]
    assert isinstance(stats, dict)
    if kind == "engine":
        # Native execution still transfers the four output rows to immutable Parquet.
        assert stats["transferred_rows"] == 4 and stats["worker_pid"] is None
        assert '"customers"' not in json.dumps(stats["statements"])
    else:
        assert stats["primary_queries"] == 0 and stats["worker_pid"] is not None
        requests = continued["object_requests"]
        assert isinstance(requests, list) and requests
        assert all(isinstance(item, dict) and item["version_pinned"] is True for item in requests)
    cold_stats = cold["statistics"]
    assert isinstance(cold_stats, dict) and cold_stats["events"] == {"reconciliation": 1}
    assert cold["object_requests"] == []
    after = _manifest()
    assert before == after
    evidence = {
        "schema": "marivo.slice4b.runtime/v1",
        "kind": kind,
        "candidate_before": before,
        "candidate_after": after,
        "producer": produced,
        "continuation": continued,
        "cold": cold,
    }
    destination = tmp_path / f"slice-4b-{kind}-runtime.json"
    destination.write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n")
    retained = os.environ.get("MARIVO_SLICE4B_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / destination.name).write_bytes(destination.read_bytes())
