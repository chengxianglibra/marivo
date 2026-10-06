"""Isolated original-ID audit counterexamples never depend on local evidence."""

from __future__ import annotations

import copy
import gzip
from pathlib import Path

import pytest

from scripts import r9_qualification_requirements as freeze
from scripts import r97_completion_audit as audit

Json = freeze.Json


def write(root: Path, name: str, value: Json) -> dict[str, Json]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(freeze.encode(value))
    return {"path": name, "sha256": freeze.digest(path.read_bytes())}


def setup(root: Path) -> tuple[dict[str, Json], Path, dict[str, Json]]:
    product = root / "marivo/product.py"
    product.parent.mkdir()
    product.write_text("value = 1\n")
    candidate: dict[str, Json] = {
        "content_sha256": "delivery",
        "source_sha256": {"marivo/product.py": freeze.digest(product.read_bytes())},
        "dependencies": {"pytest": "synthetic"},
        "head": "delivery-head",
    }
    concrete = write(
        root, "concrete.json", {"candidate": {**candidate, "content_sha256": "historical"}}
    )
    impact = write(root, "impact.json", {"r92": {}, "r93": {}})
    requirements: list[Json] = []
    originals: list[Json] = []
    routes: dict[str, Json] = {}
    overrides: dict[str, Json] = {}
    r93: dict[str, Json] = {}
    for index in range(394):
        identity = audit.SQLITE_DECIMAL if index == 0 else f"R9:synthetic:{index}"
        cost = index >= 366
        proofs: list[Json] = (
            ["cost", "numeric_state", "resource_cancel"]
            if 366 <= index < 379
            else ["cost"]
            if cost
            else ["submission"]
        )
        owner = "R9.6" if cost else "R9.3" if 1 <= index <= 23 else "R9.2"
        family = (
            "V17"
            if index == 393
            else "cost-scenario"
            if index >= 379
            else "cost-baseline"
            if cost
            else "synthetic"
        )
        requirements.append({"id": identity, "expectation": "success", "required_proofs": proofs})
        original: dict[str, Json] = {
            "id": identity,
            "gap_owner": owner,
            "family": family,
            "required_proofs": proofs,
            "original_expectation": "success",
            "effective_expectation": "rejection" if index == 0 else "success",
            "authority": "r93" if owner == "R9.3" else "r92",
            "pointer": "/requirements/" + identity if owner == "R9.3" else "/overrides/" + identity,
            "owning_status": "passed",
        }
        if owner == "R9.3":
            ref = write(
                root,
                f"r93/raw/raw-{index}.json",
                {
                    "status": "passed",
                    "proofs": {"submission": {"status": "passed"}},
                    "candidate_sha256": None,
                    "resources": 0,
                },
            )
            r93[identity] = {
                "status": "passed",
                "record": f"raw-{index}.json",
                "record_sha256": ref["sha256"],
                "source_package": "raw",
            }
            original["original_record_sha256"] = ref["sha256"]
        elif not cost:
            overrides[identity] = {
                "id": identity,
                "status": "passed",
                "proofs": {"submission": {"status": "passed"}},
                "candidate_sha256": "original-execution",
            }
        originals.append(original)
        routes[identity] = {
            "required_proofs": proofs,
            "status": "historical_closure_available_current_adoption_ready",
            "adoption_binding": {
                "authority": "source_numeric_impact",
                "json_pointer": "/r93" if owner == "R9.3" else "/r92",
            },
        }
    authority = write(root, "r93/implementation/acceptance.json", {"requirements": r93})
    source = write(root, "r92.json", {"overrides": overrides})
    original = write(
        root,
        "original.json",
        {"requirements": originals, "authorities": {"r92": source, "r93": authority}},
    )
    routing = root / "routing.json"
    write(
        root,
        "routing.json",
        {
            "requirements": routes,
            "authorities": {
                "original_routing": original,
                "concrete_eight_authority_proof": concrete,
                "source_numeric_impact": impact,
            },
            "separate_additional_obligations": {"DS23": {"not_in_original_394": True}},
        },
    )
    return {"requirements": requirements}, routing, candidate


def test_audit_preserves_original_ids_indirect_records_and_unwaived_gaps(tmp_path: Path) -> None:
    frozen, routing, candidate = setup(tmp_path)
    report = audit.audit(tmp_path, frozen, routing, candidate, skip_remaining_cost=True)
    assert report["counts"] == {
        "adopted_finite_owner_proofs": 366,
        "unverified": 13,
        "authorized_skipped": 15,
    }
    assert report["defects"] == []
    assert report["original_denominator"] == 394
    assert report["qualification_complete"] is report["full_r9_complete"] is False
    results = freeze.obj(report["results"])
    indirect = freeze.obj(results["R9:synthetic:1"])
    assert freeze.obj(indirect["historical_execution"])["candidate_sha256"] is None
    assert "command" in freeze.arr(indirect["unrecorded_execution_fields"])
    assert freeze.obj(results[audit.SQLITE_DECIMAL])["effective_expectation"] == "rejection"
    assert len(freeze.arr(report["unresolved_non_cost_obligations"])) == 4
    assert all(
        freeze.obj(value)["user_waiver_inferred"] is False
        for value in freeze.arr(report["unresolved_non_cost_obligations"])
    )


