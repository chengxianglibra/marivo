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
from marivo.analysis.core.frame_audit import (
    EvaluationRecord,
    FrameAudit,
    FrameResultMetadata,
    StoredFrameMetadata,
)
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunFailure,
    decode_failure,
    digest,
    parse_timestamp,
)
from marivo.analysis.materialization.errors import IntegrityError
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
    producers = _rows(
        conn,
        "SELECT run_ref FROM analysis_action_run_terminals WHERE output_artifact_ref=?",
        (ref,),
    )
    if len(producers) != 1 or _text(producers[0], "run_ref") != value.run_id:
        raise invalid("terminal Artifact has a different or duplicate producer")
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
    saved = collection(conn, value.artifact_id)
    if (
        saved.finding_count != value.metadata.finding_count
        or saved.evidence_count != value.metadata.source_fact_count + value.metadata.check_count
        or saved.evidence_digest != value.metadata.evidence_digest
        or saved.finding_set_digest != value.metadata.finding_set_digest
    ):
        raise invalid("terminal collection descriptor differs from captured metadata")
    return value


def audit_evidence(value: FrameResultMetadata) -> tuple[OutputEvidence, ...]:
    return (*value.audit.source_facts, *value.audit.evaluation.checks)


def describe(value: FrameResultMetadata) -> StoredFrameMetadata:
    if value.lineage is None:
        raise invalid("missing saved column lineage")
    evaluation = value.audit.evaluation
    return StoredFrameMetadata(
        value.result_id,
        evaluation.completed_at,
        evaluation.inputs,
        evaluation.operations,
        len(value.audit.source_facts),
        len(evaluation.checks),
        len(value.findings),
        digest(tuple(encode(item) for item in audit_evidence(value))),
        digest(tuple(encode(item) for item in value.findings)),
        value.lineage,
    )


def validate_result_records(schema: DataSchema, metadata: FrameResultMetadata) -> None:
    from marivo.analysis.core.lineage import validate

    if metadata.lineage is None:
        raise invalid("missing saved column lineage")
    validate(metadata.lineage, schema)
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
    metadata: FrameResultMetadata,
    resources: tuple[ResourceRecord, ...],
    event: Callable[[str], None],
) -> ArtifactManifest:
    if describe(metadata) != value.metadata:
        raise invalid("publication audit differs from its captured descriptor")
    findings, evidence = metadata.findings, audit_evidence(metadata)
    payload = encode(value)
    decode(payload, ArtifactManifest)
    finding_payloads = tuple(encode(item) for item in findings)
    evidence_payloads = tuple(encode(item) for item in evidence)
    validate_result_records(value.data.schema, metadata)
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
                "frame-output-v11",
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
        confirm_records(conn, value.artifact_id, metadata.findings, audit_evidence(metadata))
        event("before_commit")
    event("after_commit")
    return value


@dataclass(frozen=True, slots=True)
class RecordRead:
    items: tuple[OutputFinding | OutputEvidence, ...]
    next_cursor: str | None
    total_count: int
    committed_at: str


def _validate_coordinates(
    schema: DataSchema, item: OutputFinding | OutputEvidence, *, source: bool = False
) -> None:
    coordinates = item.coordinates
    slots = {column.slot_id for column in schema.columns}
    if (
        (source and coordinates)
        or len({coordinate.slot_id for coordinate in coordinates}) != len(coordinates)
        or any(coordinate.slot_id not in slots for coordinate in coordinates)
    ):
        raise invalid("output record coordinates must reference distinct saved slots")


def _cursor_error(kind: Literal["finding", "evidence"], received: str) -> IntegrityError:
    member = "findings" if kind == "finding" else "evidence"
    return IntegrityError(
        expected="the preceding cursor for this exact input, audit collection and limit",
        received=received,
        repair=f"Repeat the same Frame.{member}(limit=...) request, or begin again with cursor=None.",
        stage="terminal_record_cursor",
        help_target=f"analysis_frame.{member}",
    )


