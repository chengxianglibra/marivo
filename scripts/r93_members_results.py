"""Bind C03 representative source scenarios with independent resource proofs."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import copytree

from scripts import r9_qualification_requirements as freeze
from scripts.r93_evidence_transport import read_attachment, verify
from scripts.r93_retained_results import save_result
from scripts.r93_source_results import resource_proofs

BACKENDS = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
KINDS = ("snapshot", "validity", "duplicate", "overlap", "null_id", "null_tenant")
MODES = ("complete", "missing", "multiple")
PAIRS: list[freeze.Json] = [
    [9007199254740992, "a"],
    [9007199254740993, "a"],
    [9007199254740993, "b"],
]


def build(trino: Path, clickhouse: Path) -> tuple[dict[str, freeze.Json], dict[str, bytes]]:
    """Refuse incomplete or inconsistent evidence before writing any result."""
    directories = (trino, clickhouse)
    runs = [freeze.read(directory / "run.json") for directory in directories]
    payload = freeze.load(trino / "freeze")
    authority = freeze.obj(payload["candidate"])["content_sha256"]
    attachments: dict[str, freeze.Json] = {}
    origins: dict[str, Path] = {}
    tests: list[ET.Element] = []
    for directory, run in zip(directories, runs, strict=True):
        verify(directory)
        if (
            run["exit_code"] != 0
            or run["candidate_sha256"] != authority
            or freeze.load(directory / "freeze") != payload
        ):
            raise ValueError("Successful identical candidate executions required")
        for name, digest in freeze.obj(run["attachments"]).items():
            if not name.startswith(("c03-", "deadline-", "mysql-graph-")):
                continue
            if name in attachments:
                raise ValueError("Duplicate source or cancellation receipt")
            attachments[name], origins[name] = digest, directory
        tests.extend(ET.fromstring(read_attachment(directory, "junit.xml")).findall(".//testcase"))

    def document(name: str) -> dict[str, freeze.Json]:
        if name not in origins:
            raise ValueError("Required evidence attachment missing")
        raw = read_attachment(origins[name], name)
        if freeze.digest(raw) != attachments[name]:
            raise ValueError("Source receipt differs from execution manifest")
        return freeze.obj(freeze.checked(json.loads(raw)))

    def invocation(module: str, name: str) -> None:
        selected = [
            item
            for item in tests
            if item.attrib.get("classname") == "tests." + module and item.attrib["name"] == name
        ]
        if len(selected) != 1:
            raise ValueError("Required source or cancellation invocation not unique")
        if list(selected[0]):
            raise ValueError("Required source or cancellation invocation not passed")

    cancellation, supporting = resource_proofs(attachments, document, invocation)
    if not set(BACKENDS) - {"mysql"} <= set(cancellation) <= set(BACKENDS):
        raise ValueError("Required independent native cancellation profiles missing or unknown")
    overrides: dict[str, freeze.Json] = {}
    witnesses: dict[str, bytes] = {}
    rows = {
        str(freeze.obj(row)["backend"]): freeze.obj(row)
        for row in freeze.arr(payload["requirements"])
        if freeze.obj(row)["family"] == "C03.a/b/c" and freeze.obj(row)["gap_owner"] == "R9.3"
    }
    if set(rows) != set(BACKENDS):
        raise ValueError("Frozen six-backend C03 denominator required")
    for backend in BACKENDS:
        records: list[freeze.Json] = []
        for category, scenarios, function in (
            ("members", KINDS, "test_c03_complete_versioned_members"),
            ("path", MODES, "test_c03_scalar_path_requires_scoped_complete_keys"),
        ):
            for scenario in scenarios:
                invocation("test_r93_members_consumers", f"{function}[{scenario}-{backend}]")
                name = f"c03-{category}-{scenario}-{backend}.json"
                receipt = document(name)
                environment = freeze.obj(receipt["environment"])
                if environment["backend"] != backend or not freeze.obj(receipt["input"]):
                    raise ValueError("Source environment and synthetic inputs required")
                profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend)
                if profile is not None and environment.get("profile") != profile:
                    raise ValueError("Source and cancellation profiles differ")
                native = freeze.arr(receipt["actual_native_submissions"])
                submissions = [freeze.obj(item) for item in freeze.arr(receipt["submissions"])]
                if (
                    not native
                    or not submissions
                    or any(
                        item["state"] != "succeeded"
                        or item["connection_disconnected"] is not True
                        or item["cursor_state"] not in ("closed", "connection_owned")
                        or not item["sql"]
                        or item["sql"] not in native
                        for item in submissions
                    )
                    or receipt["connection_disconnected"] is not True
                ):
                    raise ValueError("Actual successful submissions and owner release required")
                oracle = freeze.obj(receipt["oracle"])
                if category == "members" and scenario not in ("snapshot", "validity"):
                    expected: dict[str, freeze.Json] = {
                        "rejected_invalid_identity": True,
                        "published_parquet": 0,
                        "resources": 0,
                    }
                elif category == "members":
                    expected = {
                        "pairs": PAIRS,
                        "numeric": [10, 20, 30],
                        "resources": 0,
                        "boolean_int8_rejected": backend == "mysql",
                        "before_end_count": 0 if scenario == "snapshot" else 3,
                        "first_period_before_end_count": 3,
                        "later_period_count": 1 if scenario == "snapshot" else 3,
                        "missing_period_count": 0 if scenario == "snapshot" else 3,
                    }
                else:
                    expected = {
                        "mode": scenario,
                        "resources": 0,
                        "numeric": [30] if scenario == "missing" else [10],
                        "scoped_rows": 1 if scenario == "missing" else 2,
                        "null_pairs": [] if scenario == "missing" else [[9007199254740993, "a"]],
                        "rejected_invalid_full_domain": scenario != "complete",
                    }
                if oracle != expected:
                    raise ValueError("Independent complete-key and version oracle differs")
                keys = [freeze.obj(item) for item in freeze.arr(receipt["physical_keys"])]
                for key in keys:
                    shape = freeze.obj(key["shape"])
                    if (
                        key["route"] != "ibis"
                        or key["input_domains"] != ["entity"]
                        or shape["kind"] != "SourceShape"
                        or shape["backend"] != backend
                        or shape["form"] != "table"
                        or shape["table_kind"] != "native"
                        or freeze.obj(shape["time"])["kind"] != "NoTime"
                    ):
                        raise ValueError("Exact native C03 physical key required")
                if category == "path" or scenario in ("snapshot", "validity"):
                    pairs = {
                        (str(key["method"]), tuple(map(str, freeze.arr(key["input_types"]))))
                        for key in keys
                    }
                    required = {("parts_transport@v1", ("int64",)), ("bind_project@v1", ("int64",))}
                    if category == "members":
                        required |= {("parts_transport@v1", (kind,)) for kind in ("string", "date")}
                        if backend != "mysql":
                            required.add(("parts_transport@v1", ("boolean",)))
                        if scenario == "snapshot":
                            required.add(("time.product@v1", ("int64",)))
                    if not required <= pairs:
                        raise ValueError("Missing actual selected C03 consumer key")
                    retained = [
                        freeze.obj(item) for item in freeze.arr(receipt.get("retained", []))
                    ]
                    if not retained or any(
                        item["verified"] is not True
                        or not freeze.arr(item["schema"])
                        or "subject"
                        not in {freeze.obj(part)["role"] for part in freeze.arr(item["parts"])}
                        for item in retained
                    ):
                        raise ValueError("Verified retained schemas and subject parts required")
                    for item in retained:
                        schema: dict[str, str] = {}
                        for column in freeze.arr(item["schema"]):
                            fields = freeze.arr(column)
                            if len(fields) != 2:
                                raise ValueError("Closed Arrow field pair required")
                            schema[str(fields[0])] = str(fields[1])
                        expected_keys: list[freeze.Json] = [["key_0", "int64"], ["key_1", "string"]]
                        if item["kind"] == "MaterializedTimeAnalysisDomain":
                            expected_keys.append(["key_2", "string"])
                        if any(
                            schema.get(str(freeze.arr(pair)[0])) != freeze.arr(pair)[1]
                            for pair in expected_keys
                        ):
                            raise ValueError("Complete native composite primary schema required")
                        for part in freeze.arr(item["parts"]):
                            if (
                                freeze.obj(part)["role"] == "subject"
                                and freeze.obj(part)["key_fields"] != expected_keys
                            ):
                                raise ValueError("Complete subject-part key types required")
                        value_types = {
                            "MaterializedNumericRelation": "int64",
                            "MaterializedCategoryRelation": "string",
                            "MaterializedBooleanRelation": "bool",
                            "MaterializedTemporalRelation": "date32[day]",
                        }
                        kind = str(item["kind"])
                        if kind in value_types and (
                            schema.get("value") != value_types[kind]
                            or schema.get("cell_tag") != "string"
                            or schema.get("cell_reason") != "string"
                        ):
                            raise ValueError("Exact typed Cell schema required")
                    if category == "members":
                        kinds = {item["kind"] for item in retained}
                        required_kinds = {
                            "MaterializedNumericRelation",
                            "MaterializedCategoryRelation",
                            "MaterializedTemporalRelation",
                            "MaterializedAnalysisDomain",
                        }
                        if backend != "mysql":
                            required_kinds.add("MaterializedBooleanRelation")
                        if scenario == "snapshot":
                            required_kinds.add("MaterializedTimeAnalysisDomain")
                        if not required_kinds <= kinds:
                            raise ValueError("Four typed reads and complete domain required")
                records.append(
                    {
                        "attachment": name,
                        "sha256": attachments[name],
                        "oracle": oracle,
                        "environment": environment,
                        "input": receipt["input"],
                    }
                )
        row = rows[backend]
        identity = str(row["id"])
        if backend == "mysql":
            resource_gap = "" if backend in cancellation else " and native cancellation"
            overrides[identity] = {
                "status": "blocked",
                "owner": "R9.3",
                "reason": "Native BOOLEAN is exposed as int8; four-kind success"
                + resource_gap
                + " is unproven",
                "release_condition": "Prove a contract-valid Boolean route"
                + ("" if backend in cancellation else " and same-candidate native cancellation"),
            }
            continue
        if set(map(str, freeze.arr(row["required_proofs"]))) != {
            "submission",
            "numeric_state",
            "type_domain_parts",
            "resource_cancel",
        }:
            raise ValueError("Unexpected C03 proof obligation")
        resource_names = [cancellation[backend]] + [
            str(freeze.obj(item)["attachment"]) for item in supporting
        ]
        if backend in ("trino", "clickhouse"):
            resource_names += [
                name
                for name in attachments
                if name.startswith(f"deadline-native-{backend}-") and name != cancellation[backend]
            ]
        witness_name = f"c03-binding-{backend}.json"
        witness = freeze.encode(
            {
                "requirement_id": identity,
                "candidate_sha256": authority,
                "scenarios": records,
                "resource_proofs": [
                    {"attachment": name, "sha256": attachments[name], "observed": document(name)}
                    for name in resource_names
                ],
            }
        )
        witnesses[witness_name] = witness
        run = runs[1 if backend == "clickhouse" else 0]
        overrides[identity] = {
            "status": "passed",
            "proof_class": "runtime",
            "expectation": row["expectation"],
            "exit_code": 0,
            "skipped": False,
            "owner_sha256": payload["owners"],
            "environment": {
                "candidate_dependencies": freeze.obj(payload["candidate"])["dependencies"],
                "source": freeze.obj(records[0])["environment"],
            },
            "input_sha256": freeze.digest(
                freeze.encode([freeze.obj(item)["input"] for item in records])
            ),
            "oracle": [freeze.obj(item)["oracle"] for item in records],
            **{field: run[field] for field in ("command", "started", "finished")},
            "test_node": f"tests/test_r93_members_consumers.py::test_c03_complete_versioned_members[snapshot-{backend}]",
            "proofs": {
                str(obligation): {
                    "status": "passed",
                    "requirement_id": identity,
                    "candidate_sha256": authority,
                    "attachment": witness_name,
                    "sha256": freeze.digest(witness),
                }
                for obligation in freeze.arr(row["required_proofs"])
            },
        }
    return {
        "candidate_sha256": authority,
        "default": "unverified",
        "overrides": overrides,
    }, witnesses


def bind(trino: Path, clickhouse: Path, output: Path) -> dict[str, freeze.Json]:
    result, witnesses = build(trino, clickhouse)
    output.mkdir(parents=True, exist_ok=False)
    copytree(trino / "freeze", output / "freeze")
    for name, content in witnesses.items():
        (output / name).write_bytes(content)
    copytree(trino, output / "sources/trino")
    copytree(clickhouse, output / "sources/clickhouse")
    return save_result(output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trino", type=Path)
    parser.add_argument("clickhouse", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(freeze.encode(bind(args.trino, args.clickhouse, args.output)).decode())