@pytest.mark.parametrize("fault", ["duplicate", "proof", "expectation", "product", "dependency"])
def test_original_authority_and_current_owner_drift_are_rejected(
    tmp_path: Path, fault: str
) -> None:
    frozen, routing, candidate = setup(tmp_path)
    if fault == "duplicate":
        freeze.arr(frozen["requirements"])[1] = freeze.arr(frozen["requirements"])[0]
    elif fault == "proof":
        freeze.obj(freeze.arr(frozen["requirements"])[1])["required_proofs"] = ["cost"]
    elif fault == "expectation":
        freeze.obj(freeze.arr(frozen["requirements"])[1])["expectation"] = "rejection"
    elif fault == "product":
        (tmp_path / "marivo/product.py").write_text("value = 2\n")
    else:
        candidate["dependencies"] = {"pytest": "changed"}
    with pytest.raises(ValueError):
        audit.audit(tmp_path, frozen, routing, candidate, skip_remaining_cost=True)


def test_tampered_and_nonpassed_raw_proofs_remain_unverified(tmp_path: Path) -> None:
    frozen, routing, candidate = setup(tmp_path)
    (tmp_path / "r93/raw/raw-1.json").write_text("{}")
    report = audit.audit(tmp_path, frozen, routing, candidate, skip_remaining_cost=True)
    assert freeze.obj(freeze.obj(report["results"])["R9:synthetic:1"])["status"] == "unverified"
    assert len(freeze.arr(report["defects"])) == 1
    assert report["requested_audit_scope_complete"] is False


def test_original_cost_skip_requires_explicit_execution_option(tmp_path: Path) -> None:
    frozen, routing, candidate = setup(tmp_path)
    report = audit.audit(tmp_path, frozen, routing, candidate)
    assert report["counts"] == {"adopted_finite_owner_proofs": 366, "unverified": 28}


def test_packed_evidence_is_reconstructed_and_hash_checked_without_writes(tmp_path: Path) -> None:
    original = b'{"actual_resource_release":true}\n'
    packed = gzip.compress(original, mtime=0)
    (tmp_path / "raw.json.gz.part-001").write_bytes(packed)
    write(
        tmp_path,
        "packed-attachments.json",
        {
            "raw.json": {
                "bytes": len(original),
                "sha256": freeze.digest(original),
                "packed_sha256": freeze.digest(packed),
                "parts": [
                    {
                        "path": "raw.json.gz.part-001",
                        "bytes": len(packed),
                        "sha256": freeze.digest(packed),
                    }
                ],
            }
        },
    )
    evidence = audit.Evidence(tmp_path)
    _, data = evidence.read({"path": "raw.json", "sha256": freeze.digest(original)})
    assert data["actual_resource_release"] is True
    assert not (tmp_path / "raw.json").exists()
    assert len(evidence.used) == 3
    (tmp_path / "raw.json.gz.part-001").write_bytes(b"broken")
    with pytest.raises(ValueError, match="shard mismatch"):
        audit.Evidence(tmp_path).read({"path": "raw.json", "sha256": freeze.digest(original)})


def test_bad_cost_result_cannot_satisfy_baseline_proof(tmp_path: Path) -> None:
    frozen, routing, candidate = setup(tmp_path)
    write(
        tmp_path,
        "cost.json",
        {
            "results": {
                "R9:synthetic:366": {"status": "passed", "proofs": {"cost": {"status": "passed"}}}
            }
        },
    )
    with pytest.raises(ValueError, match="baseline inventory"):
        audit.audit(
            tmp_path,
            frozen,
            routing,
            candidate,
            skip_remaining_cost=True,
            cost_results=tmp_path / "cost.json",
        )


