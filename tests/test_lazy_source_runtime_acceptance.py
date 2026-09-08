"""Terminal two-process acceptance and candidate evidence for the Slice 3a source path."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime

_ROOT = Path(__file__).resolve().parents[1]


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _manifest() -> dict[str, object]:
    roots = (_ROOT / "marivo/analysis", _ROOT / "marivo/semantic")
    paths = {path for root in roots for path in root.rglob("*.py")}
    paths.update(
        path
        for pattern in ("lazy_*.py", "test_lazy_*.py")
        for path in (_ROOT / "tests").glob(pattern)
    )
    paths.update((_ROOT / "tests/typing").glob("lazy_*.py"))
    paths.update(
        _ROOT / name
        for name in (
            "pyproject.toml",
            "Makefile",
            ".importlinter",
            "tests/conftest.py",
            "tests/shared_fixtures.py",
        )
    )
    digest = hashlib.sha256()
    files: list[dict[str, object]] = []
    for path in sorted(paths):
        relative = path.relative_to(_ROOT).as_posix()
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + payload + b"\0")
        files.append(
            {"path": relative, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()
    return {
        "base_head": head,
        "ordered_content_sha256": digest.hexdigest(),
        "files": files,
        "digest_protocol": "sorted UTF-8 path, NUL, file bytes, NUL",
    }


def _run(arguments: tuple[str, ...]) -> dict[str, object]:
    environment = os.environ.copy()
    environment["MARIVO_TELEMETRY"] = "off"
    process = subprocess.run(
        [sys.executable, "-B", "-m", "tests.lazy_source_runtime_worker", *arguments],
        cwd=_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert len(process.stdout.encode("utf-8")) < 1_048_576
    return _object(json.loads(process.stdout))


def test_source_algebra_and_sampling_survive_source_free_cold_reads(tmp_path: Path) -> None:
    before = _manifest()
    produced = _run(("produce", str(tmp_path)))
    stats = _object(produced["statistics"])
    assert stats["sampling_fences"] == stats["primary_queries"] == 1
    assert stats["transferred_rows"] == 1
    assert isinstance(stats["transferred_bytes"], int) and stats["transferred_bytes"] > 0
    assert stats["primary_stages"] == 1
    counts = _object(_object(produced["after"])["counts"])
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 1
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 1
    assert counts["findings"] == counts["action_resource_journal"] == 0
    (tmp_path / "warehouse.duckdb").rename(tmp_path / "warehouse.offline")
    recovered = _run(
        (
            "recover",
            str(tmp_path),
            "--session",
            _text(produced["session_ref"]),
            "--artifact",
            _text(produced["artifact_ref"]),
        )
    )
    assert recovered["pid"] != produced["pid"]
    assert recovered["before"] == recovered["after"] == produced["after"]
    for field in ("rows", "artifact", "descriptor", "sampling_execution", "show"):
        assert recovered[field] == produced[field]
    assert not any(_object(recovered["forbidden_attempts"]).values())
    cold_stats = _object(recovered["statistics"])
    assert (
        cold_stats["sampling_fences"]
        == cold_stats["primary_queries"]
        == cold_stats["validation_queries"]
        == 0
    )
    assert cold_stats["events"] == {} and cold_stats["statements"] == []
    after = _manifest()
    evidence = {
        "schema": "marivo.slice3a.runtime_acceptance/v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "candidate_before": before,
        "candidate_after": after,
        "candidate_stable": before == after,
        "backend": "declared DuckDB TableSource",
        "storage": "immutable Parquet primary and bounded sampling state",
        "producer": produced,
        "cold_reader": recovered,
    }
    encoded = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n"
    assert len(encoded.encode("utf-8")) < 1_048_576
    report_path = os.environ.get("MARIVO_SLICE3A_EVIDENCE")
    if report_path:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded)
    print(
        "Slice 3a runtime terminal evidence: "
        + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    )
    assert before == after, (
        "Candidate files changed during acceptance; rerun after the owned changes settle."
    )
