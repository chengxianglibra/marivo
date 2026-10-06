"""Direct native statistical result-card continuations in independent offline processes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.r9_qualification_requirements import Json, encode, obj, read
from tests.test_r94_archived_domain_recovery import restore_project


@pytest.mark.runtime
def test_archived_statistical_direct_k_portable_cold_exact_hits(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    directory = repository / (
        "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-statistical-direct-k-bindings-01"
    )
    archive_sha = restore_project(
        directory, "clickhouse-statistical-k-project.json", "clickhouse", tmp_path
    )
    manifest = read(tmp_path / "r94-statistical.json")
    output = tmp_path / "portable-direct-k.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.r94_statistical_k_worker",
            str(tmp_path),
            "cold",
            str(output),
        ],
        cwd=repository,
        env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
        capture_output=True,
        text=True,
        timeout=1200,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = read(output)
    assert report["pid"] != os.getpid()
    assert report["outputs"] == manifest["statistical_K"]
    assert report["views"] == manifest["statistical_views"]
    assert len(obj(report["outputs"])) == 162 and len(obj(report["views"])) == 54
    assert report["new_runs"] == report["kernels"] == report["resources"] == 0
    assert report["source_semantic_duckdb_statistical_kernels_forbidden"] is True
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "statistical-direct-k-portable-cold.json").write_bytes(
            encode({"archive_sha256": archive_sha, "report": report})
        )


@pytest.mark.runtime
def test_archived_native_statistical_direct_k_fixed_and_cold(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    directory = (
        repository
        / "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-statistical-bindings-01"
    )
    archive_sha = restore_project(
        directory, "clickhouse-statistical-project.json", "clickhouse", tmp_path
    )
    reports: list[Json] = []
    for phase in ("fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_statistical_k_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=1200,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        report = read(output)
        assert len(obj(report["outputs"])) == 162 and len(obj(report["views"])) == 54
        reports.append(report)
    fixed, cold = obj(reports[0]), obj(reports[1])
    assert len({fixed["pid"], cold["pid"], os.getpid()}) == 3
    assert fixed["outputs"] == cold["outputs"] and fixed["views"] == cold["views"]
    assert cold["new_runs"] == cold["kernels"] == fixed["resources"] == cold["resources"] == 0
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "statistical-direct-k.json").write_bytes(
            encode({"archive_sha256": archive_sha, "reports": reports})
        )
