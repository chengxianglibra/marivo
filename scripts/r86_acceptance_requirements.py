"""Preserve R8.6 IDs and attach only exact scenario and execution authority."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    checked,
    decode_payload,
    object_json,
    read_json,
)

FIELDS = (
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
    "kernel_proof_class",
)


def package_evidence(evidence: Path, output: Path) -> None:
    """Retain exact raw bytes in one deterministic, independently hashed bundle."""
    files: dict[str, Json] = {}
    for path in sorted(evidence.rglob("*")):
        if path.is_file() and not path.name.startswith("ledger"):
            data = path.read_bytes()
            files[str(path.relative_to(evidence))] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "data_base64": base64.b64encode(data).decode(),
            }
    compressed = gzip.compress(
        json.dumps({"files": files}, sort_keys=True, separators=(",", ":")).encode(), mtime=0
    )
    parts: list[Json] = []
    for offset in range(0, len(compressed), 512 * 1024):
        path = output / f"attachments.json.gz.part-{len(parts) + 1:02d}"
        chunk = compressed[offset : offset + 512 * 1024]
        path.write_bytes(chunk)
        parts.append(
            {"path": path.name, "sha256": hashlib.sha256(chunk).hexdigest(), "bytes": len(chunk)}
        )
    for path in output.glob("attachments.json.gz*"):
        if path.name not in {object_json(p)["path"] for p in parts}:
            path.unlink()
    index: dict[str, Json] = {
        "bundle_parts": parts,
        "bundle_sha256": hashlib.sha256(compressed).hexdigest(),
        "files": {
            name: {key: object_json(value)[key] for key in ("sha256", "bytes")}
            for name, value in files.items()
        },
    }
    (output / "attachments-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    )


def restore_evidence(bundle: Path, destination: Path) -> None:
    """Restore reviewed local evidence, checking paths and raw hashes first."""
    if bundle.suffix == ".json":
        index = read_json(bundle)
        chunks = []
        for raw in array_json(index["bundle_parts"]):
            part = object_json(raw)
            name = part["path"]
            assert isinstance(name, str) and Path(name).name == name
            chunk = (bundle.parent / name).read_bytes()
            assert (
                hashlib.sha256(chunk).hexdigest() == part["sha256"] and len(chunk) == part["bytes"]
            )
            chunks.append(chunk)
        compressed = b"".join(chunks)
        assert hashlib.sha256(compressed).hexdigest() == index["bundle_sha256"]
    else:
        compressed = bundle.read_bytes()
    payload = object_json(checked(json.loads(gzip.decompress(compressed))))
    for name, raw in object_json(payload["files"]).items():
        relative = Path(name)
        assert not relative.is_absolute() and ".." not in relative.parts
        entry = object_json(raw)
        encoded = entry["data_base64"]
        assert isinstance(encoded, str)
        data = base64.b64decode(encoded, validate=True)
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["bytes"]
        path = destination / relative
        assert path.resolve().is_relative_to(destination.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def matches_authority(row: dict[str, Json], proof: dict[str, Json]) -> bool:
    return (
        all(row.get(field) == proof.get(field) for field in FIELDS)
        and row["scenario"] in array_json(proof.get("scenarios", []))
        and set(array_json(row["required_parts"])) <= set(array_json(proof["retained_parts"]))
        and bool(proof.get("oracle"))
        and isinstance(proof.get("snapshot"), dict)
        and isinstance(proof.get("check"), dict)
        and object_json(proof["check"]).get("exit_code") == 0
        and (row["kernel_proof_class"] == "source_kernel" or proof.get("source_offline") is True)
        and (row["V"] != "V20" or proof.get("installed") is True)
    )


def match(row: dict[str, Json], proof: dict[str, Json]) -> bool:
    return matches_authority(row, proof) and proof.get("outcome") != "blocked"


def _proofs(directory: Path) -> list[dict[str, Json]]:
    proofs: list[dict[str, Json]] = []
    for path in sorted(directory.glob("*.json")):
        raw = checked(json.loads(path.read_text()))
        if not isinstance(raw, dict):
            continue
        payload = object_json(raw)
        if "reports" in payload:
            for raw in array_json(payload["reports"]):
                proofs.extend(object_json(p) for p in array_json(object_json(raw)["proofs"]))
        elif "kernel_proof_class" in payload:
            proofs.append(payload)
    return proofs


def source_proofs(evidence: Path) -> list[dict[str, Json]]:
    """Bind raw proofs to a successful explicit scope and its retained log."""
    checks = read_json(evidence / "checks.json")
    result: list[dict[str, Json]] = []
    for path in sorted(evidence.glob("*.json")):
        check_id = (
            "next-round"
            if path.name.startswith("a11-next-")
            else "sharing"
            if path.name.startswith("sharing-")
            else "source-authority"
            if path.name.startswith("source-authority-")
            else "faults"
            if path.name.startswith("fault-")
            else "acceptance"
            if path.name.startswith("a11-") or path.name.endswith("-processes.json")
            else None
        )
        if check_id is None:
            continue
        test_owner = (
            "tests/test_analysis_acceptance_r86.py::test_a11_direct_score_runs_and_blocked_next_round"
            if check_id == "next-round"
            else "tests/test_analysis_acceptance_r86.py::test_shared_method_and_transport_are_distinct_from_new_kernels"
            if check_id == "sharing"
            else "tests/test_analysis_faults_r86.py::test_source_parts_versions_keys_scope_and_finding_authority"
            if check_id == "source-authority"
            else "tests/test_analysis_faults_r86.py::test_resources_atomic_publication_and_deadline"
            if check_id == "faults"
            else "tests/test_analysis_acceptance_r86.py::test_nine_methods_three_independent_processes"
            if path.name.endswith("-processes.json")
            else "tests/test_analysis_acceptance_r86.py::test_a11_score_select_members_followup_one_dag"
            if path.name.startswith("a11-members-")
            else "tests/test_analysis_acceptance_r86.py::test_a11_category_time_three_methods_three_models"
        )
        check = object_json(checks[check_id])
        log = evidence / str(check["log"])
        if (
            check["exit_code"] != 0
            or check["log_sha256"] != hashlib.sha256(log.read_bytes()).hexdigest()
            or not any(
                test_owner == scope or test_owner.startswith(str(scope) + "::")
                for scope in array_json(check.get("test_scopes", []))
            )
        ):
            continue
        payload = read_json(path)
        attached = (
            [
                object_json(p)
                for raw in array_json(payload["reports"])
                for p in array_json(object_json(raw)["proofs"])
            ]
            if "reports" in payload
            else [payload]
        )
        result.extend(
            {
                **p,
                "check": check,
                "test_owner": test_owner,
                "evidence_file": path.name,
                "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for p in attached
        )
    return result


def handoff(
    root: Path, frozen: dict[str, Json], current: list[Json], output: Path
) -> dict[str, Json]:
    """Preserve every original ID, including omissions in predecessor ledgers."""
    prior: dict[str, dict[str, Json]] = {}
    ledgers: dict[str, Json] = {}
    for phase in ("r82", "r83", "r84", "r85"):
        directory = root / f"docs/superpowers/specs/2026-10-04-marivo-{phase}-evidence"
        paths = sorted(directory.glob("requirements.json.gz*"))
        ledger_bytes = b"".join(p.read_bytes() for p in paths)
        ledger = object_json(json.loads(gzip.decompress(ledger_bytes)))
        ledgers[phase] = {
            "paths": [str(p.relative_to(root)) for p in paths],
            "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        }
        for record in array_json(ledger["requirements"]):
            entry = object_json(record)
            original = object_json(entry.get("requirement", entry.get("frozen_requirement", entry)))
            identity = str(original["id"])
            assert identity not in prior
            prior[identity] = {"status": entry["status"], "ledger": phase}
    local = {str(object_json(r)["id"]): object_json(r) for r in current}
    records: list[Json] = []
    for raw in array_json(frozen["requirements"]):
        row = object_json(raw)
        identity = str(row["id"])
        evidence = local.get(identity, prior.get(identity))
        records.append(
            {
                "id": identity,
                "responsibility": row["responsibility"],
                "status": evidence["status"] if evidence else "unverified",
                "evidence_owner": "r86"
                if identity in local
                else evidence["ledger"]
                if evidence
                else "missing_predecessor_record",
            }
        )
    assert len(records) == len({object_json(r)["id"] for r in records}) == 53695
    phases: dict[str, Json] = {
        phase: {
            key: int(value)
            for key, value in Counter(
                str(object_json(r)["status"])
                for r in records
                if object_json(r)["responsibility"] == phase
            ).items()
        }
        for phase in ("R8.2", "R8.3", "R8.4", "R8.5", "R8.6")
    }
    summary: dict[str, Json] = {
        "mandatory": 53695,
        "counts_by_phase": phases,
        "prior_ledgers": ledgers,
        "r8_complete": all(object_json(r)["status"] == "passed" for r in records),
    }
    (output / "r8-handoff.json.gz").write_bytes(
        gzip.compress(
            json.dumps(
                {**summary, "requirements": records}, sort_keys=True, separators=(",", ":")
            ).encode(),
            mtime=0,
        )
    )
    return summary


def backend_targets(frozen: dict[str, Json], output: Path) -> None:
    """Hand R9 the frozen profile seeds and sole-owner physical form expansion."""
    profiles: dict[str, Json] = {}
    for raw in array_json(frozen["requirements"]):
        row = object_json(raw)
        profile = {
            field: row[field]
            for field in (
                "method",
                "input_types",
                "input_domains",
                "domain",
                "key_profile",
                "time_profile",
                "source_precision",
                "numeric_policy",
                "precision_contract",
                "state_version",
                "required_parts",
            )
        }
        identity = hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        profiles[identity] = profile
    forms: dict[str, Json] = {
        "duckdb": ["table", "view", "file", "HTTP-JSON"],
        "postgres": ["table", "view", "namespace"],
        "mysql": ["table", "view"],
        "sqlite": ["main-table", "main-view"],
        "trino": ["Iceberg", "non-Iceberg"],
        "clickhouse": ["MergeTree", "Distributed"],
    }
    payload: dict[str, Json] = {
        "kind": "static_required_target_handoff",
        "owner": "docs/superpowers/specs/2026-09-26-marivo-full-refactor-r0-sql-ledger.md#4",
        "profiles": profiles,
        "backend_forms": forms,
        "routes": ["ibis_python", "artifact_python"],
        "status": "unverified",
        "obligations": [
            "actual issued Ibis and driver submission",
            "complete input and exact keys/types/time",
            "independent oracle and required parts",
            "fixed/cold and poisoned source",
            "cancel/close/deadline/atomic resources",
            "submit count and observed cost",
        ],
        "disposition": "Every profile seed requires provider-specific form and route qualification; local acceptance remains separately attached to original IDs. Parsed-time and other provider-specific extensions stay with the referenced owner. Native route attempts cannot replace the mandatory preselected preparation route.",
    }
    (output / "r9-targets.json.gz").write_bytes(
        gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    )


def build(root: Path, evidence: Path, output: Path) -> dict[str, Json]:
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    rows = [
        object_json(r)
        for r in array_json(frozen["requirements"])
        if object_json(r)["responsibility"] == "R8.6"
    ]
    assert len(rows) == len({r["id"] for r in rows}) == 492
    proofs = source_proofs(evidence)
    installed = evidence / "installed-wheel"
    if (installed / "commands.json").exists():
        commands = [object_json(r) for r in json.loads((installed / "commands.json").read_text())]
        required = {
            "install",
            "dependencies",
            "dependency-check",
            "origin",
            "poisoned-pythonpath",
            "surface",
            "r8-public",
            "a04-table-recover",
            "a04-parquet-recover",
            "console-help",
            "module-help",
        }
        names = {r["name"] for r in commands}
        if (
            required <= names
            and all(
                r["exit_code"] != 0 if r["name"] == "poisoned-pythonpath" else r["exit_code"] == 0
                for r in commands
            )
            and all(
                r["log_sha256"]
                == hashlib.sha256((installed / (str(r["name"]) + ".log")).read_bytes()).hexdigest()
                for r in commands
            )
        ):
            archive = read_json(installed / "archives.json")
            package = {
                str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (root / "marivo").rglob("*")
                if path.is_file() and (path.suffix == ".py" or path.name == "SKILL.md")
            }
            assert archive["files"] == package
            wheels = tuple((root / "dist/pypi").glob("marivo-*.whl"))
            assert len(wheels) <= 1
            if wheels:
                assert archive["wheel_sha256"] == hashlib.sha256(wheels[0].read_bytes()).hexdigest()
            for row in _proofs(installed):
                if row["kernel_proof_class"] == "fresh_process_source_offline_kernel":
                    proofs.append(
                        {
                            **row,
                            "installed": True,
                            "check": {
                                "name": "same-wheel",
                                "exit_code": 0,
                                "commands_sha256": hashlib.sha256(
                                    (installed / "commands.json").read_bytes()
                                ).hexdigest(),
                            },
                            "scenarios": [
                                "every_new_result_cold",
                                "site_packages_origin",
                                "poisoned_pythonpath",
                                "wheel_hash_dependencies",
                            ],
                        }
                    )
    requirements: list[Json] = []
    for row in rows:
        matches = [p for p in proofs if match(row, p)]
        blocks = [p for p in proofs if p.get("outcome") == "blocked" and matches_authority(row, p)]
        requirements.append(
            {
                "id": row["id"],
                "frozen_requirement": row,
                "status": "passed"
                if matches
                else "blocked"
                if row["status"] == "blocked" or blocks
                else "unverified",
                "proof": matches[0] if matches else blocks[0] if blocks else None,
                "disposition": "exact original scenario, method/type/domain/origin/route/parts and process authority"
                if matches
                else "Exact original composition blocked: " + str(blocks[0]["blocking_error"])
                if blocks
                else "No exact evidence attached; aggregate tests, installed transport and predecessor results do not close this obligation.",
            }
        )
    counts = dict(Counter(str(object_json(r)["status"]) for r in requirements))
    summary: dict[str, Json] = {
        "version": "r86-acceptance/v1",
        "mandatory": 492,
        "counts": {key: int(value) for key, value in counts.items()},
        "r86_complete": counts.get("passed", 0) == 492,
        "snapshot_sha256": hashlib.sha256((root / SNAPSHOT).read_bytes()).hexdigest(),
        "attachments": {
            str(p.relative_to(evidence)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(evidence.rglob("*"))
            if p.is_file() and not p.name.startswith("ledger")
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    summary["handoff"] = handoff(root, frozen, requirements, output)
    backend_targets(frozen, output)
    payload = {**summary, "requirements": requirements}
    (output / "requirements.json.gz").write_bytes(
        gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    )
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    package_evidence(evidence, output)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if args.restore:
        restore_evidence(args.restore, args.evidence)
    summary = build(Path.cwd(), args.evidence, args.output)
    print(
        json.dumps(
            {key: summary[key] for key in ("mandatory", "counts", "r86_complete")}, sort_keys=True
        )
    )
