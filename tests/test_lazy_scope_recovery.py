"""Independent source windows remain bound across source-free process recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _run(arguments: tuple[str, ...]) -> dict[str, object]:
    environment = os.environ.copy()
    environment["MARIVO_TELEMETRY"] = "off"
    process = subprocess.run(
        [sys.executable, "-B", "-m", "tests.lazy_scope_recovery_worker", *arguments],
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


def test_january_membership_and_independent_observation_survive_cold_recovery(
    tmp_path: Path,
) -> None:
    produced = _run(("produce", str(tmp_path)))
    assert produced["reference_values"] == {"january": 30, "february": 300, "unscoped": 3000}
    artifacts = _object(produced["artifacts"])
    explicit, unscoped = _object(artifacts["february"]), _object(artifacts["unscoped"])
    assert explicit["authored_observation_scope"] == ["2026-02-01", "2026-03-01", "closed_open"]
    assert unscoped["authored_observation_scope"] is None
    assert explicit["definition_fingerprint"] != unscoped["definition_fingerprint"]
    assert explicit["rows"] == [{"snapshot_value": 300}]
    assert unscoped["rows"] == [{"snapshot_value": 3000}]
    for artifact in (explicit, unscoped):
        assert _object(artifact["execution_statistics"])["primary_queries"] == 1
    counts = _object(_object(produced["after"])["counts"])
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 2
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 2
    assert counts["findings"] == counts["action_resource_journal"] == 0
    (tmp_path / "warehouse.duckdb").rename(tmp_path / "warehouse.offline")
    recovered = _run(
        (
            "recover",
            str(tmp_path),
            "--session",
            _text(produced["session_ref"]),
            "--artifacts",
            _text(explicit["artifact_ref"]),
            _text(unscoped["artifact_ref"]),
        )
    )
    assert recovered["pid"] != produced["pid"]
    assert recovered["before"] == recovered["after"] == produced["after"]
    cold_artifacts = _object(recovered["artifacts"])
    for name in ("february", "unscoped"):
        original, cold = _object(artifacts[name]), _object(cold_artifacts[name])
        for field in ("artifact_ref", "definition_fingerprint", "rows", "artifact", "descriptor"):
            assert cold[field] == original[field]
        authority = _object(_object(cold["descriptor"])["population_authority"])
        assert authority["membership_scope"] == ["2026-01-01", "2026-02-01", "closed_open"]
        selection = authority["version_selection"]
        assert isinstance(selection, list)
        assert selection[0] == "snapshot" and selection[2] == "2026-01-31"
        validations = authority["validation_results"]
        assert isinstance(validations, list)
        names = []
        for validation in validations:
            assert isinstance(validation, list) and len(validation) == 2
            names.append(_text(validation[0]))
            assert validation[1] == 0
        assert len(names) == len(set(names))
        assert (
            sum(name.startswith("sales.snapshot_value.status_time_non_null") for name in names) >= 2
        )
    assert not any(_object(recovered["forbidden_attempts"]).values())
    stats = _object(recovered["statistics"])
    assert stats["primary_queries"] == stats["validation_queries"] == stats["source_fences"] == 0
    assert stats["events"] == {} and stats["statements"] == []
    assert not (tmp_path / "warehouse.duckdb").exists()
