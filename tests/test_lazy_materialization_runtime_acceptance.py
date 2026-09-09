"""Real process death, authoritative recovery and terminal evidence for Slice 2b."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.lazy_materialization_crash_worker import CRASH_EXIT, CRASH_POINTS

pytestmark = pytest.mark.runtime

_ROOT = Path(__file__).resolve().parents[1]
_WORKER = "tests.lazy_materialization_crash_worker"


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _records(value: object) -> tuple[dict[str, object], ...]:
    assert isinstance(value, list)
    return tuple(_object(item) for item in value)


def _source_snapshot() -> dict[str, object]:
    paths = {
        path
        for folder in ("datasets", "observation", "compiler", "materialization")
        for path in (_ROOT / "marivo" / "analysis" / folder).rglob("*.py")
    }
    paths.update(
        path
        for pattern in ("lazy_*.py", "test_lazy_*.py", "typing/lazy_*.py")
        for path in (_ROOT / "tests").glob(pattern)
    )
    paths.update(
        _ROOT / path
        for path in (
            "marivo/analysis/session/_lazy_sources.py",
            "marivo/datasource/backends.py",
            "marivo/semantic/ir.py",
            "marivo/semantic/validator.py",
            "marivo/semantic/metric_graph.py",
            "marivo/semantic/metric_graph_lowering.py",
            ".importlinter",
            "pyproject.toml",
            "tests/conftest.py",
            "tests/shared_fixtures.py",
        )
    )
    digest = hashlib.sha256()
    records: list[dict[str, object]] = []
    for path in sorted(paths):
        relative = path.relative_to(_ROOT).as_posix()
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + payload + b"\0")
        records.append(
            {"path": relative, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "base_head": head,
        "ordered_content_sha256": digest.hexdigest(),
        "files": records,
        "digest_protocol": "sorted UTF-8 path, NUL, file bytes, NUL",
        "work_summary": "Private DuckDB Observation lowering, v3 atomic publication, immutable Parquet, guarded retained reads and crash recovery.",
    }


def _run(arguments: tuple[str, ...], *, expected_exit: int) -> tuple[dict[str, object], ...]:
    environment = os.environ.copy()
    environment["MARIVO_TELEMETRY"] = "off"
    process = subprocess.run(
        [sys.executable, "-B", "-m", _WORKER, *arguments],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == expected_exit, process.stdout + process.stderr
    assert len(process.stdout.encode("utf-8")) < 1_048_576
    return tuple(_object(json.loads(line)) for line in process.stdout.splitlines() if line.strip())


def _counts(snapshot: object) -> dict[str, object]:
    return _object(_object(snapshot)["counts"])


def _assert_bundle(snapshot: object, *, committed: bool, terminal: bool) -> None:
    counts = _counts(snapshot)
    assert _object(snapshot)["user_version"] == 3
    assert counts["analysis_action_runs"] == 1
    assert counts["analysis_action_run_terminals"] == int(terminal)
    assert counts["dataset_artifacts"] == int(committed)
    assert counts["dataset_evidence"] == int(committed)
    assert counts["findings"] == 0
    assert counts["analysis_action_run_inputs"] == 0


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_actual_process_crashes_reconcile_without_replaying_the_source(
    tmp_path: Path, point: str
) -> None:
    before = _source_snapshot()
    cases: list[dict[str, object]] = []
    project = tmp_path / point
    project.mkdir()
    produced = _run(("produce", str(project), "--point", point), expected_exit=CRASH_EXIT)
    assert len(produced) == 2
    constructed, crashed = produced
    assert constructed["phase"] == "constructed"
    assert crashed["phase"] == "crash" and crashed["point"] == point
    assert _counts(constructed["store"])["analysis_action_runs"] == 0
    construction_stats = _object(constructed["statistics"])
    assert construction_stats["primary_queries"] == construction_stats["validation_queries"] == 0
    assert construction_stats["events"] == {}
    assert construction_stats["statements"] == []
    journey = _object(constructed["journey"])
    assert journey["shape"] == "metric/dimension-time@v1"
    assert journey["columns"] == ["region", "order_time", "revenue"]
    assert crashed["journey"] == journey
    is_committed = point == "after_commit"
    _assert_bundle(crashed["store"], committed=is_committed, terminal=is_committed)
    assert _counts(crashed["store"])["action_resource_journal"] == (0 if is_committed else 3)
    stats = _object(crashed["statistics"])
    assert (
        stats["primary_queries"]
        == stats["primary_stages"]
        == (0 if point == "output_reserved" else 1)
    )
    assert isinstance(stats["validation_queries"], int) and stats["validation_queries"] > 0
    if point == "output_reserved":
        assert crashed["payload_files"] == {}
        assert stats["transferred_rows"] == stats["transferred_bytes"] == 0
    else:
        assert _object(crashed["payload_files"])
        assert isinstance(stats["transferred_rows"], int) and stats["transferred_rows"] > 0
    # The producer has really exited and been reaped before the source goes offline.
    producer_pid = crashed["pid"]
    assert isinstance(producer_pid, int)
    try:
        os.kill(producer_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("The producing process is still alive")
    (project / "warehouse.duckdb").rename(project / "warehouse.offline")
    restored_records = _run(
        (
            "recover",
            str(project),
            "--session",
            _text(crashed["session_ref"]),
            "--run",
            _text(crashed["run_ref"]),
        ),
        expected_exit=0,
    )
    assert len(restored_records) == 1
    restored = restored_records[0]
    assert restored["pid"] != producer_pid
    assert restored["session_ref"] == crashed["session_ref"]
    assert restored["run_ref"] == crashed["run_ref"]
    assert restored["execution_key"] == crashed["execution_key"]
    assert restored["before"] == crashed["store"]
    _assert_bundle(restored["after"], committed=is_committed, terminal=True)
    assert _counts(restored["after"])["action_resource_journal"] == 0
    assert not any(_object(restored["forbidden_attempts"]).values())
    recovery_stats = _object(restored["statistics"])
    for counter in (
        "primary_queries",
        "validation_queries",
        "source_fences",
        "transferred_rows",
        "transferred_bytes",
    ):
        assert recovery_stats[counter] == 0
    assert recovery_stats["statements"] == []
    assert restored["repeated_reconciliation_unchanged"] is True
    if is_committed:
        assert restored["lifecycle"] == "succeeded"
        assert restored["after"] == restored["before"]
        record = _object(restored["committed_artifact"])
        assert record == crashed["committed_artifact"]
        for identity in (
            "definition_fingerprint",
            "row_contract_fingerprint",
            "row_set_contract_fingerprint",
        ):
            assert record[identity] == journey[identity]
        receipt = _object(record["primary_receipt"])
        assert receipt["realized_row_count"] == stats["transferred_rows"]
        assert _object(record["evidence"])["finding_count"] == 0
        rows = _records(restored["rows"])
        non_null = tuple(row for row in rows if row["revenue"] is not None)
        assert non_null == (
            {"region": "EU", "order_time": "2026-02-02", "revenue": 10.0},
            {"region": "EU", "order_time": "2026-02-03", "revenue": 30.0},
            {"region": "EU", "order_time": "2026-02-04", "revenue": 0.0},
            {"region": None, "order_time": "2026-02-02", "revenue": 100.0},
        )
        assert restored["payload_files_after"] == crashed["payload_files"]
    else:
        assert restored["lifecycle"] == "failed"
        assert restored["failure_kind"] == "process_lost"
        assert restored["artifact_ref"] is None
        assert restored["payload_files_after"] == {}
    cases.append(
        {
            "point": point,
            "producer_exit_code": CRASH_EXIT,
            "constructed": constructed,
            "producer_terminal_evidence": crashed,
            "fresh_recovery_terminal_evidence": restored,
        }
    )
    after = _source_snapshot()
    evidence = {
        "schema": "marivo.slice2b.runtime_acceptance/v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "candidate_before": before,
        "candidate_after": after,
        "candidate_stable": before == after,
        "backend": "declared DuckDB TableSource",
        "storage": "local immutable Parquet",
        "finding_policy": "canonical zero-Finding",
        "scenario_count": len(cases),
        "scenarios": cases,
    }
    encoded = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n"
    assert len(encoded.encode("utf-8")) < 1_048_576
    report_path = os.environ.get("MARIVO_SLICE2B_EVIDENCE_PATH")
    if report_path:
        report = Path(report_path)
        destination = report.with_name(f"{report.stem}-{point}{report.suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded)
    print(
        "Slice 2b runtime terminal evidence: "
        + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    )
    assert before == after, (
        "Candidate files changed during acceptance; rerun after the owned changes settle."
    )