@pytest.mark.parametrize("tamper", ["none", "log", "junit", "node"])
def test_final_audit_binds_actual_gate_and_discloses_residual_qualification(
    tmp_path: Path, tamper: str
) -> None:
    frozen, routing, candidate = setup(tmp_path)
    raw = write(tmp_path, "raw-cost.json", {"observation": "original"})
    cost: dict[str, Json] = {
        "schema": "marivo.r97.cost-baseline-binding.v1",
        "validator_sources": [],
        "results": {
            f"R9:synthetic:{index}": {
                "status": "passed",
                "execution_candidates": ["original"],
                "proofs": {
                    name: {"status": "passed", "evidence": [raw]}
                    for name in ("cost", "numeric_state", "resource_cancel")
                },
            }
            for index in range(366, 379)
        },
    }
    write(tmp_path, "cost.json", cost)
    previous = write(
        tmp_path,
        "previous-disclosure.json",
        {
            "scoped_independent_disclosure_junit": {"classes": {"tests.synthetic": ["case"]}},
            "optional_driver_and_extension_boundaries": {
                "actual_current_six_nodes": ["tests/synthetic.py::boundary"]
            },
        },
    )
    disclosed = write(
        tmp_path, "disclosure.json", {"authorities": {"original_disclosure_binding": previous}}
    )
    routes = freeze.read(routing)
    freeze.obj(routes["authorities"])["disclosure_current_C8"] = disclosed
    write(tmp_path, "routing.json", routes)
    engineering = tmp_path / "engineering"
    engineering.mkdir()
    (engineering / "command.log").write_text("All engineering stages passed\n")
    (engineering / "junit.xml").write_text(
        '<testsuite><testcase classname="tests.synthetic" name="case"/><testcase classname="tests.synthetic" name="boundary"/></testsuite>'
    )
    write(
        tmp_path,
        "engineering/run.json",
        {
            "candidate": candidate,
            "candidate_after": candidate,
            "candidate_unchanged": True,
            "command": ["make", "check-agent"],
            "exit_code": 0,
            "log_sha256": freeze.digest((engineering / "command.log").read_bytes()),
            "junit_sha256": freeze.digest((engineering / "junit.xml").read_bytes()),
        },
    )
    if tamper == "log":
        (engineering / "command.log").write_text("changed")
    elif tamper == "junit":
        (engineering / "junit.xml").write_text("changed")
    elif tamper == "node":
        (engineering / "junit.xml").write_text(
            '<testsuite><testcase classname="tests.synthetic" name="case"/></testsuite>'
        )
        run = freeze.read(engineering / "run.json")
        run["junit_sha256"] = freeze.digest((engineering / "junit.xml").read_bytes())
        write(tmp_path, "engineering/run.json", run)
    report = audit.audit(
        tmp_path,
        frozen,
        routing,
        candidate,
        skip_remaining_cost=True,
        cost_results=tmp_path / "cost.json",
        engineering_run=engineering / "run.json",
    )
    assert report["counts"] == {"adopted_finite_owner_proofs": 379, "authorized_skipped": 15}
    assert report["requested_audit_scope_complete"] is (tamper == "none")
    assert report["full_r9_complete"] is report["qualification_complete"] is False


def test_json_pointer_escapes_keep_exact_original_identity() -> None:
    value: Json = {"requirements": {"R9:C01.a/b~test": {"passed": True}}}
    assert audit.pointer(value, "/requirements/R9:C01.a~1b~0test") == {"passed": True}
    with pytest.raises(ValueError, match="pointer"):
        audit.pointer(copy.deepcopy(value), "requirements")


@pytest.mark.parametrize("fault", ["none", "unknown", "missing_backend"])
def test_sql_binding_distinguishes_public_witnesses_from_supplements(
    tmp_path: Path, fault: str
) -> None:
    identity = "R9:DS01:all:sql-owner:audit:classification-reachability-submission"
    receipts: list[Json] = [
        {
            "backend": backend,
            "public_owner_witness": True,
            "original_receipt": {"unknown_submissions": 0},
        }
        for backend in freeze.PROFILES
    ]
    receipts.extend(
        {
            "backend": "duckdb",
            "public_owner_witness": False,
            "original_receipt": {"unknown_submissions": 0},
        }
        for _ in range(2)
    )
    if fault == "unknown":
        freeze.obj(freeze.obj(receipts[-1])["original_receipt"])["unknown_submissions"] = 1
    elif fault == "missing_backend":
        freeze.obj(receipts[0])["backend"] = "mysql"
    source = write(
        tmp_path,
        "sql.json",
        {
            "mapping": {"id": "DS01", "original_mapping": [identity]},
            "status": "scoped_implementation_closed",
            "native_receipts": receipts,
            "static_audit": {"report": {"failures": []}},
        },
    )
    impact = write(tmp_path, "impact.json", {"sql": {}})
    row: dict[str, Json] = {
        "id": identity,
        "authority": "sql",
        "pointer": "/mapping",
        "gap_owner": "R9.5",
        "owning_status": "scoped_implementation_closed",
        "original_expectation": "success",
        "effective_expectation": "success",
        "required_proofs": ["submission", "disclosure"],
    }
    arguments: tuple[
        audit.Evidence, dict[str, Json], dict[str, Json], dict[str, Json], dict[str, Json]
    ] = (
        audit.Evidence(tmp_path),
        {"authorities": {"sql": source}},
        {"authorities": {"impact": impact}},
        row,
        {"adoption_binding": {"authority": "impact", "json_pointer": "/sql"}},
    )
    if fault == "none":
        result = audit.historical_binding(*arguments)
        assert result["status"] == "adopted_finite_owner_proofs"
    else:
        with pytest.raises(ValueError, match="SQL actual native"):
            audit.historical_binding(*arguments)
