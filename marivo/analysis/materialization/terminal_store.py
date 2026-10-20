"""Terminal output transactions within the existing SessionStore authority."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.core.artifact_data import (
    ArtifactManifest,
    DataSchema,
    OutputEvidence,
    OutputFinding,
)
from marivo.analysis.core.frame_audit import FrameResultMetadata
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunFailure,
    decode_failure,
    digest,
    parse_timestamp,
)
from marivo.analysis.materialization.ownership import owns_resource, validate_receipt_owner
from marivo.analysis.materialization.store import SessionStore, _now, _one, _optional, _rows, _text
from marivo.analysis.materialization.terminal_protocol import (
    InvocationInput,
    decode,
    encode,
    invalid,
)


@dataclass(frozen=True, slots=True)
class TerminalRun:
    run_ref: str
    session_ref: str
    invocation_id: str
    dataset_input: InvocationInput
    lifecycle: Literal["incomplete", "succeeded", "failed"]
    admitted_at: str
    terminal_at: str | None
    output_artifact_ref: str | None
    failure: RunFailure | None


def run(store: SessionStore, conn: sqlite3.Connection, ref: str) -> TerminalRun | None:
    row = _one(conn, "SELECT * FROM analysis_action_runs WHERE run_ref=?", (ref,))
    if row is None:
        return None
    selected = decode(_text(row, "dataset_input_payload"), InvocationInput)
    session = _text(row, "session_ref")
    terminal = _one(conn, "SELECT * FROM analysis_action_run_terminals WHERE run_ref=?", (ref,))
    lifecycle: Literal["incomplete", "succeeded", "failed"] = "incomplete"
    output = None
    failure = None
    time = None
    if terminal is not None:
        time = _text(terminal, "terminal_at")
        if _text(terminal, "session_ref") != session or parse_timestamp(time) < parse_timestamp(
            _text(row, "admitted_at")
        ):
            raise invalid("terminal Run ownership or time differs")
        output = _optional(terminal, "output_artifact_ref")
        text = _optional(terminal, "failure_payload")
        status = _text(terminal, "outcome")
        if status == "succeeded" and output is not None and text is None:
            lifecycle = "succeeded"
        elif status == "failed" and output is None and text is not None:
            lifecycle, failure = "failed", decode_failure(text)
        else:
            raise invalid("contradictory terminal Run")
    return TerminalRun(
        ref,
        session,
        _text(row, "invocation_id"),
        selected,
        lifecycle,
        _text(row, "admitted_at"),
        time,
        output,
        failure,
    )


def admit(store: SessionStore, session: str, ref: str, result_id: str) -> None:
    with store._write() as conn:
        if (
            _one(
                conn,
                "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND t.run_ref IS NULL",
                (session,),
            )
            is not None
        ):
            raise invalid("Session already has an incomplete Run")
        conn.execute(
            "INSERT INTO analysis_action_runs VALUES(?,?,?,?,?)",
            (ref, session, ref, _now(), encode(InvocationInput(result_id))),
        )
        conn.execute("UPDATE sessions SET updated_at=? WHERE session_ref=?", (_now(), session))


def manifest(store: SessionStore, conn: sqlite3.Connection, ref: str) -> ArtifactManifest | None:
    row = _one(conn, "SELECT * FROM dataset_artifacts WHERE artifact_ref=?", (ref,))
    if row is None:
        return None
    value = decode(_text(row, "descriptor_payload"), ArtifactManifest)
    producer = run(store, conn, value.run_id)
    if (
        value.store_id != store.store_id
        or value.artifact_id != ref
        or value.session_id != _text(row, "session_ref")
        or producer is None
        or producer.lifecycle != "succeeded"
        or producer.session_ref != value.session_id
        or producer.output_artifact_ref != ref
        or producer.dataset_input.result_id != value.result_id
        or producer.invocation_id != _text(row, "invocation_id")
        or producer.terminal_at != _text(row, "committed_at")
    ):
        raise invalid("terminal Artifact and succeeded producer differ")
    prefix = store.layout.relative_path(store.layout.artifact_dir(value.session_id, ref))
    handles = (
        value.data.rows,
        *(item.rows for item in value.data.attachments),
        *(item.payload.rows for item in value.method_facts),
    )
    locators = tuple(handle.receipt.project_relative_path for handle in handles)
    if len(set(locators)) != len(locators):
        raise invalid("terminal tables must have distinct receipt locations")
    for handle in handles:
        validate_receipt_owner(handle.receipt, prefix)
        if len(handle.payload_digest) != 64 or any(
            c not in "0123456789abcdef" for c in handle.payload_digest
        ):
            raise invalid("terminal receipt requires its immutable payload digest")
        if any(
            owns_resource(handle.receipt, resource)
            for resource in store._resources(conn, value.session_id)
        ):
            raise invalid("committed terminal output retains a cleanup obligation")
    confirm_records(conn, value.artifact_id, value.metadata.findings, audit_evidence(value))
    return value


def audit_evidence(value: ArtifactManifest) -> tuple[OutputEvidence, ...]:
    return (*value.metadata.audit.source_facts, *value.metadata.audit.evaluation.checks)


def validate_result_records(schema: DataSchema, metadata: FrameResultMetadata) -> None:
    findings = metadata.findings
    if len({item.finding_id for item in findings}) != len(findings):
        raise invalid("duplicate terminal Finding identity")
    slots = {column.slot_id for column in schema.columns}
    if any(fact.coordinates for fact in metadata.audit.source_facts):
        raise invalid("inherited source facts cannot reference current output slots")
    records: tuple[OutputFinding | OutputEvidence, ...] = (
        *findings,
        *metadata.audit.evaluation.checks,
    )
    for item in records:
        if len({coordinate.slot_id for coordinate in item.coordinates}) != len(
            item.coordinates
        ) or any(coordinate.slot_id not in slots for coordinate in item.coordinates):
            raise invalid("output record coordinates must reference distinct saved slots")


def publish(
    store: SessionStore,
    value: ArtifactManifest,
    resources: tuple[ResourceRecord, ...],
    event: Callable[[str], None],
) -> ArtifactManifest:
    findings, evidence = value.metadata.findings, audit_evidence(value)
    payload = encode(value)
    decode(payload, ArtifactManifest)
    finding_payloads = tuple(encode(item) for item in findings)
    evidence_payloads = tuple(encode(item) for item in evidence)
    validate_result_records(value.data.schema, value.metadata)
    with store._write() as conn:
        producer = run(store, conn, value.run_id)
        if (
            producer is None
            or producer.lifecycle != "incomplete"
            or producer.session_ref != value.session_id
            or producer.dataset_input.result_id != value.result_id
            or value.store_id != store.store_id
        ):
            raise invalid("terminal publication differs from its incomplete Run")
        now = _now()
        conn.execute(
            "INSERT INTO dataset_artifacts VALUES(?,?,?,?,?)",
            (value.artifact_id, value.session_id, producer.invocation_id, payload, now),
        )
        event("insert_artifact")
        conn.execute(
            "INSERT INTO dataset_evidence VALUES(?,?,?,?,?,?)",
            (
                value.artifact_id,
                digest(evidence_payloads),
                len(evidence),
                len(findings),
                digest(finding_payloads),
                "frame-output-v3",
            ),
        )
        conn.executemany(
            "INSERT INTO output_evidence VALUES(?,?,?,?)",
            (
                (value.artifact_id, ordinal, body, digest(body))
                for ordinal, body in enumerate(evidence_payloads)
            ),
        )
        event("insert_evidence")
        conn.executemany(
            "INSERT INTO findings VALUES(?,?,?,?,?)",
            (
                (item.finding_id, value.artifact_id, ordinal, digest(body), body)
                for ordinal, (item, body) in enumerate(zip(findings, finding_payloads, strict=True))
            ),
        )
        event("insert_findings")
        conn.execute(
            "INSERT INTO analysis_action_run_terminals VALUES(?,?,?,?,?,?)",
            (value.run_id, value.session_id, "succeeded", now, value.artifact_id, None),
        )
        conn.execute(
            "UPDATE sessions SET updated_at=? WHERE session_ref=?", (now, value.session_id)
        )
        event("insert_terminal")
        store._delete_resources(conn, value.run_id, resources)
        if (
            _one(conn, "SELECT 1 FROM action_resource_journal WHERE run_ref=?", (value.run_id,))
            is not None
        ):
            raise invalid("successful terminal Run retains resource obligations")
        result = manifest(store, conn, value.artifact_id)
        if result != value:
            raise invalid("committed terminal manifest differs from publication")
        event("before_commit")
    event("after_commit")
    return value


def records_page(
    store: SessionStore,
    ref: str,
    *,
    kind: Literal["finding", "evidence"],
    limit: int = 20,
    cursor: str | None = None,
) -> tuple[tuple[OutputFinding | OutputEvidence, ...], str | None]:
    """Private bounded exact-output record read, independent of data and Catalog."""
    if type(limit) is not int or not 1 <= limit <= 100 or kind not in ("finding", "evidence"):
        raise invalid("expected a record kind and page size 1..100")
    start = 0
    with store._read() as conn:
        if manifest(store, conn, ref) is None:
            raise invalid("exact terminal Artifact is absent")
        collection = _one(conn, "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (ref,))
        if collection is None or _text(collection, "evidence_format") != "frame-output-v3":
            raise invalid("terminal output collection is absent or obsolete")
        owner = digest(
            (
                ref,
                kind,
                _text(collection, "finding_set_digest" if kind == "finding" else "evidence_digest"),
            )
        )
        if cursor is not None:
            try:
                position, identity = decode_keyset_cursor(cursor)
            except (ValueError, RecursionError):
                raise invalid("invalid terminal record cursor") from None
            if type(position) is not int or position < 0 or identity != owner:
                raise invalid("record cursor belongs to another output collection")
            start = position + 1
        table, ordinal, body = (
            ("findings", "finding_ordinal", "finding_body_payload")
            if kind == "finding"
            else ("output_evidence", "evidence_ordinal", "body_payload")
        )
        total = collection["finding_count" if kind == "finding" else "evidence_count"]
        count = conn.execute(
            f"SELECT count(*) FROM {table} WHERE artifact_ref=?", (ref,)
        ).fetchone()[0]
        if type(total) is not int or total != count or start > total:
            raise invalid("terminal collection cardinality differs")
        rows = _rows(
            conn,
            f"SELECT * FROM {table} WHERE artifact_ref=? AND {ordinal}>=? ORDER BY {ordinal} LIMIT ?",
            (ref, start, limit + 1),
        )
        if any(row[ordinal] != start + index for index, row in enumerate(rows)):
            raise invalid("terminal collection ordinal gap")
        items: list[OutputFinding | OutputEvidence] = []
        for row in rows[:limit]:
            text = _text(row, body)
            if kind == "evidence" and digest(text) != _text(row, "body_digest"):
                raise invalid("terminal Evidence body digest differs")
            item = (
                decode(text, OutputFinding) if kind == "finding" else decode(text, OutputEvidence)
            )
            if kind == "finding" and (
                digest(text) != _text(row, "finding_identity_digest")
                or not isinstance(item, OutputFinding)
                or item.finding_id != _text(row, "finding_ref")
            ):
                raise invalid("terminal Finding identity or digest differs")
            items.append(item)
        return tuple(items), encode_keyset_cursor(start + limit - 1, owner) if len(
            rows
        ) > limit else None


@dataclass(frozen=True, slots=True)
class OutputCollection:
    evidence_digest: str
    finding_set_digest: str
    finding_count: int
    evidence_count: int


def collection(conn: sqlite3.Connection, ref: str) -> OutputCollection:
    row = _one(conn, "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (ref,))
    if row is None or _text(row, "evidence_format") != "frame-output-v3":
        raise invalid("terminal output collection is absent or obsolete")
    evidence_count, finding_count = row["evidence_count"], row["finding_count"]
    if (
        type(evidence_count) is not int
        or evidence_count < 0
        or type(finding_count) is not int
        or finding_count < 0
    ):
        raise invalid("invalid terminal output collection counts")
    digests = (_text(row, "evidence_digest"), _text(row, "finding_set_digest"))
    if any(
        len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in digests
    ):
        raise invalid("invalid terminal output collection digest")
    return OutputCollection(*digests, finding_count, evidence_count)


def evidence(store: SessionStore, ref: str) -> tuple[OutputCollection, tuple[OutputEvidence, ...]]:
    """Read the complete saved Evidence collection in one SQLite snapshot."""
    with store._read() as conn:
        saved = collection(conn, ref)
        rows = _rows(
            conn,
            "SELECT * FROM output_evidence WHERE artifact_ref=? ORDER BY evidence_ordinal",
            (ref,),
        )
        payloads = tuple(_text(row, "body_payload") for row in rows)
        if (
            len(rows) != saved.evidence_count
            or any(
                row["evidence_ordinal"] != index or digest(body) != _text(row, "body_digest")
                for index, (row, body) in enumerate(zip(rows, payloads, strict=True))
            )
            or digest(payloads) != saved.evidence_digest
        ):
            raise invalid("terminal Evidence collection count, order or digest differs")
        return saved, tuple(decode(body, OutputEvidence) for body in payloads)


def confirm_records(
    conn: sqlite3.Connection,
    ref: str,
    findings: tuple[OutputFinding, ...],
    evidence: tuple[OutputEvidence, ...],
) -> None:
    """Confirm the original transaction after a lost commit acknowledgement."""
    collection = _one(conn, "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (ref,))
    finding_payloads = tuple(encode(item) for item in findings)
    evidence_payloads = tuple(encode(item) for item in evidence)
    if (
        collection is None
        or collection["finding_count"] != len(findings)
        or collection["evidence_count"] != len(evidence)
        or _text(collection, "finding_set_digest") != digest(finding_payloads)
        or _text(collection, "evidence_digest") != digest(evidence_payloads)
        or _text(collection, "evidence_format") != "frame-output-v3"
    ):
        raise invalid("original terminal record collection differs from publication")
    rows = _rows(
        conn, "SELECT * FROM findings WHERE artifact_ref=? ORDER BY finding_ordinal", (ref,)
    )
    if tuple(
        (
            _text(row, "finding_ref"),
            row["finding_ordinal"],
            _text(row, "finding_identity_digest"),
            _text(row, "finding_body_payload"),
        )
        for row in rows
    ) != tuple(
        (item.finding_id, ordinal, digest(body), body)
        for ordinal, (item, body) in enumerate(zip(findings, finding_payloads, strict=True))
    ):
        raise invalid("original Finding rows differ from publication")
    rows = _rows(
        conn, "SELECT * FROM output_evidence WHERE artifact_ref=? ORDER BY evidence_ordinal", (ref,)
    )
    if tuple(
        (row["evidence_ordinal"], _text(row, "body_payload"), _text(row, "body_digest"))
        for row in rows
    ) != tuple((ordinal, body, digest(body)) for ordinal, body in enumerate(evidence_payloads)):
        raise invalid("original Evidence rows differ from publication")
