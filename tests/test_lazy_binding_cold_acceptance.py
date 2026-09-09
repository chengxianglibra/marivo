"""Three real targets preserve source captures through fresh-process exact reuse."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.analysis.materialization.targets import S3Access
from tests.lazy_binding_cold_worker import ALPHA, BETA
from tests.lazy_execution_fixtures import controlled_json_source, seed_execution_database
from tests.test_lazy_adapter_runtime_acceptance import _manifest

pytestmark = pytest.mark.runtime


def _run(
    mode: str, kind: str, project: Path, url: str, session: str, access: S3Access | None
) -> dict[str, object]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    if access is not None:
        environment.update(
            MARIVO_TEST_S3_ENDPOINT=access.endpoint_url, MARIVO_TEST_S3_BUCKET=access.bucket
        )
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_binding_cold_worker",
            mode,
            kind,
            str(project),
            url,
            "--session",
            session,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert len(process.stdout.encode()) < 1_048_576
    if mode == "produce":
        assert ALPHA in process.stdout and BETA in process.stdout
    else:
        assert ALPHA not in process.stdout and BETA not in process.stdout
    value: object = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("kind", ("local", "engine", "object"))
def test_captured_bindings_survive_scope_exit_and_source_free_cold_process(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str
) -> None:
    access = None
    if kind == "object":
        selected: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(selected, S3Access)
        access = selected
    candidate_before = _manifest()
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    with controlled_json_source(tenant_values={ALPHA: 10.0, BETA: 25.0}) as server:
        produced = _run("produce", kind, tmp_path, server.url, "", access)
        assert len(server.requests) == 2
        url = server.url
    database.rename(tmp_path / "warehouse.offline")
    cold = _run("cold", kind, tmp_path, url, str(produced["session"]), access)
    assert produced["pid"] != cold["pid"]
    assert (
        cold["before"]
        == cold["after"]
        == produced["after"]
        == {
            "analysis_action_runs": 2,
            "analysis_action_run_terminals": 2,
            "dataset_artifacts": 2,
            "dataset_evidence": 2,
            "action_resource_journal": 0,
        }
    )
    original, recovered = produced["results"], cold["results"]
    assert isinstance(original, list) and isinstance(recovered, list)
    assert len(original) == len(recovered) == 2
    identities = []
    for expected, actual in zip(original, recovered, strict=True):
        assert isinstance(expected, dict) and isinstance(actual, dict)
        for field in ("definition", "artifact", "run", "value"):
            assert actual[field] == expected[field]
        identities.append((actual["definition"], actual["artifact"], actual["run"]))
        stats = actual["statistics"]
        assert isinstance(stats, dict)
        assert stats["events"] == {"reconciliation": 1}
        assert stats["primary_queries"] == stats["validation_queries"] == 0
        assert stats["transferred_rows"] == stats["transferred_bytes"] == 0
        assert stats["worker_pid"] is None and stats["statements"] == []
    assert all(identities[0][index] != identities[1][index] for index in range(3))
    assert [item["value"] for item in recovered] == [10.0, 25.0]
    assert not database.exists()
    candidate_after = _manifest()
    assert candidate_before == candidate_after
    evidence = {
        "schema": "marivo.slice4c.binding-runtime/v1",
        "kind": kind,
        "candidate_before": candidate_before,
        "candidate_after": candidate_after,
        "producer": produced,
        "cold": cold,
    }
    destination = tmp_path / f"binding-{kind}.json"
    destination.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    retained = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if retained:
        directory = Path(retained)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / destination.name).write_bytes(destination.read_bytes())
