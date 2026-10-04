"""Attach exact statistical kernel executions to the unchanged R8.1 IDs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    decode_payload,
    object_json,
    read_json,
)

COMMAND = (
    "MARIVO_R84_EVIDENCE=docs/superpowers/specs/2026-10-04-marivo-r84-evidence/"
    "public-processes.json make runtime-test "
    "TESTS='tests/test_analysis_statistics_r84.py::test_three_process_offline_statistics'"
)


def match(row: dict[str, Json], proof: dict[str, Json]) -> bool:
    """Require an exact frozen kernel key and its original retained authority."""
    return (
        row.get("scenario") is None
        and all(
            row.get(k) == proof.get(k)
            for k in (
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
            )
        )
        and set(array_json(row["required_parts"])) <= set(array_json(proof["retained_parts"]))
        and (proof["input_codec"], proof["state_codec"])
        in (
            ("r8.pair_inputs/v1", "r8.association_state/v1"),
            ("r8.training_inputs/v1", "r8.forecast_state/v1"),
        )
        and (row["proof_class"] == "source_kernel" or proof["source_offline"] is True)
    )


def build(root: Path, attachment: Path, output: Path) -> None:
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    rows = tuple(
        object_json(r)
        for r in array_json(frozen["requirements"])
        if object_json(r).get("responsibility") == "R8.4"
    )
    assert len(rows) == 35662 and len({r["id"] for r in rows}) == 35662
    proofs = tuple(object_json(r) for r in array_json(read_json(attachment)["proofs"]))
    assert {p["proof_class"] for p in proofs} == {
        "source_kernel",
        "fixed_kernel",
        "fresh_process_source_offline_kernel",
    }
    attached: list[Json] = []
    for row in rows:
        matches = [p for p in proofs if match(row, p)]
        assert len(matches) <= 1
        status = "passed" if matches else "blocked" if row["status"] == "blocked" else "unverified"
        attached.append(
            {
                **row,
                "status": status,
                "execution_command": COMMAND if matches else None,
                "exit_code": 0 if matches else None,
                "artifact": matches[0]["artifact"] if matches else None,
                "attachment_sha256": hashlib.sha256(attachment.read_bytes()).hexdigest()
                if matches
                else None,
                "execution_oracle": matches[0]["oracle"] if matches else None,
                "disposition": "Exact public kernel key with original capture and independent fixture equations"
                if matches
                else "Frozen native route remains blocked"
                if status == "blocked"
                else "No exact ID proof attached; focused tests do not qualify this cell",
            }
        )
    counts: dict[str, Json] = {
        k: Counter(str(object_json(r)["status"]) for r in attached)[k]
        for k in ("passed", "failed", "blocked", "skipped", "unverified")
    }
    payload: dict[str, Json] = {
        "version": "r84-requirements/v1",
        "snapshot_sha256": hashlib.sha256((root / SNAPSHOT).read_bytes()).hexdigest(),
        "attachment_sha256": hashlib.sha256(attachment.read_bytes()).hexdigest(),
        "mandatory": len(rows),
        "counts": counts,
        "requirements": attached,
    }
    output.write_bytes(
        gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    )
    print(
        json.dumps(
            {
                "mandatory": len(rows),
                "counts": counts,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("attachment", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(Path.cwd(), args.attachment, args.output)
