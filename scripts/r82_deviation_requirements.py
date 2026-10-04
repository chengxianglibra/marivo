"""Match actual R8.2 kernel evidence to the unchanged R8.1 requirement IDs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from marivo.analysis.methods.physical import FixedShape, NoTime, QualificationKey, SourceShape
from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    decode_payload,
    object_json,
    read_json,
)
from scripts.r82_deviation_evidence import PROOFS, KernelProof
from scripts.r82_f11_evidence import CHAINS, SourceChainProof


def key_json(key: QualificationKey) -> dict[str, Json]:
    time: dict[str, Json] = (
        {"kind": "NoTime"}
        if isinstance(key.shape.time, NoTime)
        else {
            "kind": "instant",
            "unit": key.shape.time.unit,
            "timezone": key.shape.time.timezone,
        }
    )
    shape: dict[str, Json] = {"kind": "FixedShape", "time": time}
    if isinstance(key.shape, SourceShape):
        shape = {
            "kind": "SourceShape",
            "backend": key.shape.backend,
            "form": key.shape.form,
            "table_kind": key.shape.table_kind,
            "time": time,
        }
    else:
        assert isinstance(key.shape, FixedShape)
    return {
        "method": f"{key.method.name}@v{key.method.version}",
        "input_types": [value.name for value in key.input_types],
        "input_domains": list(key.input_domains),
        "shape": shape,
        "route": key.route,
    }


def match(row: dict[str, Json], proof: KernelProof) -> bool:
    profile = {
        "KS": "string",
        "KI": "int64",
        "KC": "composite(string,int64)",
        "KT": "typed_complete_tuple",
    }[proof.key_profile]
    if proof.domain == "time" and proof.key_profile == "KT":
        profile = (
            "time_cell:string"
            if len(proof.current_key_fields) == 1 and proof.current_key_fields[0][1] == "string"
            else "invalid_time_cell_key"
        )
    required = array_json(row["required_parts"])
    return (
        isinstance(proof.origin.shape, SourceShape)
        and row["qualification_key"] == key_json(proof.actual)
        and row["implementation_id"] == proof.implementation_id
        and row["implementation_contract_version"] == f"v{proof.contract_version}"
        and row["precision_contract"] == proof.precision_contract
        and row["state_version"] == proof.state_version
        and row["numeric_policy"] == proof.numeric_policy
        and proof.input_codec == "r8.fit_inputs/v1"
        and proof.state_codec == "r8.fit_state/v1"
        and proof.selection_transform == "select_output_retain_scope@v1"
        and row["domain"] == proof.domain
        and row["key_profile"] == profile
        and proof.origin.method == proof.actual.method
        and proof.origin.input_types == proof.actual.input_types
        and proof.origin.input_domains == proof.actual.input_domains
        and row["backend"] == proof.origin.shape.backend
        and row["physical_form"] == proof.origin.shape.form
        and row["source_precision"]
        == ("none" if isinstance(proof.origin.shape.time, NoTime) else proof.origin.shape.time.unit)
        and row["time_profile"] == proof.time_profile
        and row.get("kernel_proof_class", row["proof_class"]) == proof.proof_class
        and set(required) <= {role for role, _ in proof.part_receipt_digests}
    )


def match_chain(row: dict[str, Json], proof: SourceChainProof) -> bool:
    profile = {
        "KS": "string",
        "KI": "int64",
        "KC": "composite(string,int64)",
    }.get(proof.key_profile)
    observation = object_json(row["downstream_observation"])
    return (
        row.get("scenario") == "A11_score_members_observe"
        and row["kernel_proof_class"] == "source_kernel"
        and isinstance(proof.actual.shape, SourceShape)
        and row["qualification_key"] == key_json(proof.actual)
        and row["implementation_id"] == proof.implementation_id
        and row["implementation_contract_version"] == f"v{proof.contract_version}"
        and row["precision_contract"] == proof.precision_contract
        and row["state_version"] == proof.state_version
        and row["numeric_policy"] == proof.numeric_policy
        and proof.input_codec == "r8.fit_inputs/v1"
        and proof.state_codec == "r8.fit_state/v1"
        and proof.selection_transform == "select_output_retain_scope@v1"
        and row["domain"] == proof.domain
        and row["key_profile"] == profile
        and row["physical_form"] == proof.actual.shape.form
        and row["time_profile"] == proof.time_profile
        and observation["value_type"] == proof.contribution_type
        and observation["grid_window"] == (proof.domain == "entity_time")
        and observation["reduction"] == proof.followup_reduction == "sum"
        and proof.summary_reduction == "count_defined"
        and proof.source_read_count > 0
        and proof.local_count == 1
        and 0 <= proof.last_source_read < proof.first_local_consume
        and set(array_json(row["required_parts"]))
        <= {role for role, _ in proof.consumed_fit_part_digests}
    )


def open_status(row: dict[str, Json]) -> tuple[str, str]:
    if row["physical_form"] == "parquet" and row["source_precision"] == "s":
        return (
            "blocked",
            "The current Parquet fixture writer stores milliseconds, so no second-precision kernel evidence exists. The original requirement remains mandatory.",
        )
    if row.get("scenario") == "A11_score_members_observe":
        if row["kernel_proof_class"] != "source_kernel":
            return (
                "blocked",
                "A fixed/cold public observation continuation with captured contributions and an admitted public entry is required; source F11 and missing-part rejection cannot substitute.",
            )
        if row["input_types"] in (["decimal(9,2)"], ["decimal(18,6)"]):
            return (
                "blocked",
                "Accepted Decimal sum/change yields decimal(38,s), which differs from this frozen narrow scoring key. No narrowing cast or key relabeling is used; a contract decision is pending.",
            )
        return "unverified", "public F11 observation chain evidence required"
    if row.get("scenario") is not None:
        return "unverified", "independent public scenario evidence required"
    return "unverified", "no matching executed key, origin, domain, parts and kernel proof"


def build(
    root: Path, attachments: tuple[Path, ...], output: Path, chains: tuple[Path, ...] = ()
) -> None:
    root = root.resolve()
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    rows = tuple(
        object_json(row)
        for row in array_json(frozen["requirements"])
        if object_json(row).get("responsibility") == "R8.2"
        and str(object_json(row).get("method", "")).startswith("deviation.")
    )
    assert len(rows) == 15564 and sum(row["status"] == "blocked" for row in rows) == 2232
    assert len({row["id"] for row in rows}) == len(rows)
    proofs: dict[str, list[tuple[str, int, KernelProof]]] = {}
    chain_proofs: dict[str, list[tuple[str, int, SourceChainProof]]] = {}
    files: list[Json] = []
    for attachment in attachments:
        attachment = attachment.resolve()
        data = attachment.read_bytes()
        decoded = gzip.decompress(data) if attachment.suffix == ".gz" else data
        records = PROOFS.validate_json(decoded)
        files.append(
            {
                "path": str(attachment.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "proof_count": len(records),
            }
        )
        for index, proof in enumerate(records):
            assert isinstance(proof.origin.shape, SourceShape)
            key = json.dumps(key_json(proof.actual), sort_keys=True)
            proofs.setdefault(key, []).append((str(attachment.relative_to(root)), index, proof))
    for attachment in chains:
        attachment = attachment.resolve()
        data = attachment.read_bytes()
        chain_records = CHAINS.validate_json(
            gzip.decompress(data) if attachment.suffix == ".gz" else data
        )
        files.append(
            {
                "path": str(attachment.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_chain_count": len(chain_records),
            }
        )
        for index, chain in enumerate(chain_records):
            key = json.dumps(key_json(chain.actual), sort_keys=True)
            chain_proofs.setdefault(key, []).append(
                (str(attachment.relative_to(root)), index, chain)
            )
    ledger: list[Json] = []
    counts: Counter[str] = Counter()
    for row in rows:
        candidates = proofs.get(json.dumps(row["qualification_key"], sort_keys=True), [])
        qualified = next(
            (
                (path, index, proof)
                for path, index, proof in candidates
                if row.get("scenario") is None and match(row, proof)
            ),
            None,
        )
        chain_match = (
            next(
                (
                    (path, index, chain)
                    for path, index, chain in chain_proofs.get(
                        json.dumps(row["qualification_key"], sort_keys=True), []
                    )
                    if match_chain(row, chain)
                ),
                None,
            )
            if row.get("scenario") == "A11_score_members_observe"
            else None
        )
        status, reason = open_status(row)
        current: dict[str, Json] = {
            "requirement": row,
            "status": "passed" if qualified is not None or chain_match is not None else status,
            "evidence": None,
            "reason": None if qualified is not None or chain_match is not None else reason,
        }
        if qualified is not None:
            path, index, proof = qualified
            current["evidence"] = {
                "attachment": path,
                "record_index": index,
                "artifact_ref": proof.artifact_ref,
                "run_ref": proof.producing_run_ref,
                "execution_key": proof.execution_key,
            }
        elif chain_match is not None:
            path, index, chain = chain_match
            current["evidence"] = {
                "attachment": path,
                "record_index": index,
                "artifact_ref": chain.summary_artifact,
                "run_ref": chain.producing_run_ref,
                "execution_key": chain.execution_key,
                "consumed_fit_scope": chain.fit_scope,
            }
        counts[str(current["status"])] += 1
        ledger.append(current)
    payload: dict[str, Json] = {
        "version": "r82-requirements/v1",
        "historical_snapshot_sha256": hashlib.sha256((root / SNAPSHOT).read_bytes()).hexdigest(),
        "mandatory_count": len(rows),
        "originally_blocked_count": 2232,
        "phase_complete": counts["passed"] == len(rows),
        "counts": dict(counts),
        "attachments": files,
        "requirements": ledger,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(data, mtime=0))
    print(
        json.dumps(
            {"counts": dict(counts), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attachment", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chain-attachment", action="append", type=Path, default=[])
    args = parser.parse_args()
    build(Path.cwd(), tuple(args.attachment), args.output, tuple(args.chain_attachment))
