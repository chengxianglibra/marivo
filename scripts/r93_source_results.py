"""Bind source scenarios only with exact consumers and cancellation proofs."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from scripts import r9_qualification_requirements as freeze
from scripts.r93_evidence_transport import read_attachment, verify
from scripts.r93_retained_results import result, save_result

MYSQL_RESOURCE_NAMES = (
    "deadline-native-mysql.json",
    "mysql-graph-cancel.json",
    "mysql-graph-control_close.json",
)


def validate_mysql_resources(records: dict[str, dict[str, freeze.Json]]) -> None:
    """Require owned native cancellation, atomic failure and pending recovery."""
    if set(records) != set(MYSQL_RESOURCE_NAMES):
        raise ValueError("Complete MySQL native and graph resource proofs required")
    native = records[MYSQL_RESOURCE_NAMES[0]]
    data_id, control_id = native.get("data_connection_id"), native.get("control_connection_id")
    if (
        type(data_id) is not int
        or type(control_id) is not int
        or not 0 < data_id <= 18446744073709551615
        or not 0 < control_id <= 18446744073709551615
        or data_id == control_id
        or native.get("backend") != "mysql"
        or native.get("state") != "failed"
        or native.get("termination") != "remote_unknown"
        or native.get("deadline_seconds") != 0.05
        or not 0.05 < float(str(native.get("elapsed_seconds"))) < 3
        or not native.get("sql")
        or native.get("control_statement") != f"KILL QUERY {data_id}"
        or native.get("control_state") != "succeeded"
        or any(
            native.get(field) is not True
            for field in (
                "connection_disconnected",
                "cleanup_on_owner_thread",
                "independent_server_termination",
                "control_connection_disconnected",
            )
        )
    ):
        raise ValueError("Independent owned MySQL native cancellation proof required")
    for fault, name in zip(("cancel", "control_close"), MYSQL_RESOURCE_NAMES[1:], strict=True):
        graph = records[name]
        data_id, control_id = graph.get("data_connection_id"), graph.get("control_connection_id")
        if (
            type(data_id) is not int
            or type(control_id) is not int
            or not 0 < data_id <= 18446744073709551615
            or not 0 < control_id <= 18446744073709551615
            or data_id == control_id
            or graph.get("backend") != "mysql"
            or graph.get("fault") != fault
            or graph.get("run_lifecycle") != ("failed" if fault == "cancel" else "incomplete")
            or graph.get("published_artifacts") != 1
            or graph.get("remaining_control_obligations") != (0 if fault == "cancel" else 1)
            or graph.get("slow_expression_injected") is not (fault == "cancel")
            or graph.get("reconciliation_refused") is not (fault == "control_close")
            or graph.get("native_cancel_submissions")
            != (
                [{"connection_id": control_id, "sql": f"KILL QUERY {data_id}"}]
                if fault == "cancel"
                else []
            )
            or any(
                graph.get(field) is not True
                for field in (
                    "previous_artifact_preserved",
                    "independent_connection_release",
                    "business_driver_submissions_verified",
                    "data_cursor_cleanup_on_owner_thread",
                )
            )
        ):
            raise ValueError("MySQL graph atomicity and guarded control recovery proof required")


def resource_proofs(
    attachments: dict[str, freeze.Json],
    document: Callable[[str], dict[str, freeze.Json]],
    invocation: Callable[[str, str], None],
) -> tuple[dict[str, str], list[freeze.Json]]:
    """Verify native cancellation and the shared publication failure boundary."""
    supporting: list[freeze.Json] = []
    for method in ("zscore", "mad"):
        invocation(
            "test_r93_source_deadline",
            f"test_statistic_checks_expiry_before_native_submission[{method}]",
        )
        name = "deadline-before-submission-" + method + ".json"
        observed = document(name)
        if (
            observed["method"] != "deviation." + method + "@v1"
            or observed["native_submissions"] != 0
            or observed["published_artifacts"] != 0
            or observed["resources"] != 0
            or observed["run_lifecycle"] != "failed"
        ):
            raise ValueError("Deadline failure was not atomic before submission")
        supporting.append({"attachment": name, "sha256": attachments[name]})
    invocation(
        "test_r93_source_deadline", "test_unconfirmed_source_disconnect_prevents_publication"
    )
    name = "deadline-unconfirmed-release.json"
    release = document(name)
    if (
        release["unconfirmed_release_rejected"] is not True
        or release["published_artifacts"] != 0
        or release["resources"] != 0
        or release["run_lifecycle"] != "failed"
    ):
        raise ValueError("Unconfirmed release must prevent publication")
    supporting.append({"attachment": name, "sha256": attachments[name]})
    cancellation: dict[str, str] = {}
    for backend in ("duckdb", "sqlite", "postgres"):
        name = "deadline-native-" + backend + ".json"
        if backend == "postgres" and name not in attachments:
            continue
        invocation(
            "test_r93_source_deadline",
            "test_postgres_cancel_has_independent_server_termination_proof"
            if backend == "postgres"
            else f"test_native_interrupt_keeps_cleanup_on_the_owner_thread[{backend}]",
        )
        observed = document(name)
        if (
            observed["backend"] != backend
            or observed["state"] != "failed"
            or observed["termination"]
            != ("remote_unknown" if backend == "postgres" else "local_closed")
            or any(
                observed[field] is not True
                for field in (
                    "connection_disconnected",
                    "cleanup_on_owner_thread",
                )
            )
            or not observed["sql"]
            or observed["deadline_seconds"] != 0.05
            or not 0.05 < float(str(observed["elapsed_seconds"])) < 3
        ):
            raise ValueError("Native interruption and owner cleanup were not verified")
        if backend == "postgres":
            if (
                observed["sqlstate"] != "57014"
                or observed["independent_server_termination"] is not True
            ):
                raise ValueError("Independent PostgreSQL cancellation proof required")
        elif observed["interrupt_on_timer_thread"] is not True:
            raise ValueError("Native interruption must run on the timer thread")
        cancellation[backend] = name
    trino_names = [
        "deadline-native-trino-" + phase + ".json"
        for phase in ("pending", "initial-response", "fetch")
    ]
    if any(name in attachments for name in trino_names):
        for phase, name in zip(("pending", "initial-response", "fetch"), trino_names, strict=True):
            invocation(
                "test_r93_source_deadline",
                "test_trino_cancel_has_independent_server_termination_proof[" + phase + "]",
            )
            observed = document(name)
            if (
                observed["backend"] != "trino"
                or observed.get("profile") != "iceberg"
                or observed["phase"] != phase
                or observed["state"] != "failed"
                or observed["termination"] != "remote_unknown"
                or observed["server_state"] != "FAILED"
                or observed["server_error"] != "USER_CANCELED"
                or observed["deadline_seconds"] != 1
                or not 1 < float(str(observed["elapsed_seconds"])) < 5
                or not observed["sql"]
                or not observed["query_id"]
                or any(
                    observed[field] is not True
                    for field in (
                        "connection_disconnected",
                        "cleanup_on_owner_thread",
                        "independent_server_termination",
                    )
                )
            ):
                raise ValueError("Independent Trino cancellation and owner cleanup required")
            if observed["interrupt_on_timer_thread"] is not True and not (
                observed["interrupt_on_timer_thread"] is False
                and phase == "fetch"
                and observed.get("cancellation_mode") == "owner_checkpoint"
                and observed.get("native_cancel_on_owner_thread") is True
            ):
                raise ValueError(
                    "Trino expiry requires timer interruption or observed fetch-owner cancellation"
                )
        cancellation["trino"] = trino_names[0]
    clickhouse_names = [
        "deadline-native-clickhouse-" + phase + ".json" for phase in ("pending", "fetch")
    ]
    if any(name in attachments for name in clickhouse_names):
        for phase, name in zip(("pending", "fetch"), clickhouse_names, strict=True):
            invocation(
                "test_r93_source_deadline",
                "test_clickhouse_deadline_has_independent_server_termination_proof[" + phase + "]",
            )
            observed = document(name)
            settings = freeze.obj(observed["settings"])
            sql, server_sql = str(observed["sql"]), str(observed["server_sql"])
            if (
                observed["backend"] != "clickhouse"
                or observed["profile"] != "mergetree"
                or observed["phase"] != phase
                or observed["state"] != "failed"
                or observed["termination"] != "remote_unknown"
                or observed["server_event"] != "ExceptionWhileProcessing"
                or observed["server_code"] != 159
                or observed["active_queries"] != 0
                or observed["deadline_mode"] != "native_http"
                or observed["deadline_seconds"] != 1
                or not 0 < float(str(observed["elapsed_seconds"])) < 3
                or not sql
                or not server_sql.startswith(sql)
                or server_sql[len(sql) :].strip() != "FORMAT Native"
                or not observed["query_id"]
                or settings["query_id"] != observed["query_id"]
                or not 0 < float(str(settings["max_execution_time"])) <= 1
                or settings["timeout_before_checking_execution_speed"] != 0
                or settings["timeout_overflow_mode"] != "throw"
                or any(
                    observed[field] is not True
                    for field in (
                        "connection_disconnected",
                        "cleanup_on_owner_thread",
                        "independent_server_termination",
                    )
                )
            ):
                raise ValueError(
                    "Independent ClickHouse native deadline and owner cleanup required"
                )
        cancellation["clickhouse"] = clickhouse_names[0]
    if any(name in attachments for name in MYSQL_RESOURCE_NAMES):
        invocation(
            "test_r93_source_deadline", "test_mysql_cancel_has_independent_server_termination_proof"
        )
        for fault in ("cancel", "control_close"):
            invocation(
                "test_r93_mysql_cancellation",
                "test_mysql_graph_failure_preserves_original_artifact_and_control_obligation["
                + fault
                + "]",
            )
        records = {name: document(name) for name in MYSQL_RESOURCE_NAMES}
        validate_mysql_resources(records)
        cancellation["mysql"] = MYSQL_RESOURCE_NAMES[0]
        supporting.extend(
            {"attachment": name, "sha256": attachments[name]} for name in MYSQL_RESOURCE_NAMES[1:]
        )
    return cancellation, supporting


def mysql_resource_supplement(
    directory: Path, payload: dict[str, freeze.Json]
) -> tuple[dict[str, freeze.Json], list[ET.Element], dict[str, freeze.Json]]:
    """Verify the separate graph invocation without rewriting its original run."""
    verify(directory)
    run = freeze.read(directory / "run.json")
    if (
        freeze.load(directory / "freeze") != payload
        or run["candidate_sha256"] != freeze.obj(payload["candidate"])["content_sha256"]
        or run["exit_code"] != 0
        or not run["command"]
        or run.get("phases")
    ):
        raise ValueError("Successful identical frozen candidate supplement required")
    attachments = freeze.obj(run["attachments"])
    if set(attachments) != {"inventory.json", "test.log", "junit.xml", *MYSQL_RESOURCE_NAMES[1:]}:
        raise ValueError("Closed MySQL graph resource supplement required")
    tests = ET.parse(directory / "junit.xml").getroot().findall(".//testcase")
    expected = {
        "test_mysql_graph_failure_preserves_original_artifact_and_control_obligation[" + fault + "]"
        for fault in ("cancel", "control_close")
    }
    if (
        len(tests) != 2
        or {item.attrib["name"] for item in tests} != expected
        or any(
            item.attrib.get("classname") != "tests.test_r93_mysql_cancellation" or list(item)
            for item in tests
        )
    ):
        raise ValueError("Two unique passed MySQL graph supplement invocations required")
    origin: dict[str, freeze.Json] = {
        "directory": str(directory.resolve()),
        "manifest_sha256": freeze.digest((directory / "run.json").read_bytes()),
        "candidate_sha256": run["candidate_sha256"],
        **{field: run[field] for field in ("command", "started", "finished")},
    }
    return {name: attachments[name] for name in MYSQL_RESOURCE_NAMES[1:]}, tests, origin


def bind(directory: Path, mysql_resource_directory: Path | None = None) -> dict[str, freeze.Json]:
    verify(directory)
    payload = freeze.load(directory / "freeze")
    run = freeze.read(directory / "run.json")
    authority = freeze.obj(payload["candidate"])["content_sha256"]
    if run["candidate_sha256"] != authority or run["exit_code"] != 0:
        raise ValueError("Successful unchanged candidate execution required")
    attachments = dict(freeze.obj(run["attachments"]))
    tests = ET.parse(directory / "junit.xml").getroot().findall(".//testcase")
    supplemental_tests: list[ET.Element] = []
    supplemental_origin: dict[str, freeze.Json] | None = None
    supplemental_names: set[str] = set()
    if mysql_resource_directory is not None:
        extra, supplemental_tests, supplemental_origin = mysql_resource_supplement(
            mysql_resource_directory, payload
        )
        if set(extra) & set(attachments):
            raise ValueError("Duplicate primary and supplemental resource attachments")
        supplemental_names = set(extra)
        attachments.update(extra)

    def document(name: str) -> dict[str, freeze.Json]:
        owner = (
            mysql_resource_directory
            if name in supplemental_names and mysql_resource_directory is not None
            else directory
        )
        raw = read_attachment(owner, name)
        if attachments.get(name) != freeze.digest(raw):
            raise ValueError("Attachment is not bound to the execution manifest")
        return freeze.obj(freeze.checked(json.loads(raw)))

    def invocation(module: str, name: str) -> None:
        selected = [
            item
            for item in [*tests, *supplemental_tests]
            if item.attrib.get("classname") == "tests." + module and item.attrib["name"] == name
        ]
        if len(selected) != 1:
            raise ValueError("Required source or cancellation invocation not unique")
        if list(selected[0]):
            raise ValueError("Required source or cancellation invocation not passed")

    executions: dict[tuple[str, str], dict[str, freeze.Json]] = {}
    phases = freeze.arr(run.get("phases", []))
    if phases:
        observed_nodes: list[tuple[str | None, str, tuple[str, ...]]] = []
        if [freeze.obj(phase).get("backend") for phase in phases] != ["trino", "clickhouse"]:
            raise ValueError("Closed Trino and ClickHouse execution phases required")
        for value in phases:
            phase_record = freeze.obj(value)
            if phase_record["exit_code"] != 0 or not phase_record["command"]:
                raise ValueError("Every native execution phase must pass")
            xml_bytes = read_attachment(directory, "junit-" + str(phase_record["backend"]) + ".xml")
            for item in ET.fromstring(xml_bytes).findall(".//testcase"):
                classname, name = item.attrib.get("classname"), item.attrib["name"]
                observed_nodes.append((classname, name, tuple(child.tag for child in item)))
                if classname is not None:
                    key = (classname, name)
                    if key in executions:
                        raise ValueError("Duplicate invocation across native execution phases")
                    executions[key] = {
                        field: phase_record[field] for field in ("command", "started", "finished")
                    }
        if observed_nodes != [
            (item.attrib.get("classname"), item.attrib["name"], tuple(child.tag for child in item))
            for item in tests
        ]:
            raise ValueError("Combined observations differ from actual phase invocations")

    def execution(module: str, name: str) -> dict[str, freeze.Json]:
        return executions.get(
            ("tests." + module, name),
            {field: run[field] for field in ("command", "started", "finished")},
        )

    cancellation, supporting = resource_proofs(attachments, document, invocation)
    if supplemental_origin is not None:
        for raw_support in supporting:
            support = freeze.obj(raw_support)
            if support["attachment"] in supplemental_names:
                support["origin"] = supplemental_origin
    trino_names = [
        "deadline-native-trino-" + phase + ".json"
        for phase in ("pending", "initial-response", "fetch")
    ]
    clickhouse_names = [
        "deadline-native-clickhouse-" + phase + ".json" for phase in ("pending", "fetch")
    ]
    combined = result(directory)
    overrides = freeze.obj(combined["overrides"])
    if phases:
        for raw_override in overrides.values():
            record = freeze.obj(raw_override)
            module, name = str(record["test_node"]).split("::", 1)
            record.update(execution(module.removeprefix("tests/").removesuffix(".py"), name))
    rows = {
        str(freeze.obj(row)["id"]): freeze.obj(row) for row in freeze.arr(payload["requirements"])
    }
    expected = {
        identity
        for identity, row in rows.items()
        if row["family"] in freeze.obj(payload["method_targets"])
        and row["backend"] in cancellation
        and row["scenario"] == "complete-source-capture"
    }
    added: set[str] = set()
    for name in sorted(attachments):
        if not name.startswith("binding-") or not name.endswith(
            tuple("-" + backend + ".json" for backend in cancellation)
        ):
            continue
        receipt = document(name)
        identity = str(receipt["id"])
        if identity not in expected or identity in added:
            raise ValueError("Unknown or duplicate local source requirement")
        row = rows[identity]
        backend, family = str(row["backend"]), str(row["family"])
        profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend)
        if profile is not None and freeze.obj(receipt["environment"]).get("profile") != profile:
            raise ValueError("Source consumer and native cancellation profiles differ")
        method = family.split(".")[1].split("@")[0]
        node = f"test_source_statistic[{method}-{backend}]"
        invocation("test_r93_method_consumers", node)
        physical = freeze.obj(receipt["physical_key"])
        shape = freeze.obj(physical["shape"])
        if (
            physical["method"] != family
            or physical["route"] != "ibis_python"
            or shape["kind"] != "SourceShape"
            or shape["backend"] != backend
            or shape["form"] != "table"
        ):
            raise ValueError("Exact selected source key differs from the required consumer")
        raw_name = str(receipt["raw_receipt"])
        raw = read_attachment(directory, raw_name)
        if freeze.digest(raw) != receipt["raw_receipt_sha256"] or attachments.get(
            raw_name
        ) != freeze.digest(raw):
            raise ValueError("Full native source receipt differs")
        full = freeze.obj(freeze.checked(json.loads(raw)))
        if (
            full["id"] != identity
            or full["physical_key"] != physical
            or full["oracle"] != receipt["oracle"]
            or full["parts"] != receipt["parts"]
            or full["input"] != receipt["input"]
            or full["environment"] != receipt["environment"]
            or full["implementation_id"] != receipt["implementation_id"]
        ):
            raise ValueError("Compact binding differs from full source authority")
        submissions = [freeze.obj(item) for item in freeze.arr(receipt["submissions"])]
        full_submissions = [freeze.obj(item) for item in freeze.arr(full["submissions"])]
        if submissions != [
            {key: value for key, value in item.items() if key != "sql"} for item in full_submissions
        ] or receipt["actual_native_submissions"] != [
            freeze.digest(str(sql).encode())
            for sql in freeze.arr(full["actual_native_submissions"])
        ]:
            raise ValueError("Compact submission bindings differ from native observations")
        if not submissions or any(
            item["state"] != "succeeded"
            or item["connection_disconnected"] is not True
            or item["cursor_state"] not in ("closed", "connection_owned")
            for item in submissions
        ):
            raise ValueError("Successful source cursors and connections must be released")
        obligations = set(map(str, freeze.arr(row["required_proofs"])))
        if obligations != {"submission", "numeric_state", "type_domain_parts", "resource_cancel"}:
            raise ValueError("Unexpected source proof obligation")
        proofs: dict[str, freeze.Json] = {}
        for obligation in sorted(obligations):
            proof_name = cancellation[backend] if obligation == "resource_cancel" else name
            proofs[obligation] = {
                "status": "passed",
                "requirement_id": identity,
                "candidate_sha256": authority,
                "attachment": proof_name,
                "sha256": attachments[proof_name],
                "supporting": (
                    supporting
                    + (
                        [
                            {"attachment": item, "sha256": attachments[item]}
                            for item in trino_names[1:]
                        ]
                        if backend == "trino"
                        else []
                    )
                    + (
                        [
                            {"attachment": item, "sha256": attachments[item]}
                            for item in clickhouse_names[1:]
                        ]
                        if backend == "clickhouse"
                        else []
                    )
                    if obligation == "resource_cancel"
                    else []
                ),
            }
        overrides[identity] = {
            "status": "passed",
            "proof_class": "runtime",
            "expectation": row["expectation"],
            "exit_code": 0,
            "skipped": False,
            "owner_sha256": payload["owners"],
            "environment": {
                "candidate_dependencies": freeze.obj(payload["candidate"])["dependencies"],
                "source": receipt["environment"],
            },
            "input_sha256": freeze.digest(freeze.encode(receipt["input"])),
            "oracle": receipt["oracle"],
            **execution("test_r93_method_consumers", node),
            "test_node": "tests/test_r93_method_consumers.py::" + node,
            "proofs": proofs,
        }
        added.add(identity)
    if added != expected:
        raise ValueError("Missing required local source invocation")
    return save_result(directory, combined)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--mysql-resource-directory", type=Path)
    arguments = parser.parse_args()
    print(freeze.encode(bind(arguments.directory, arguments.mysql_resource_directory)).decode())
