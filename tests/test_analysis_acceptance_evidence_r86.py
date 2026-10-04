"""Independent anti-promotion checks for R8.6 evidence and installed staging."""

import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    checked,
    decode_payload,
    object_json,
    read_json,
)
from scripts.r86_acceptance_requirements import FIELDS, handoff, match, source_proofs
from tests.installed_wheel_probe import surface_snapshot
from tests.test_analysis_runtime_wheel import R8_TESTS, _stage_tests


def pair() -> tuple[dict[str, Json], dict[str, Json]]:
    row: dict[str, Json] = {
        **{key: key for key in FIELDS},
        "scenario": "every_new_result_cold",
        "V": "V20",
        "kernel_proof_class": "fresh_process_source_offline_kernel",
        "required_parts": ["fit_inputs", "fit_state"],
    }
    proof: dict[str, Json] = {
        **row,
        "scenarios": ["every_new_result_cold"],
        "retained_parts": ["fit_inputs", "fit_state"],
        "oracle": "independent original facts",
        "snapshot": {"artifact": "retained-result"},
        "source_offline": True,
        "installed": True,
        "check": {"exit_code": 0},
    }
    return row, proof


@pytest.mark.parametrize("field", FIELDS)
def test_exact_scenario_key_and_process_authority(field: str) -> None:
    row, original = pair()
    assert match(row, original)
    proof = deepcopy(original)
    proof[field] = "wrong-authority"
    assert not match(row, proof)


@pytest.mark.parametrize(
    "fault",
    ("scenario", "parts", "oracle", "snapshot", "offline", "installed", "failed_check", "blocked"),
)
def test_installed_transport_or_a_broad_pass_cannot_grant_another_obligation(fault: str) -> None:
    row, proof = pair()
    changes: dict[str, tuple[str, Json]] = {
        "scenario": ("scenarios", ["another-scenario"]),
        "parts": ("retained_parts", []),
        "oracle": ("oracle", None),
        "snapshot": ("snapshot", None),
        "offline": ("source_offline", False),
        "installed": ("installed", False),
        "failed_check": ("check", {"exit_code": 1}),
        "blocked": ("outcome", "blocked"),
    }
    field, value = changes[fault]
    proof[field] = value
    assert not match(row, proof)


def test_current_surface_snapshot_and_isolated_r8_staging(tmp_path: Path) -> None:
    snapshot = surface_snapshot()
    names = {item["name"] for item in snapshot}
    assert {"LogicalDeviationResult", "LogicalTimeRunResult", "LogicalForecastResult"} <= names
    assert not {"LogicalCandidateDataset", "LogicalAssociationDataset"} & names
    _stage_tests(tmp_path, (*R8_TESTS, "r86_worker"))
    assert not (tmp_path / "marivo").exists()
    for path in (
        "tests/r86_worker.py",
        "tests/r86_journeys.py",
        "scripts/r81_static_freeze.py",
        "scripts/r82_deviation_requirements.py",
    ):
        assert (tmp_path / path).is_file()


def test_full_handoff_retains_frozen_ids_and_missing_predecessor_records(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    summary = handoff(root, frozen, [], tmp_path)
    payload = object_json(
        checked(json.loads(gzip.decompress((tmp_path / "r8-handoff.json.gz").read_bytes())))
    )
    records = [object_json(r) for r in array_json(payload["requirements"])]
    assert summary["mandatory"] == 53695
    assert {r["id"] for r in records} == {
        object_json(r)["id"] for r in array_json(frozen["requirements"])
    }
    missing = [r for r in records if r["evidence_owner"] == "missing_predecessor_record"]
    assert any(r["responsibility"] == "R8.2" for r in missing)
    assert all(r["status"] == "unverified" for r in missing)
    assert summary["r8_complete"] is False


def test_raw_proof_requires_a_successful_log_with_exact_digest(tmp_path: Path) -> None:
    _, proof = pair()
    (tmp_path / "a11-members-zscore-table.json").write_text(json.dumps(proof))
    log = tmp_path / "passed.log"
    log.write_text("passed assertion owner")
    check: dict[str, Json] = {
        "exit_code": 1,
        "log": log.name,
        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "test_scopes": [
            "tests/test_analysis_acceptance_r86.py::test_a11_score_select_members_followup_one_dag"
        ],
    }
    path = tmp_path / "checks.json"
    path.write_text(json.dumps({"acceptance": check}))
    assert source_proofs(tmp_path) == []
    check["exit_code"] = 0
    check["log_sha256"] = "foreign-log"
    path.write_text(json.dumps({"acceptance": check}))
    assert source_proofs(tmp_path) == []
    check["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    path.write_text(json.dumps({"acceptance": check}))
    assert len(source_proofs(tmp_path)) == 1
    check["test_scopes"] = ["tests/test_analysis_runs_r83.py"]
    path.write_text(json.dumps({"acceptance": check}))
    assert source_proofs(tmp_path) == []
