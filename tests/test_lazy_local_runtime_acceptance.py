"""Terminal three-process acceptance with stable candidate manifests."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime


def _manifest() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    files = sorted(
        {
            *root.joinpath("marivo").rglob("*.py"),
            *root.joinpath("tests").rglob("*.py"),
            *(root / name for name in ("pyproject.toml", "Makefile", ".importlinter")),
        }
    )
    digest = hashlib.sha256()
    entries = []
    for file in files:
        relative = file.relative_to(root).as_posix()
        payload = file.read_bytes()
        digest.update(relative.encode() + b"\0" + payload + b"\0")
        entries.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})
    return {"digest": digest.hexdigest(), "files": entries}


def _run(mode: str, project: Path, session: str = "", artifact: str = "") -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_local_runtime_worker",
            mode,
            str(project),
            "--session",
            session,
            "--artifact",
            artifact,
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


def test_source_offline_continuation_and_exact_cold_binding(tmp_path: Path) -> None:
    candidate = _manifest()
    produced = _run("produce", tmp_path)
    (tmp_path / "warehouse.duckdb").rename(tmp_path / "warehouse.offline")
    continued = _run("continue", tmp_path, str(produced["session"]), str(produced["artifact"]))
    recovered = _run("continue", tmp_path, str(produced["session"]), str(produced["artifact"]))
    assert len({produced["pid"], continued["pid"], recovered["pid"]}) == 3
    assert continued["rows"] == [[[3], 100.0, 1], [[2], 30.0, 2], [[1], 10.0, 3]]
    for field in ("artifact", "rows", "columns", "show", "evidence", "after"):
        assert continued[field] == recovered[field]
    assert recovered["before"] == recovered["after"] == continued["after"]
    stats = continued["statistics"]
    assert isinstance(stats, dict)
    assert stats["primary_queries"] == stats["validation_queries"] == 0
    handoffs = stats["handoffs"]
    assert isinstance(handoffs, list) and len(handoffs) == 4
    assert all(handoffs[i][1] == handoffs[i + 1][0] for i in range(3))
    cold = recovered["statistics"]
    assert isinstance(cold, dict) and cold["local_executions"] == 0 and cold["primary_queries"] == 0
    assert continued["forbidden_attempts"] == recovered["forbidden_attempts"] == []
    after = _manifest()
    assert candidate == after
    evidence = {
        "schema": "marivo.slice4a.runtime/v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "candidate_before": candidate,
        "candidate_after": after,
        "producer": produced,
        "continuation": continued,
        "cold_recovery": recovered,
    }
    destination = tmp_path / "slice-4a-runtime.json"
    destination.write_text(json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n")
