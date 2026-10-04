"""Independent refusal gates for exact R8.4 requirement evidence attachment."""

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    decode_payload,
    object_json,
    read_json,
)
from scripts.r84_statistics_requirements import build, match


@pytest.fixture(scope="module")
def frozen_statistics() -> tuple[dict[str, Json], dict[str, Json]]:
    root = Path.cwd()
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    proofs = array_json(
        read_json(
            root / "docs/superpowers/specs/2026-10-04-marivo-r84-evidence/public-processes.json"
        )["proofs"]
    )
    proof = next(object_json(p) for p in proofs if object_json(p)["proof_class"] == "fixed_kernel")
    row = next(
        object_json(r) for r in array_json(frozen["requirements"]) if match(object_json(r), proof)
    )
    assert row["responsibility"] == "R8.4"
    return row, proof


@pytest.mark.parametrize(
    "field",
    (
        "proof_class",
        "qualification_key",
        "implementation_id",
        "implementation_contract_version",
        "precision_contract",
        "state_version",
        "numeric_policy",
        "domain",
        "key_profile",
        "origin_profile",
        "time_profile",
        "input_codec",
        "state_codec",
        "source_offline",
    ),
)
def test_each_frozen_authority_field_is_required(
    frozen_statistics: tuple[dict[str, Json], dict[str, Json]], field: str
) -> None:
    row, original = frozen_statistics
    assert match(row, original)
    proof = deepcopy(original)
    proof[field] = "damaged"
    assert not match(row, proof)


def test_required_part_and_scenario_cannot_be_substituted(
    frozen_statistics: tuple[dict[str, Json], dict[str, Json]],
) -> None:
    row, original = frozen_statistics
    proof = deepcopy(original)
    proof["retained_parts"] = [p for p in array_json(proof["retained_parts"]) if p != "grid_cells"]
    assert not match(row, proof)
    scenario = {**row, "scenario": "different-obligation"}
    assert not match(scenario, original)


def test_ledger_preserves_all_frozen_fields_and_adds_execution_oracle(tmp_path: Path) -> None:
    root = Path.cwd()
    attachment = (
        root / "docs/superpowers/specs/2026-10-04-marivo-r84-evidence/public-processes.json"
    )
    output = tmp_path / "requirements.json.gz"
    build(root, attachment, output)
    decoded: Json = json.loads(gzip.decompress(output.read_bytes()))
    ledger = object_json(decoded)
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    originals = {
        str(row["id"]): row
        for value in array_json(frozen["requirements"])
        if (row := object_json(value))["responsibility"] == "R8.4"
    }
    rows = tuple(object_json(value) for value in array_json(ledger["requirements"]))
    assert len(originals) == len(rows) == len({row["id"] for row in rows}) == 35662
    for row in rows:
        original = originals[str(row["id"])]
        assert all(row[key] == value for key, value in original.items() if key != "status")
        if row["status"] == "passed":
            assert isinstance(row["execution_oracle"], str)
            assert row["execution_oracle"]
            assert row["exit_code"] == 0
        else:
            assert row["execution_oracle"] is None
