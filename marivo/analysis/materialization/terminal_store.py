"""Terminal output transactions within the existing SessionStore authority."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from marivo.analysis.core.artifact_data import (
    ArtifactManifest,
    DataSchema,
)
from marivo.analysis.core.frame_metadata import (
    EvaluationRecord,
    FrameResultMetadata,
    StoredFrameMetadata,
)
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunFailure,
    decode_failure,
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
    return value


def describe(value: FrameResultMetadata) -> StoredFrameMetadata:
    if value.lineage is None:
        raise invalid("missing saved column lineage")
    evaluation = value.evaluation
    return StoredFrameMetadata(
        value.result_id,
        evaluation.completed_at,
        evaluation.inputs,
        evaluation.operations,
        value.source_reads,
        value.lineage,
    )


def validate_result_metadata(schema: DataSchema, metadata: FrameResultMetadata) -> None:
    from marivo.analysis.core.lineage import validate

    if metadata.lineage is None:
        raise invalid("missing saved column lineage")
    validate(metadata.lineage, schema)


def publish(
    store: SessionStore,
    value: ArtifactManifest,
    metadata: FrameResultMetadata,
    resources: tuple[ResourceRecord, ...],
    event: Callable[[str], None],
) -> ArtifactManifest:
    if describe(metadata) != value.metadata:
        raise invalid("publication metadata differs from its captured descriptor")
    payload = encode(value)
    decode(payload, ArtifactManifest)
    validate_result_metadata(value.data.schema, metadata)
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


def complete_metadata(value: ArtifactManifest) -> FrameResultMetadata:
    """Restore complete captured metadata solely from the authoritative manifest."""
    metadata = value.metadata
    return FrameResultMetadata(
        metadata.result_id,
        EvaluationRecord(metadata.evaluated_at, metadata.inputs, metadata.operations),
        metadata.source_reads,
        metadata.lineage,
    )
