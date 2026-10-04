"""Bind R8.5 evidence to unchanged R8.1 IDs without aggregate qualification."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
from collections import Counter
from functools import cache
from pathlib import Path

from scripts.r81_static_freeze import (
    SNAPSHOT,
    Json,
    array_json,
    decode_payload,
    object_json,
    read_json,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, payload: dict[str, Json]) -> None:
    path.write_bytes(
        gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    )


@cache
def definitions(root: Path, path: str) -> dict[str, str]:
    """Hash definitions in their actual scopes, including conditional definitions."""
    source = root / path
    if not source.exists():
        return {}
    text = source.read_text()
    result: dict[str, str] = {}

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        nested = scope
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = ".".join((*scope, node.name))
            body = ast.get_source_segment(text, node)
            assert body is not None
            result[name] = hashlib.sha256(body.encode()).hexdigest()
            nested = (*scope, node.name)
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(ast.parse(text), ())
    return result


def definition(root: Path, path: str, name: str) -> str | None:
    return definitions(root, path).get(name)


def matches(row: dict[str, Json], proof: dict[str, Json]) -> bool:
    """Require exact physical/input identity and original parts, never just a method."""
    return all(
        row.get(k) == proof.get(k)
        for k in (
            "method",
            "qualification_key",
            "implementation_id",
            "precision_contract",
            "key_profile",
            "origin_profile",
            "time_profile",
            "numeric_policy",
            "implementation_contract_version",
            "state_version",
        )
    ) and set(array_json(row["required_parts"])) <= set(array_json(proof["retained_parts"]))


VIEW_NODE = "tests/test_analysis_views_r85.py::test_frozen_views_scope_sharing_quantity_and_k"
AUDIT = "docs/superpowers/specs/2026-10-04-marivo-r85-evidence/test-transfer-audit.json.gz"


def covered(node: str, checks: dict[str, Json]) -> list[Json]:
    """Bind exact passed nodes or an explicitly executed whole test-file scope."""
    result: list[Json] = []
    for key, raw in checks.items():
        check = object_json(raw)
        if check["exit_code"] != 0:
            continue
        if node in array_json(check.get("passed_nodes", [])) or any(
            node == scope or node.startswith(str(scope) + "::")
            for scope in array_json(check.get("test_scopes", []))
        ):
            result.append(
                {
                    "check_id": key,
                    **{k: check[k] for k in ("command", "exit_code", "log", "log_sha256")},
                }
            )
    return result


def build(root: Path, proofs_dir: Path, checks_file: Path, output: Path) -> dict[str, Json]:
    frozen = decode_payload(root, read_json(root / SNAPSHOT))
    rows = [
        object_json(r)
        for r in array_json(frozen["requirements"])
        if object_json(r).get("responsibility") == "R8.5"
    ]
    assert len(rows) == 171 and len({r["id"] for r in rows}) == 171
    checks = read_json(checks_file)
    audit_payload = object_json(json.loads(gzip.decompress((root / AUDIT).read_bytes())))
    audits = {object_json(r)["node"]: object_json(r) for r in array_json(audit_payload["records"])}
    assert len(audits) == 209
    proofs = [read_json(p) for p in sorted(proofs_dir.glob("*.json"))]
    output.mkdir(parents=True, exist_ok=True)
    write(output / "public-views.json.gz", {"proofs": list[Json](proofs)})
    evidence_sha = sha(output / "public-views.json.gz")
    attached: list[Json] = []
    for row in rows:
        candidates = [p for p in proofs if matches(row, p)]
        assert len(candidates) <= 1
        keys = (
            ("views",) if row["V"] == "V15" else ("views", "disclosure", "daily", "site", "typing")
        )
        checked = [object_json(checks[k]) for k in keys]
        accepted = len(candidates) == 1 and all(c["exit_code"] == 0 for c in checked)
        attached.append(
            {
                **row,
                "frozen_requirement": row,
                "status": "passed"
                if accepted
                else "blocked"
                if row["status"] == "blocked"
                else "unverified",
                "execution_command": [c["command"] for c in checked] if accepted else None,
                "exit_code": 0 if accepted else None,
                "attachment_sha256": evidence_sha if accepted else None,
                "artifact": candidates[0]["artifact"] if accepted else None,
                "execution_oracle": candidates[0]["oracle"] if accepted else None,
                "test_owner": VIEW_NODE
                if row["V"] == "V15"
                else "tests/test_analysis_disclosure_r85.py + tests/test_analysis_retirement_r85.py + independent public/disclosure snapshots + "
                + VIEW_NODE,
                "assertion_transfer": "exact frozen physical profile and scenario-specific assertions"
                if accepted
                else "no exact passing evidence attached",
            }
        )
    counts: dict[str, Json] = {
        k: Counter(str(object_json(r)["status"]) for r in attached)[k]
        for k in ("passed", "failed", "blocked", "skipped", "unverified")
    }
    write(
        output / "requirements.json.gz",
        {
            "version": "r85-requirements/v1",
            "snapshot_sha256": sha(root / SNAPSHOT),
            "mandatory": 171,
            "counts": counts,
            "checks": checks,
            "requirements": attached,
        },
    )
    symbols: list[Json] = []
    for raw in array_json(frozen["symbol_dispositions"]):
        record = object_json(raw)
        path, symbol = record["path"], record["symbol"]
        assert isinstance(path, str) and isinstance(symbol, str)
        current_path, current_symbol = path, symbol
        if path == "marivo/analysis/operators/forecast_contracts.py" and symbol.split(".")[0] in (
            "ForecastHorizon",
            "ForecastModel",
            "periods",
            "naive",
            "drift",
            "seasonal_naive",
        ):
            current_path = "marivo/analysis/forecast_models.py"
        if path == "marivo/analysis/public_dsl.py" and symbol in (
            "LogicalNumericRelation.correlate",
            "MaterializedNumericRelation.correlate",
        ):
            current_symbol = "_NumericComparison.correlate"
        current = definition(root, current_path, current_symbol)
        symbols.append(
            {
                **record,
                "current_body_sha256": current,
                "physical_deletion": "definition_removed"
                if current is None
                else "retained_definition",
                "current_disposition": "removed"
                if current is None
                else "shared_owner_retained_or_updated",
                "remaining_shared_owner": current_path + "::" + current_symbol
                if current is not None
                else None,
                "replacement_owner": "typed graph/method registry/public results"
                if current is None
                else current_path + "::" + current_symbol,
                "dynamic_unreachability": "passed_by_reverse_guards"
                if current is None and object_json(checks["disclosure"])["exit_code"] == 0
                else "not_a_retired_entry"
                if current is not None
                else "unverified",
                "replacement_execution": "bounded public profile evidence; earlier qualification unchanged",
            }
        )
    tests: list[Json] = []
    for raw in array_json(frozen["legacy_test_dispositions"]):
        record = object_json(raw)
        node = record["node"]
        assert isinstance(node, str)
        path, name = node.split("::", 1)
        current = definition(root, path, name.replace("::", "."))
        audit = audits.get(node)
        owners: list[Json] = (
            [node]
            if current is not None
            else array_json(audit["current_test_owners"])
            if audit
            else []
        )
        owner_proofs: list[Json] = []
        for raw_owner in owners:
            assert isinstance(raw_owner, str)
            owner_path, owner_name = raw_owner.split("::", 1)
            digest = definition(root, owner_path, owner_name.replace("::", "."))
            evidence = covered(raw_owner, checks)
            owner_proofs.append({"node": raw_owner, "body_sha256": digest, "checks": evidence})
        verified = bool(owner_proofs) and all(
            object_json(p)["body_sha256"] is not None and object_json(p)["checks"]
            for p in owner_proofs
        )
        if audit:
            assert audit["original_body_sha256"] == record["body_sha256"]
            assert [object_json(a)["original"] for a in array_json(audit["assertions"])] == record[
                "original_assertions"
            ]
            verified = verified and all(
                object_json(a)["current_owners"] == owners for a in array_json(audit["assertions"])
            )
        state = (
            "retained_shared_test"
            if current is not None
            else audit["current_disposition"]
            if audit and verified
            else "unverified_assertion_transfer"
        )
        tests.append(
            {
                **record,
                "current_test_owners": owners,
                "current_body_sha256": current,
                "owner_proofs": owner_proofs,
                "assertion_audit": audit,
                "current_disposition": state,
                "physical_deletion": "old_harness_removed"
                if current is None
                else "shared_test_retained",
                "original_assertion_authority": "R8.1 body SHA and assertion summary; original source at baseline f0b1c5930d",
                "validation_evidence": [
                    c for p in owner_proofs for c in array_json(object_json(p)["checks"])
                ],
                "validation_status": "unverified"
                if not verified
                else "bounded; consult individual command/marker, no remote or prior-phase qualification",
                "remaining_work": "Audit every original raw-fact assertion against the mapped current owners; routing is not proof"
                if state == "unverified_assertion_transfer"
                else None,
            }
        )
    assert len(symbols) == 449 and len(tests) == 391
    write(
        output / "retirement-records.json.gz",
        {
            "version": "r85-retirement/v1",
            "snapshot_sha256": sha(root / SNAPSHOT),
            "symbols": symbols,
            "legacy_tests": tests,
        },
    )
    unverified = sum(
        object_json(t)["current_disposition"] == "unverified_assertion_transfer" for t in tests
    )
    gates: dict[str, Json] = {}
    checks_passed = all(object_json(c)["exit_code"] == 0 for c in checks.values())
    for index in range(1, 19):
        migration = f"M{index:02d}"
        owned = [
            object_json(s)
            for s in symbols
            if migration in array_json(object_json(s)["migration_ids"])
        ]
        gates[migration] = {
            "status": "closed" if checks_passed and unverified == 0 else "pending",
            "symbol_records": len(owned),
            "removed_definitions": sum(s["current_body_sha256"] is None for s in owned),
            "remaining_actual_owners": [
                s["remaining_shared_owner"] for s in owned if s["current_body_sha256"] is not None
            ],
            "test_records": 391 if migration == "M18" else 0,
            "test_transfer_unverified": unverified if migration == "M18" else 0,
            "evidence": list[Json](checks),
            "authority": "original R8.1 disposition plus actual current definition/assertion owner; no prior-phase qualification",
        }
    summary: dict[str, Json] = {
        "version": "r85-index/v1",
        "baseline_head": "f0b1c5930d993338f94a4c415458729f6d67953f",
        "mandatory": 171,
        "counts": counts,
        "symbol_records": 449,
        "test_records": 391,
        "unverified_test_transfers": unverified,
        "unexecuted_retained_test_nodes": sum(
            object_json(t)["current_disposition"] == "retained_shared_test"
            and object_json(t)["validation_status"] == "unverified"
            for t in tests
        ),
        "r85_complete": counts["passed"] == 171 and unverified == 0 and checks_passed,
        "migration_gates": gates,
        "files": {
            p.relative_to(output).as_posix(): sha(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p != output / "index.json"
        },
        "prior_qualification": "R8.2-R8.4 unchanged; no R8.6/R9/R10 qualification",
    }
    (output / "index.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("proofs", type=Path)
    parser.add_argument("checks", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(Path.cwd(), args.proofs, args.checks, args.output)))
