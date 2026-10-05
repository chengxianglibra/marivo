"""Bind C04/C05 representatives only with same-candidate native proofs."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from shutil import copytree
from typing import Literal

from scripts import r9_qualification_requirements as freeze
from scripts.r93_evidence_transport import read_attachment, verify
from scripts.r93_retained_results import save_result
from scripts.r93_source_results import resource_proofs

BACKENDS = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
MODES = ("aggregate", "slice", "linear_composite", "ratio", "weighted_mean")
MYSQL_RETRY_NODES = {
    (
        "tests.test_r93_capability_consumers",
        "test_c05_original_mean_preserves_component_weights[mysql-string]",
    ),
    (
        "tests.test_r93_source_deadline",
        "test_mysql_cancel_has_independent_server_termination_proof",
    ),
    *(
        (
            "tests.test_r93_mysql_cancellation",
            "test_mysql_graph_failure_preserves_original_artifact_and_control_obligation["
            + fault
            + "]",
        )
        for fault in ("cancel", "control_close")
    ),
}


def validate_mysql_retry(primary: Path, retry: Path) -> dict[str, freeze.Json]:
    """Admit only the four observed OOM failures repaired on an identical freeze."""
    verify(primary)
    verify(retry)
    first, second = freeze.read(primary / "run.json"), freeze.read(retry / "run.json")
    if (
        first["exit_code"] != 1
        or second["exit_code"] != 0
        or freeze.load(primary / "freeze") != freeze.load(retry / "freeze")
    ):
        raise ValueError("Failed original and successful identical-freeze MySQL repair required")
    original = ET.fromstring(read_attachment(primary, "junit.xml")).findall(".//testcase")
    repaired = ET.fromstring(read_attachment(retry, "junit.xml")).findall(".//testcase")
    failed: set[tuple[str, str]] = set()
    seen: set[tuple[str, str]] = set()
    for item in original:
        key = (item.attrib.get("classname", ""), item.attrib["name"])
        if key in seen:
            raise ValueError("Duplicate original invocation cannot be repaired")
        seen.add(key)
        if list(item):
            if len(item) != 1 or item[0].tag != "failure" or key not in MYSQL_RETRY_NODES:
                raise ValueError("Only the four native MySQL environment failures may be repaired")
            diagnostic = item[0].text or ""
            expected = (
                "Lost connection to server during query"
                if key[0] == "tests.test_r93_capability_consumers"
                else "Can't connect to server"
            )
            if expected not in diagnostic:
                raise ValueError("Native MySQL connection-loss diagnostic required")
            failed.add(key)
    if (
        failed != MYSQL_RETRY_NODES
        or len(repaired) != 4
        or {(item.attrib.get("classname", ""), item.attrib["name"]) for item in repaired}
        != MYSQL_RETRY_NODES
        or any(list(item) for item in repaired)
    ):
        raise ValueError("Exact four unique passed MySQL replacement invocations required")
    state = freeze.read(primary / "mysql-oom-state.json")
    if (
        state.get("OOMKilled") is not True
        or type(state.get("ExitCode")) is not int
        or state["ExitCode"] != 137
        or state.get("Status") != "exited"
        or not (
            datetime.fromisoformat(str(first["started"]))
            <= datetime.fromisoformat(str(state["FinishedAt"]))
            <= datetime.fromisoformat(str(first["finished"]))
        )
    ):
        raise ValueError("Independent in-batch MySQL OOM termination evidence required")
    return {
        "container_state": state,
        "container_state_sha256": freeze.digest((primary / "mysql-oom-state.json").read_bytes()),
        "failed_original_manifest_sha256": freeze.digest((primary / "run.json").read_bytes()),
        "successful_retry_manifest_sha256": freeze.digest((retry / "run.json").read_bytes()),
        "replaced_failed_nodes": [list(key) for key in sorted(failed)],
    }


def _native_receipt(backend: str, receipt: dict[str, freeze.Json]) -> None:
    environment = freeze.obj(receipt["environment"])
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    if environment.get("backend") != backend or environment.get("profile") != profile:
        raise ValueError("Exact native backend/profile required")
    native = freeze.arr(receipt["actual_native_submissions"])
    submissions = [freeze.obj(item) for item in freeze.arr(receipt["submissions"])]
    if not native or not submissions or receipt["connection_disconnected"] is not True:
        raise ValueError("Native execution and owner release required")
    if any(
        item.get("state") != "succeeded"
        or item.get("connection_disconnected") is not True
        or item.get("cursor_state") not in ("closed", "connection_owned")
        or not item.get("sql")
        or item["sql"] not in native
        for item in submissions
    ):
        raise ValueError("Successful actual native submissions required")


def validate_consumer(backend: str, mode: str, receipt: dict[str, freeze.Json]) -> None:
    """Check exact representative values, source routes and retained key types."""
    _native_receipt(backend, receipt)
    if not freeze.obj(receipt["input"]).get("roots"):
        raise ValueError("Independent source fact inputs required")
    oracle = freeze.obj(receipt["oracle"])
    if mode == "linear_composite":
        if oracle != {
            "a_1": 35,
            "a_2": 110,
            "b_1": 4,
            "complete_key": ["owner", "tenant"],
            "roots_reduced_before_combining": True,
        }:
            raise ValueError("Independent composite-key oracle differs")
    else:
        expected = {
            "aggregate": ("30", "Null(empty_contribution)"),
            "slice": ("5", "0.0"),
            "ratio": ("6.0", "0.0"),
            "weighted_mean": (str(50 / 3), "Null(empty_contribution)"),
        }
        if mode not in expected or (oracle.get("a"), oracle.get("b")) != expected[mode]:
            raise ValueError("Independent component oracle differs")
        if oracle.get("numeric_type") != "int64" or oracle.get("resources") != 0:
            raise ValueError("Exact representative facts and empty resource journal required")
        if mode == "ratio" and any(
            oracle.get(name) != value
            for name, value in {
                "roots_reduced_before_combining": True,
                "fixed_ratio_rollup": "10/3",
                "mean_of_target_ratios_rejected": True,
                "fixed_source_reads_forbidden": True,
                "exact_original_components": True,
                "empty_target": "Undefined(zero_denominator)",
            }.items()
        ):
            raise ValueError("Original ratio state and source-offline continuation required")
    keys = [freeze.obj(item) for item in freeze.arr(receipt["physical_keys"])]
    method = {
        "aggregate": "metric.observe@v1",
        "slice": "metric.sum_zero@v1",
        "linear_composite": "metric.linear@v1",
        "ratio": "metric.ratio@v1",
        "weighted_mean": "metric.weighted_mean@v1",
    }[mode]
    selected = [item for item in keys if item.get("method") == method]
    if not selected:
        raise ValueError("Actual selected C04 consumer key required")
    for key in selected:
        shape = freeze.obj(key["shape"])
        pair = mode in ("linear_composite", "ratio")
        if (
            key["route"] != "ibis"
            or shape.get("kind") != "SourceShape"
            or shape.get("backend") != backend
            or shape.get("form") != "table"
            or shape.get("table_kind") != "native"
            or key["input_domains"] != (["entity", "entity"] if pair else ["entity"])
            or key["input_types"] != (["int64", "int64"] if pair else ["string"])
            or shape.get("time") != {"kind": "instant", "unit": "us", "timezone": "UTC"}
        ):
            raise ValueError("Native Entity C04 key required")
    retained = [freeze.obj(item) for item in freeze.arr(receipt["retained"])]
    expected_keys: list[freeze.Json] = [["key_0", "string"]]
    if mode == "linear_composite":
        expected_keys.append(["key_1", "int64"])
    if not retained:
        raise ValueError("Verified retained result required")
    for item in retained:
        schema = {
            str(freeze.arr(pair)[0]): freeze.arr(pair)[1] for pair in freeze.arr(item["schema"])
        }
        parts = [freeze.obj(part) for part in freeze.arr(item["parts"])]
        if (
            item.get("verified") is not True
            or any(
                schema.get(str(freeze.arr(pair)[0])) != freeze.arr(pair)[1]
                for pair in expected_keys
            )
            or schema.get("value") != ("double" if mode in ("ratio", "weighted_mean") else "int64")
            or schema.get("cell_tag") != "string"
            or schema.get("cell_reason") != "string"
            or not {"subject", "original_state", "coverage"} <= {part["role"] for part in parts}
            or any(part["key_fields"] != expected_keys for part in parts)
        ):
            raise ValueError("Complete native Cell schema and original-state parts required")


def validate_grouping_consumer(backend: str, mode: str, receipt: dict[str, freeze.Json]) -> None:
    """Check C05 current-row, complete-coordinate and original-state evidence."""
    if mode not in ("int64", "float64", "mean-int64", "mean-string"):
        raise ValueError("Closed C05 numeric/identity mode required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    if not inputs.get("columns") or not inputs.get("values"):
        raise ValueError("Independent C05 fact inputs required")
    mean = mode.startswith("mean-")
    oracle = freeze.obj(receipt["oracle"])
    if mean:
        expected: dict[str, freeze.Json] = {
            "current": 50.5,
            "original": 200 / 101,
            "rows": 101,
            "original_components": {"a": [100, 100], "b": [100, 1]},
            "fixed_source_reads_forbidden": True,
            "no_additional_native_submission": True,
            "identity_type": mode.removeprefix("mean-"),
            "resources": 0,
        }
        methods = {"state_rollup.mean@v1", "row.mean@v1", "group.attach@v1"}
    else:
        if mode not in ("int64", "float64"):
            raise ValueError("Closed C05 numeric mode required")
        total = 6 if mode == "int64" else 6.5
        expected = {
            "counts": {"a": 2, "b": 0},
            "mean_a": 1.0,
            "mean_b_reason": "empty_mean",
            "original_a": 2,
            "original_full": total,
            "current_full_mean": total / 3,
            "complete_tuple_union": mode == "int64",
            "null_arithmetic_rejected_atomically": True,
            "count_including_null": 3,
            "count_defined": 2,
            "empty_count": 0,
            "empty_mean_reason": "empty_mean",
            "numeric_type": mode,
            "resources": 0,
        }
        methods = {
            "row.count@v1",
            "row.count_defined@v1",
            "row.sum@v1",
            "row.mean@v1",
            "state_rollup.sum_zero@v1",
            "group.attach@v1",
        }
    if oracle != expected:
        raise ValueError("Independent C05 state oracle differs")
    keys = [freeze.obj(item) for item in freeze.arr(receipt["physical_keys"])]
    if not methods <= {str(item["method"]) for item in keys}:
        raise ValueError("Actual selected C05 consumers required")
    numeric_type = "float64" if mean else mode
    if not any(
        key["method"] == "group.attach@v1"
        and key["input_types"] == [numeric_type, "string"]
        and key["input_domains"] == ["entity", "entity"]
        for key in keys
    ):
        raise ValueError("Exact C05 numeric/category attachment required")
    for key in keys:
        shape = freeze.obj(key["shape"])
        if (
            key["route"] != "ibis"
            or shape.get("kind") != "SourceShape"
            or shape.get("backend") != backend
            or shape.get("form") != "table"
            or shape.get("table_kind") != "native"
            or shape.get("time") != {"kind": "instant", "unit": "us", "timezone": "UTC"}
        ):
            raise ValueError("Exact C05 native UTC-us keys required")
    for method in methods - {"group.attach@v1"}:
        selected = [key for key in keys if key["method"] == method]
        domains: list[freeze.Json] = (
            [["entity"], ["group"]]
            if mean and method == "state_rollup.mean@v1"
            else [["group"]]
            if mean
            else [["entity"]]
        )
        if any(
            key["input_types"] != ["float64" if mean else mode]
            or key["input_domains"] not in domains
            for key in selected
        ):
            raise ValueError("Exact C05 numeric/domain consumer required")
    retained = [freeze.obj(item) for item in freeze.arr(receipt["retained"])]
    if not retained:
        raise ValueError("Verified C05 result schemas required")
    expected_values = (
        ["double"] * 3
        if mean
        else ["int64", "int64", "int64", "int64", "double", "int64"]
        if mode == "int64"
        else ["int64", "double", "int64", "double", "double"]
    )
    values = [
        next(
            (
                freeze.arr(pair)[1]
                for pair in freeze.arr(item["schema"])
                if freeze.arr(pair)[0] == "value"
            ),
            None,
        )
        for item in retained
    ]
    if values != expected_values:
        raise ValueError("Exact C05 result value types required")
    expected_kinds = (
        [
            "MaterializedStatisticRelation",
            "MaterializedRolledNumericRelation",
            "MaterializedGroupedNumericRelation",
        ]
        if mean
        else ["MaterializedStatisticRelation"] * (len(expected_values) - 1)
        + ["MaterializedGroupedNumericRelation"]
    )
    if [item["kind"] for item in retained] != expected_kinds:
        raise ValueError("Closed C05 result families required")
    roles: set[str] = set()
    complete_tuple = False
    for item in retained:
        fields = [freeze.arr(pair) for pair in freeze.arr(item["schema"])]
        schema = {str(pair[0]): pair[1] for pair in fields}
        expected_keys: list[freeze.Json] = [
            pair for pair in fields if str(pair[0]).startswith("key_")
        ]
        complete_tuple |= expected_keys == [["key_0", "string"], ["key_1", "int64"]]
        parts = [freeze.obj(part) for part in freeze.arr(item["parts"])]
        required = (
            {"row_state"}
            if item["kind"] == "MaterializedStatisticRelation"
            else {"original_state", "coverage"}
        )
        if (
            item.get("verified") is not True
            or not parts
            or schema.get("value") not in ("int64", "double")
            or schema.get("cell_tag") != "string"
            or schema.get("cell_reason") != "string"
            or required != {str(part["role"]) for part in parts}
            or any(part["key_fields"] != expected_keys for part in parts)
        ):
            raise ValueError("Complete C05 Cell/part schema required")
        roles.update(str(part["role"]) for part in parts)
    if not {"row_state", "original_state", "coverage"} <= roles or (
        mode == "int64" and not complete_tuple
    ):
        raise ValueError("Complete coordinate union and both state families required")


def build(
    trino: Path,
    clickhouse: Path,
    family: Literal["C04", "C05"] = "C04",
    *,
    mysql_retry: Path | None = None,
) -> tuple[dict[str, freeze.Json], dict[str, bytes]]:
    """Refuse incomplete invocation and resource bindings before family grants."""
    payload = freeze.load(trino / "freeze")
    authority = freeze.obj(payload["candidate"])["content_sha256"]
    repair = validate_mysql_retry(trino, mysql_retry) if mysql_retry is not None else None
    directories = (trino, clickhouse) if mysql_retry is None else (trino, clickhouse, mysql_retry)
    runs = [freeze.read(directory / "run.json") for directory in directories]
    attachments: dict[str, freeze.Json] = {}
    origins: dict[str, Path] = {}
    tests: list[ET.Element] = []
    executions: dict[tuple[str, str], dict[str, freeze.Json]] = {}
    for directory, run in zip(directories, runs, strict=True):
        verify(directory)
        if (
            run["exit_code"] != (1 if directory == trino and repair is not None else 0)
            or run["candidate_sha256"] != authority
            or freeze.load(directory / "freeze") != payload
        ):
            raise ValueError("Successful identical candidate executions required")
        for name, digest in freeze.obj(run["attachments"]).items():
            if not name.startswith((family.lower() + "-", "deadline-", "mysql-graph-")):
                continue
            if name in attachments:
                raise ValueError("Duplicate source or resource receipt")
            attachments[name], origins[name] = digest, directory
        for item in ET.fromstring(read_attachment(directory, "junit.xml")).findall(".//testcase"):
            key = (item.attrib.get("classname", ""), item.attrib["name"])
            if directory == trino and repair is not None and key in MYSQL_RETRY_NODES:
                continue
            tests.append(item)
            executions[key] = {
                **{field: run[field] for field in ("command", "started", "finished")},
                "source_batch_exit_code": run["exit_code"],
                "source_manifest_sha256": freeze.digest((directory / "run.json").read_bytes()),
            }

    def document(name: str) -> dict[str, freeze.Json]:
        if name not in origins:
            raise ValueError(f"Required {family} attachment missing")
        raw = read_attachment(origins[name], name)
        if freeze.digest(raw) != attachments[name]:
            raise ValueError("Attachment differs from execution manifest")
        return freeze.obj(freeze.checked(json.loads(raw)))

    def invocation(module: str, name: str) -> None:
        selected = [
            item
            for item in tests
            if item.attrib.get("classname") == "tests." + module and item.attrib["name"] == name
        ]
        if len(selected) != 1:
            raise ValueError(f"Required {family} or resource invocation not unique")
        if list(selected[0]):
            raise ValueError(f"Required {family} or resource invocation not passed")

    cancellation, supporting = resource_proofs(attachments, document, invocation)
    if not set(BACKENDS) - {"mysql"} <= set(cancellation) <= set(BACKENDS):
        raise ValueError("Required independent native cancellation profiles missing or unknown")
    controls: dict[str, freeze.Json] = {}
    if family == "C04":
        invocation(
            "test_r93_multiroot_consumers",
            "test_c04_c06_independent_roots_and_temporal_fold[linear_fanout-duckdb]",
        )
        control_name = "c04-refused-incomplete-relationship.json"
        control = document(control_name)
        if (
            control.get("complete_target_key") != ["owner", "tenant"]
            or control.get("relationship_key") != ["owner"]
            or control.get("received") != "Metric roots or relationship endpoints differ"
            or control.get("location") != "analysis.graph_observation"
            or control.get("actual_native_submissions") != []
            or control.get("published_parquet") != 0
            or control.get("resources") != 0
        ):
            raise ValueError("Pre-submission incomplete-relationship refusal required")
        controls["relationship_control"] = {
            "attachment": control_name,
            "sha256": attachments[control_name],
            "observed": control,
        }
    rows = {
        str(freeze.obj(row)["backend"]): freeze.obj(row)
        for row in freeze.arr(payload["requirements"])
        if freeze.obj(row)["family"] == family + ".a/b/c" and freeze.obj(row)["gap_owner"] == "R9.3"
    }
    if set(rows) != set(BACKENDS):
        raise ValueError(f"Frozen six-backend {family} denominator required")
    overrides: dict[str, freeze.Json] = {}
    witnesses: dict[str, bytes] = {}
    for backend in BACKENDS:
        row = rows[backend]
        identity = str(row["id"])
        if backend not in cancellation:
            overrides[identity] = {
                "status": "blocked",
                "owner": "R9.3",
                "reason": "Independent native MySQL cancellation remains unproven",
                "release_condition": "Prove explicitly authorized native cancellation and owner release",
            }
            continue
        records: list[freeze.Json] = []
        modes = MODES if family == "C04" else ("int64", "float64", "mean-int64", "mean-string")
        for mode in modes:
            if family == "C04":
                module = "test_r93_multiroot_consumers"
                node = f"test_c04_c06_independent_roots_and_temporal_fold[{mode}-{backend}]"
                name = f"c04-source-{backend}-{mode}.json"
            elif mode.startswith("mean-"):
                identity_type = mode.removeprefix("mean-")
                module = "test_r93_capability_consumers"
                node = (
                    f"test_c05_original_mean_preserves_component_weights[{backend}-{identity_type}]"
                )
                name = f"c05-mean-{backend}-{identity_type}.json"
            else:
                module = "test_r93_capability_consumers"
                node = (
                    f"test_c05_source_coordinates_statistics_and_original_state[{backend}-{mode}]"
                )
                name = f"c05-source-{backend}-{mode}.json"
            invocation(module, node)
            receipt = document(name)
            if family == "C04":
                validate_consumer(backend, mode, receipt)
            else:
                validate_grouping_consumer(backend, mode, receipt)
            records.append(
                {
                    "attachment": name,
                    "sha256": attachments[name],
                    "execution": executions[("tests." + module, node)],
                    **{
                        field: receipt[field]
                        for field in ("oracle", "environment", "input", "physical_keys", "retained")
                    },
                }
            )
        resource_names = [str(freeze.obj(item)["attachment"]) for item in supporting]
        resource_names.extend(
            name for name in attachments if name.startswith(f"deadline-native-{backend}")
        )
        witness_name = f"{family.lower()}-binding-{backend}.json"
        witness = freeze.encode(
            {
                "requirement_id": identity,
                "candidate_sha256": authority,
                "scenarios": records,
                **controls,
                **({"mysql_environment_repair": repair} if repair is not None else {}),
                "resource_proofs": [
                    {"attachment": name, "sha256": attachments[name], "observed": document(name)}
                    for name in resource_names
                ],
            }
        )
        witnesses[witness_name] = witness
        representative = (
            (
                "tests.test_r93_multiroot_consumers",
                f"test_c04_c06_independent_roots_and_temporal_fold[linear_composite-{backend}]",
            )
            if family == "C04"
            else (
                "tests.test_r93_capability_consumers",
                f"test_c05_source_coordinates_statistics_and_original_state[{backend}-int64]",
            )
        )
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
            **executions[representative],
            "test_node": f"tests/test_r93_multiroot_consumers.py::test_c04_c06_independent_roots_and_temporal_fold[linear_composite-{backend}]"
            if family == "C04"
            else f"tests/test_r93_capability_consumers.py::test_c05_source_coordinates_statistics_and_original_state[{backend}-int64]",
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


def bind(
    trino: Path,
    clickhouse: Path,
    output: Path,
    family: Literal["C04", "C05"] = "C04",
    *,
    mysql_retry: Path | None = None,
) -> dict[str, freeze.Json]:
    result, witnesses = build(trino, clickhouse, family, mysql_retry=mysql_retry)
    output.mkdir(parents=True, exist_ok=False)
    copytree(trino / "freeze", output / "freeze")
    for name, content in witnesses.items():
        (output / name).write_bytes(content)
    for name, directory in (("trino", trino), ("clickhouse", clickhouse)):
        copytree(directory, output / "sources" / name)
    if mysql_retry is not None:
        copytree(mysql_retry, output / "sources" / "mysql-retry")
    return save_result(output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trino", type=Path)
    parser.add_argument("clickhouse", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--family", choices=("C04", "C05"), default="C04")
    parser.add_argument("--mysql-retry", type=Path)
    args = parser.parse_args()
    print(
        freeze.encode(
            bind(
                args.trino, args.clickhouse, args.output, args.family, mysql_retry=args.mysql_retry
            )
        ).decode()
    )
