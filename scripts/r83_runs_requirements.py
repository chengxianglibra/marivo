"""Attach exact executed runs keys to the unchanged R8.1 mandatory IDs."""

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


def build(root: Path, attachment: Path, output: Path) -> None:
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    rows = tuple(
        object_json(row)
        for row in array_json(frozen["requirements"])
        if object_json(row).get("responsibility") == "R8.3"
    )
    assert len(rows) == 1708 and len({row["id"] for row in rows}) == 1708
    proofs = tuple(object_json(row) for row in array_json(read_json(attachment)["proofs"]))
    assert {p["proof_class"] for p in proofs} == {
        "source_kernel",
        "fixed_kernel",
        "fresh_process_source_offline_kernel",
    }
    attached: list[Json] = []
    for row in rows:
        matches = [
            p
            for p in proofs
            if row.get("scenario") is None
            and all(
                row.get(key) == p.get(key)
                for key in (
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
            and set(array_json(row["required_parts"])) <= set(array_json(p["retained_parts"]))
            and p["input_codec"] == "r8.condition_cells/v1"
            and p["state_codec"] == "r8.run_cells/v1"
            and (row["proof_class"] == "source_kernel" or p["source_offline"] is True)
        ]
        assert len(matches) <= 1
        attached.append(
            {
                **row,
                "status": "passed" if matches else "unverified",
                "execution_command": "MARIVO_R83_EVIDENCE=<attachment> make runtime-test TESTS='tests/test_analysis_runs_r83.py'"
                if matches
                else None,
                "artifact": matches[0]["artifact"] if matches else None,
                "disposition": "exact integrated public kernel key and independent fixture oracle"
                if matches
                else "No exact frozen-ID proof attached; focused tests and broad gates do not qualify this cell.",
            }
        )
    statuses = Counter(str(object_json(row)["status"]) for row in attached)
    payload: dict[str, Json] = {
        "version": "r83-requirements/v1",
        "snapshot_sha256": hashlib.sha256((root / SNAPSHOT).read_bytes()).hexdigest(),
        "attachment_sha256": hashlib.sha256(attachment.read_bytes()).hexdigest(),
        "mandatory": len(rows),
        "counts": dict(statuses),
        "requirements": attached,
    }
    output.write_bytes(
        gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    )
    print(
        json.dumps(
            {
                "mandatory": len(rows),
                "counts": dict(statuses),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("attachment", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    build(Path.cwd(), arguments.attachment, arguments.output)
