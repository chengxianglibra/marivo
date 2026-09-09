"""Composed real-process Slice 4a-d acceptance for every retained storage kind."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(mode: str, kind: str, project: Path) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    process = subprocess.run(
        [sys.executable, "-B", "-m", "tests.lazy_runtime_read_worker", mode, kind, str(project)],
        env=environment,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


@pytest.mark.parametrize("kind", ["local"])
def test_real_retained_bundle_failure_foreign_reads_and_cold_binding(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str
) -> None:
    before = _manifest()
    produced = _run("produce", kind, tmp_path)
    continued = _run("continue", kind, tmp_path)
    cold = _run("cold", kind, tmp_path)
    assert len({produced["pid"], continued["pid"], cold["pid"]}) == 3
    produced_counts = _mapping(_mapping(produced["after"])["counts"])
    assert produced_counts["analysis_action_runs"] == 2
    assert produced_counts["analysis_action_run_terminals"] == 2
    assert produced_counts["dataset_artifacts"] == produced_counts["dataset_evidence"] == 1
    assert produced_counts["action_resource_journal"] == 0
    counts = _mapping(_mapping(cold["after"])["counts"])
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 4
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 3
    assert counts["analysis_action_run_inputs"] == 3
    assert counts["action_resource_journal"] == counts["findings"] == 0
    assert cold["before"] == cold["after"] == continued["after"]
    for name, expected in (("origin", [140, 140 / 3]), ("consumer_read", [100, 100])):
        current, restored = _mapping(continued[name]), _mapping(cold[name])
        assert current["artifact"] == restored["artifact"]
        assert current["evidence_digest"] == restored["evidence_digest"]
        assert current["graph"] == restored["graph"]
        assert current["rows"] == restored["rows"]
        rows = restored["rows"]
        assert isinstance(rows, list) and len(rows) == 1
        assert rows[0] == pytest.approx(expected)
        assert current["before"] == current["after"]
        assert restored["before"] == restored["after"]
        assert restored["revalidation"] == {
            "artifact_integrity": "valid",
            "storage_authority": "readable",
            "evidence_integrity": "valid",
        }
    for name in ("continuation_statistics", "consumer_statistics"):
        stats = _mapping(continued[name])
        assert stats["primary_queries"] == (1 if kind == "engine" else 0)
        assert stats["transferred_rows"] == 0
        if kind == "engine":
            assert stats["worker_pid"] is None
        else:
            assert isinstance(stats["worker_pid"], int)
            assert isinstance(stats["handoffs"], list) and len(stats["handoffs"]) == 2
        assert '"orders"' not in json.dumps(stats["statements"])
    assert cold["binding_object_requests"] == 0
    for name in ("binding_statistics", "consumer_binding_statistics"):
        stats = _mapping(cold[name])
        assert stats["primary_queries"] == stats["transferred_rows"] == 0
        assert stats["events"] == {"reconciliation": 1}
    after = _manifest()
    assert before == after
    evidence = {
        "schema": "marivo.slice4d.runtime/v1",
        "kind": kind,
        "candidate_before": before,
        "candidate_after": after,
        "producer": produced,
        "continuation": continued,
        "cold": cold,
    }
    destination = tmp_path / f"slice-4d-{kind}-runtime.json"
    destination.write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n")
    retained = os.environ.get("MARIVO_SLICE4D_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / destination.name).write_bytes(destination.read_bytes())
