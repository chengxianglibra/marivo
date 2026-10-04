"""Independent denominators and negative evidence-promotion guards for R8.5."""

import gzip
import json
from pathlib import Path

import pytest

from scripts import r85_retirement_requirements as ledger
from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    decode_payload,
    object_json,
    read_json,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/superpowers/specs/2026-10-04-marivo-r85-evidence"


def read_gzip(path: Path) -> dict[str, Json]:
    return object_json(json.loads(gzip.decompress(path.read_bytes())))


def inputs(tmp_path: Path) -> tuple[Path, Path]:
    proofs = tmp_path / "proofs"
    proofs.mkdir()
    for index, proof in enumerate(
        array_json(read_gzip(EVIDENCE / "public-views.json.gz")["proofs"])
    ):
        (proofs / f"{index}.json").write_text(json.dumps(proof))
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps(read_gzip(EVIDENCE / "requirements.json.gz")["checks"]))
    return proofs, checks


def test_original_ids_profiles_and_test_symbol_denominators_are_preserved() -> None:
    frozen = decode_payload(ROOT, read_json(ROOT / SNAPSHOT))
    expected = {
        object_json(row)["id"]: object_json(row)
        for row in array_json(frozen["requirements"])
        if object_json(row)["responsibility"] == "R8.5"
    }
    actual = [
        object_json(row)
        for row in array_json(read_gzip(EVIDENCE / "requirements.json.gz")["requirements"])
    ]
    assert len(expected) == len(actual) == 171
    assert sum(row["V"] == "V15" for row in actual) == 54
    assert sum(row["V"] == "V19" for row in actual) == 117
    assert {row["id"] for row in actual} == set(expected)
    for row in actual:
        assert row["frozen_requirement"] == expected[row["id"]]
    records = read_gzip(EVIDENCE / "retirement-records.json.gz")
    assert len(array_json(records["symbols"])) == 449
    assert len(array_json(records["legacy_tests"])) == 391
    assert records["snapshot_sha256"] == ledger.sha(ROOT / SNAPSHOT)
    for raw in array_json(records["legacy_tests"]):
        row = object_json(raw)
        if row["physical_deletion"] == "old_harness_removed":
            audit = object_json(row["assertion_audit"])
            assert audit["original_body_sha256"] == row["body_sha256"]
            assert [object_json(a)["original"] for a in array_json(audit["assertions"])] == row[
                "original_assertions"
            ]


@pytest.mark.parametrize(
    "fault",
    ("precision_contract", "implementation_id", "key_profile", "state_version", "required_part"),
)
def test_method_name_cannot_promote_wrong_identity_or_missing_parts(
    tmp_path: Path, fault: str
) -> None:
    proofs, checks = inputs(tmp_path)
    first_path = proofs / "0.json"
    first = read_json(first_path)
    method = first["method"]
    if fault == "required_part":
        first["retained_parts"] = []
    else:
        first[fault] = "independent-wrong-identity"
    first_path.write_text(json.dumps(first))
    summary = ledger.build(ROOT, proofs, checks, tmp_path / "output")
    rows = array_json(read_gzip(tmp_path / "output/requirements.json.gz")["requirements"])
    assert all(
        object_json(r)["status"] != "passed" for r in rows if object_json(r)["method"] == method
    )
    assert summary["r85_complete"] is False


def test_failed_disclosure_check_cannot_grant_cutover(tmp_path: Path) -> None:
    proofs, checks = inputs(tmp_path)
    values = read_json(checks)
    object_json(values["disclosure"])["exit_code"] = 1
    checks.write_text(json.dumps(values))
    summary = ledger.build(ROOT, proofs, checks, tmp_path / "output")
    assert object_json(summary["counts"])["passed"] == 54
    assert summary["r85_complete"] is False


def test_invented_test_owner_cannot_close_an_assertion_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proofs, checks = inputs(tmp_path)
    audit = read_gzip(EVIDENCE / "test-transfer-audit.json.gz")
    first = object_json(array_json(audit["records"])[0])
    first["current_test_owners"] = ["tests/test_analysis_retirement_r85.py::test_invented_owner"]
    changed = tmp_path / "audit.json.gz"
    changed.write_bytes(gzip.compress(json.dumps(audit).encode()))
    monkeypatch.setattr(ledger, "AUDIT", str(changed))
    summary = ledger.build(ROOT, proofs, checks, tmp_path / "output")
    count = summary["unverified_test_transfers"]
    assert isinstance(count, int) and count >= 1
    assert summary["r85_complete"] is False