def records_page(
    store: SessionStore,
    ref: str,
    *,
    kind: Literal["finding", "evidence"],
    limit: int = 20,
    cursor: str | None = None,
    input_id: str | None = None,
    expected_manifest: ArtifactManifest | None = None,
) -> RecordRead:
    """Read one collection range and bounded lookahead in one SQLite snapshot."""
    if type(limit) is not int or not 1 <= limit <= 100 or kind not in ("finding", "evidence"):
        raise invalid("expected a record kind and page size 1..100")
    start = 0
    with store._read() as conn:
        value = manifest(store, conn, ref)
        if value is None or (expected_manifest is not None and value != expected_manifest):
            raise invalid("exact terminal Artifact is absent or changed")
        saved = collection(conn, ref)
        owner = digest(
            (
                store.store_id,
                value.session_id,
                ref,
                input_id,
                kind,
                limit,
                saved.finding_set_digest if kind == "finding" else saved.evidence_digest,
            )
        )
        if cursor is not None:
            try:
                position, identity = decode_keyset_cursor(cursor)
            except (ValueError, RecursionError):
                raise _cursor_error(kind, "invalid terminal record cursor") from None
            if type(position) is not int or position < 0 or identity != owner:
                raise _cursor_error(
                    kind, "record cursor belongs to another input, collection or page size"
                )
            start = position + 1
        table, ordinal, body = (
            ("findings", "finding_ordinal", "finding_body_payload")
            if kind == "finding"
            else ("output_evidence", "evidence_ordinal", "body_payload")
        )
        total = saved.finding_count if kind == "finding" else saved.evidence_count
        if start > total or (cursor is not None and start >= total):
            raise _cursor_error(kind, "terminal record cursor is outside the captured collection")
        expected = min(limit, total - start)
        # Only ordinals are probed beyond the requested bodies. A tail probe at
        # the declared end also detects extra records without a count(*) scan.
        ordinals = _rows(
            conn,
            f"SELECT {ordinal} FROM {table} WHERE artifact_ref=? AND {ordinal}>=? ORDER BY {ordinal} LIMIT ?",
            (ref, start, expected + 1),
        )
        expected_probe = expected + int(start + expected < total)
        if len(ordinals) != expected_probe or any(
            row[ordinal] != start + index for index, row in enumerate(ordinals)
        ):
            raise invalid(
                "terminal collection has missing, duplicate, out-of-order or extra records"
            )
        rows = _rows(
            conn,
            f"SELECT * FROM {table} WHERE artifact_ref=? AND {ordinal}>=? AND {ordinal}<? ORDER BY {ordinal}",
            (ref, start, start + expected),
        )
        if len(rows) != expected:
            raise invalid("terminal collection range is incomplete")
        items: list[OutputFinding | OutputEvidence] = []
        for index, row in enumerate(rows):
            if row[ordinal] != start + index:
                raise invalid("terminal collection ordinal differs")
            text = _text(row, body)
            item = (
                decode(text, OutputFinding) if kind == "finding" else decode(text, OutputEvidence)
            )
            if kind == "finding":
                if (
                    digest(text) != _text(row, "finding_identity_digest")
                    or not isinstance(item, OutputFinding)
                    or item.finding_id != _text(row, "finding_ref")
                ):
                    raise invalid("terminal Finding identity or digest differs")
            elif digest(text) != _text(row, "body_digest"):
                raise invalid("terminal Evidence body digest differs")
            source = kind == "evidence" and start + index < value.metadata.source_fact_count
            if isinstance(item, OutputEvidence) and (
                (source and item.kind not in ("read", "check"))
                or (not source and item.kind != "check")
            ):
                raise invalid("terminal Evidence partition kind differs")
            _validate_coordinates(value.data.schema, item, source=source)
            items.append(item)
        committed = _one(
            conn, "SELECT committed_at FROM dataset_artifacts WHERE artifact_ref=?", (ref,)
        )
        assert committed is not None
        next_cursor = (
            encode_keyset_cursor(start + expected - 1, owner) if start + expected < total else None
        )
        return RecordRead(tuple(items), next_cursor, total, _text(committed, "committed_at"))


@dataclass(frozen=True, slots=True)
class OutputCollection:
    evidence_digest: str
    finding_set_digest: str
    finding_count: int
    evidence_count: int


def collection(conn: sqlite3.Connection, ref: str) -> OutputCollection:
    row = _one(conn, "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (ref,))
    if row is None or _text(row, "evidence_format") != "frame-output-v11":
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


def complete_metadata(conn: sqlite3.Connection, value: ArtifactManifest) -> FrameResultMetadata:
    """Consume both complete collections, including their global set digests."""
    saved = collection(conn, value.artifact_id)
    evidence_rows = _rows(
        conn,
        "SELECT * FROM output_evidence WHERE artifact_ref=? ORDER BY evidence_ordinal",
        (value.artifact_id,),
    )
    finding_rows = _rows(
        conn,
        "SELECT * FROM findings WHERE artifact_ref=? ORDER BY finding_ordinal",
        (value.artifact_id,),
    )
    evidence = tuple(decode(_text(row, "body_payload"), OutputEvidence) for row in evidence_rows)
    findings = tuple(
        decode(_text(row, "finding_body_payload"), OutputFinding) for row in finding_rows
    )
    if len(evidence) != saved.evidence_count or len(findings) != saved.finding_count:
        raise invalid("terminal record collection count differs")
    confirm_records(conn, value.artifact_id, findings, evidence)
    metadata = value.metadata
    result = FrameResultMetadata(
        metadata.result_id,
        FrameAudit(
            evidence[: metadata.source_fact_count],
            EvaluationRecord(
                metadata.evaluated_at,
                metadata.inputs,
                metadata.operations,
                evidence[metadata.source_fact_count :],
            ),
        ),
        findings,
        metadata.lineage,
    )
    validate_result_records(value.data.schema, result)
    if describe(result) != metadata:
        raise invalid("complete audit differs from captured descriptor")
    return result


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
        or _text(collection, "evidence_format") != "frame-output-v11"
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
