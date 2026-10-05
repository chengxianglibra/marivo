"""Current R9 matrix integrity; collection never starts backend services."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts import r9_qualification_requirements as freeze


@pytest.fixture(scope="module")
def frozen() -> dict[str, freeze.Json]:
    current = freeze.build()
    original = freeze.load(freeze.OUTPUT)
    assert {freeze.obj(row)["id"] for row in freeze.arr(current["requirements"])} == {
        freeze.obj(row)["id"] for row in freeze.arr(original["requirements"])
    }
    return current


def test_frozen_targets_and_candidate(frozen: dict[str, freeze.Json]) -> None:
    freeze.validate(frozen)
    assert len(freeze.obj(frozen["seeds"])) == 9109
    assert set(freeze.obj(frozen["physical_profiles"])) == {
        "duckdb",
        "postgres",
        "mysql",
        "sqlite",
        "trino",
        "clickhouse",
    }
    assert all(
        freeze.obj(row)["expectation"] in ("success", "rejection")
        for row in freeze.arr(frozen["requirements"])
    )


def test_seed_mapping_is_traceability_not_cartesian_qualification(
    frozen: dict[str, freeze.Json],
) -> None:
    rows = [freeze.obj(row) for row in freeze.arr(frozen["requirements"])]
    targets = {str(row["id"]): row for row in rows}
    mappings = freeze.obj(frozen["mappings"])
    methods = freeze.obj(frozen["method_targets"])
    assert len(rows) < 500
    assert len(methods) == 9
    for seed, value in freeze.obj(frozen["seeds"]).items():
        method = str(freeze.obj(value)["method"])
        assert mappings[seed] == methods[method]
        mapped = [targets[str(identity)] for identity in freeze.arr(mappings[seed])]
        assert {row["backend"] for row in mapped if row["route"] == "ibis_python"} == {
            "duckdb",
            "postgres",
            "mysql",
            "sqlite",
            "trino",
            "clickhouse",
        }
        assert all(row["backend"] == "none" for row in mapped if row["route"] == "artifact_python")
    profile_rows = [row for row in rows if row["family"] == "source-profile"]
    assert len(profile_rows) == 19
    assert {row["profile"] for row in profile_rows if row["backend"] == "duckdb"} >= {
        "csv",
        "parquet",
        "local-json",
        "http-json-public",
        "http-json-auth",
    }
    assert {row["profile"] for row in profile_rows if row["backend"] == "trino"} == {
        "iceberg",
        "non-iceberg",
    }
    assert {row["profile"] for row in profile_rows if row["backend"] == "clickhouse"} == {
        "mergetree",
        "distributed",
    }
    assert not any(row["route"] == "static" and row["profile"] == "owner-handoff" for row in rows)
    assert all(row["required_proofs"] for row in rows)


def test_sql_and_risk_obligations_are_retained(frozen: dict[str, freeze.Json]) -> None:
    mappings = freeze.obj(frozen["mappings"])
    assert {f"DS{number:02}" for number in range(1, 23)} <= set(mappings)
    assert {f"AN{number:02}" for number in range(1, 34)} <= set(mappings)
    assert {f"V{number:02}" for number in range(1, 18)} <= set(mappings)
    sql_rows = [
        origin
        for origin, record in freeze.obj(frozen["source_rows"]).items()
        if freeze.obj(record)["disposition"] == "sql-authority"
    ]
    assert (
        len({str(identity) for origin in sql_rows for identity in freeze.arr(mappings[origin])})
        == 55
    )
    assert all(mappings[origin] for origin in sql_rows)
    rows = [freeze.obj(row) for row in freeze.arr(frozen["requirements"])]
    assert len([row for row in rows if row["expectation"] == "rejection"]) == 5
    assert any(row["scenario"] == "cross-batch-long-run" for row in rows)
    assert any(row["scenario"] == "original-fit-domain" for row in rows)
    assert any(row["scenario"] == "complete-training" for row in rows)
    assert all(row["backend"] == "none" for row in rows if row["route"] == "artifact_python")
    assert len([row for row in rows if row["family"] == "cost-baseline"]) == 13


@pytest.mark.parametrize(
    "fault", ["duplicate", "missing", "passed", "executed", "candidate", "owner"]
)
def test_false_authority_is_rejected(frozen: dict[str, freeze.Json], fault: str) -> None:
    value = copy.deepcopy(frozen)
    rows = freeze.arr(value["requirements"])
    if fault == "duplicate":
        rows.append(rows[0])
    elif fault == "missing":
        rows.pop()
    elif fault == "passed":
        freeze.obj(value["results"])["runtime_passed"] = 1
    elif fault == "executed":
        freeze.obj(value["defaults"])["executed"] = True
    elif fault == "candidate":
        freeze.obj(value["candidate"])["content_sha256"] = "wrong"
    else:
        freeze.obj(value["owners"])[freeze.SQL] = "wrong"
    with pytest.raises(ValueError):
        freeze.validate(value)


def test_shards_are_deterministic_and_reject_damage(
    frozen: dict[str, freeze.Json], tmp_path: Path
) -> None:
    small = copy.deepcopy(frozen)
    small["requirements"] = freeze.arr(small["requirements"])[:2]
    small["mappings"] = {}
    small["seeds"] = {}
    first, second = tmp_path / "first", tmp_path / "second"
    freeze.save(small, first)
    freeze.save(small, second)
    assert (first / "index.json").read_bytes() == (second / "index.json").read_bytes()
    assert freeze.load(first) == small
    with pytest.raises(ValueError, match="already exists"):
        freeze.save(small, first)
    extra = first / "requirements.json.gz.part-orphan"
    extra.write_bytes(b"orphan")
    with pytest.raises(ValueError, match="orphan"):
        freeze.load(first)
    extra.unlink()
    shard = next(first.glob("requirements.json.gz.part-*"))
    shard.write_bytes(b"broken")
    with pytest.raises(ValueError, match="digest"):
        freeze.load(first)


@pytest.mark.parametrize("proof_class", ["static", "compile", "runtime"])
def test_results_require_complete_runtime_proofs(tmp_path: Path, proof_class: str) -> None:
    payload: dict[str, freeze.Json] = {
        "candidate": {"content_sha256": "candidate"},
        "owners": {"owner": "digest"},
        "requirements": [{"id": "id", "expectation": "success"}],
        "consumers": {"test_functions": {"test.py": ["test_case"]}},
    }
    result: dict[str, freeze.Json] = {
        "candidate_sha256": "candidate",
        "overrides": {"id": {"status": "passed", "proof_class": proof_class, "exit_code": 0}},
    }
    path = tmp_path / "results.json"
    path.write_bytes(freeze.encode(result))
    with pytest.raises(ValueError):
        freeze.verify_results(payload, path)
    result["overrides"] = {
        "id": {
            "status": "blocked",
            "reason": "missing service",
            "owner": "R9.2",
            "release_condition": "bind ready service",
        }
    }
    path.write_bytes(freeze.encode(result))
    assert freeze.verify_results(payload, path) == {
        "passed": 0,
        "failed": 0,
        "blocked": 1,
        "unverified": 0,
    }
    result["overrides"] = {"unknown": {"status": "passed"}}
    path.write_bytes(freeze.encode(result))
    with pytest.raises(ValueError, match="orphan"):
        freeze.verify_results(payload, path)
