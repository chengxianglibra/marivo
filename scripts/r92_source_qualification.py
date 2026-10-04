"""Run explicit R9.2 tests and bind immutable per-scenario evidence (no service lifecycle)."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from scripts import r9_qualification_requirements as freeze

ROOT = freeze.ROOT
OUTPUT = ROOT / freeze.SPECS / "2026-10-04-marivo-r92-evidence"
TEST = "tests/test_r92_source_profiles.py"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write(path: Path, value: freeze.Json) -> None:
    path.write_bytes(freeze.encode(value) + b"\n")


def run(directory: Path, label: str, selection: str) -> int:
    target = directory / label
    target.mkdir(parents=True, exist_ok=False)
    candidate = freeze.candidate()
    command = [
        str(ROOT / ".venv/bin/pytest"),
        "-q",
        "--tb=short",
        "-n",
        "0",
        "-m",
        "runtime",
        TEST,
        "-k",
        selection,
        f"--junitxml={target / 'junit.xml'}",
    ]
    environment = dict(os.environ)
    environment["MARIVO_R92_EVIDENCE_DIR"] = str(target)
    started = now()
    with (target / "test.log").open("wb") as log:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False
        )
    if freeze.candidate()["content_sha256"] != candidate["content_sha256"]:
        raise ValueError(
            "Candidate changed during execution; preserve this run without qualification"
        )
    drivers: dict[str, freeze.Json] = {}
    for name in (
        "psycopg",
        "mysqlclient",
        "trino",
        "clickhouse-connect",
        "ibis-framework",
        "duckdb",
        "pyarrow",
    ):
        try:
            drivers[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            drivers[name] = "not-installed"
    write(
        target / "run.json",
        {
            "candidate": candidate,
            "command": freeze.checked(command),
            "started": started,
            "finished": now(),
            "exit_code": result.returncode,
            "environment": {
                "platform": platform.platform(),
                "python": sys.version,
                "sqlite": sqlite3.sqlite_version,
                "drivers": drivers,
                "opt_in": {
                    key: environment.get(key, "0")
                    for key in (
                        *[
                            f"MARIVO_{backend}_ANALYSIS_TEST"
                            for backend in ("POSTGRES", "MYSQL", "TRINO", "CLICKHOUSE")
                        ],
                        "MARIVO_CLICKHOUSE_CLUSTER_TEST",
                    )
                },
            },
            "attachments": {
                file.name: freeze.digest(file.read_bytes())
                for file in sorted(target.iterdir())
                if file.is_file()
            },
        },
    )
    print((target / "test.log").read_text())
    return result.returncode


def finalize(directory: Path, labels: list[str]) -> dict[str, freeze.Json]:
    if (directory / "results-index.json").exists():
        raise ValueError("Results already exist; preserve them and use a new directory")
    if (directory / "freeze/index.json").exists():
        payload = freeze.load(directory / "freeze")
        freeze.validate(payload)
    else:
        payload = freeze.build()
        freeze.save(payload, directory / "freeze")
    authority = freeze.obj(payload["candidate"])["content_sha256"]
    rows = {
        str(freeze.obj(row)["id"]): freeze.obj(row) for row in freeze.arr(payload["requirements"])
    }
    overrides: dict[str, freeze.Json] = {}
    for label in labels:
        target = directory / label
        run_record = freeze.read(target / "run.json")
        if (
            freeze.obj(run_record["candidate"])["content_sha256"] != authority
            or run_record["exit_code"] != 0
        ):
            raise ValueError("Incomplete or stale run cannot qualify the final candidate")
        for file, digest in freeze.obj(run_record["attachments"]).items():
            if freeze.digest((target / file).read_bytes()) != digest:
                raise ValueError("Run attachment changed")
        successful = {
            node.attrib["name"]
            for node in ElementTree.parse(target / "junit.xml").iter("testcase")
            if not any(node.find(kind) is not None for kind in ("failure", "error", "skipped"))
        }
        for attachment in sorted(target.glob("profile-*.json")) + sorted(
            target.glob("risk-*.json")
        ):
            evidence = freeze.read(attachment)
            backend = str(evidence["backend"])
            profile = evidence.get("profile")
            risk = evidence.get("risk")
            function = "test_source_profile" if profile is not None else "test_source_type_risk"
            node = (
                f"{function}[{backend}-{profile}]"
                if profile is not None
                else f"{function}[{risk}-{backend}]"
            )
            if node not in successful:
                raise ValueError("Receipt does not belong to a passed test")
            identities = [
                identity
                for identity, row in rows.items()
                if row["gap_owner"] == "R9.2"
                and row["backend"] == backend
                and (
                    (
                        profile is not None
                        and row["family"] == "source-profile"
                        and row["profile"] == profile
                    )
                    or (
                        risk is not None
                        and row["family"] == "source-types"
                        and row["scenario"] == risk
                    )
                )
            ]
            if len(identities) != 1 or identities[0] in overrides:
                raise ValueError("Missing or duplicate scenario binding")
            identity = identities[0]
            blocker = evidence.get("blocker")
            if blocker:
                overrides[identity] = {
                    "status": "blocked",
                    "reason": blocker,
                    "owner": "R9.2",
                    "release_condition": "Provide an exact physical Decimal carrier without float coercion, then rerun this scenario",
                    "observed_test_node": f"{TEST}::{node}",
                    "attachment": f"{label}/{attachment.name}",
                }
                continue
            overrides[identity] = {
                "status": "passed",
                "proof_class": "runtime",
                "expectation": rows[identity]["expectation"],
                "exit_code": 0,
                "owner_sha256": payload["owners"],
                "environment": {
                    "host": run_record["environment"],
                    "service": evidence["environment"],
                },
                "input_sha256": freeze.digest(freeze.encode(evidence["oracle"])),
                "oracle": evidence["oracle"],
                "command": run_record["command"],
                "started": run_record["started"],
                "finished": run_record["finished"],
                "test_node": f"{TEST}::{node}",
                "proofs": {
                    str(proof): {
                        "status": "passed",
                        "requirement_id": identity,
                        "candidate_sha256": authority,
                        "attachment": f"{label}/{attachment.name}",
                        "sha256": freeze.digest(attachment.read_bytes()),
                    }
                    for proof in freeze.arr(rows[identity]["required_proofs"])
                },
                "boundaries": "Tested physical key/profile only; fault injection proves local cleanup, remote termination is not granted",
            }
    expected = {identity for identity, row in rows.items() if row["gap_owner"] == "R9.2"}
    if set(overrides) != expected:
        raise ValueError(f"Missing R9.2 bindings: {sorted(expected - set(overrides))}")
    path = directory / "results-index.json"
    write(path, {"candidate_sha256": authority, "overrides": overrides})
    counts = freeze.verify_results(payload, path)
    write(
        directory / "summary.json",
        {
            "counts": counts,
            "r92_required": len(expected),
            "selected_runs": freeze.checked(labels),
            "original_ids_sha256": freeze.digest(freeze.encode(freeze.checked(sorted(rows)))),
            "candidate_sha256": authority,
        },
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "finalize"))
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    parser.add_argument("--label")
    parser.add_argument("--selection")
    parser.add_argument("--runs", nargs="+")
    args = parser.parse_args()
    if args.action == "run":
        if not args.label or not args.selection or Path(args.label).name != args.label:
            parser.error("run requires a simple --label and --selection")
        raise SystemExit(run(args.directory, args.label, args.selection))
    if not args.runs:
        parser.error("finalize requires --runs")
    print(finalize(args.directory, args.runs))


if __name__ == "__main__":
    main()
