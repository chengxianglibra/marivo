"""v7 operations of SessionStore, sharing its transactions and resource journal."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunFailure,
    decode_failure,
    parse_timestamp,
)
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    RUN_INPUT,
    Descriptor,
    FixedRunInput,
    PartReceipt,
    PrimaryReceipt,
    RunInput,
    SourceRunInput,
    decode,
    descriptor_plan,
    digest,
    encode,
    invalid,
    plan_digest,
    validate_descriptor,
)
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.materialization.ownership import owns_resource, validate_receipt_owner
from marivo.analysis.materialization.store import _now, _one, _optional, _rows, _text

if TYPE_CHECKING:
    from marivo.analysis.materialization.store import SessionStore


@dataclass(frozen=True, slots=True)
class GraphRun:
    run_ref: str
    session_ref: str
    execution_key_digest: str
    dataset_input: RunInput
    input_artifact_refs: tuple[str, ...]
    lifecycle: Literal["incomplete", "succeeded", "failed"]
    admitted_at: str
    terminal_at: str | None
    output_artifact_ref: str | None = None
    failure: RunFailure | None = None


@dataclass(frozen=True, slots=True)
class GraphArtifact:
    artifact_ref: str
    session_ref: str
    execution_key_digest: str
    descriptor: Descriptor
    producing_run_ref: str


def run(store: SessionStore, conn: sqlite3.Connection, ref: str) -> GraphRun | None:
    store._require_generation(7)
    row = _one(conn, "SELECT * FROM analysis_action_runs WHERE run_ref=?", (ref,))
    if row is None:
        return None
    admitted_at = parse_timestamp(_text(row, "admitted_at"))
    session = _text(row, "session_ref")
    selected = decode(_text(row, "dataset_input_payload"), RUN_INPUT)
    inputs = _rows(
        conn,
        "SELECT * FROM analysis_action_run_inputs WHERE run_ref=? ORDER BY input_ordinal",
        (ref,),
    )
    if any(
        item["input_ordinal"] != i or _text(item, "session_ref") != session
        for i, item in enumerate(inputs)
    ):
        raise invalid("Run input order or Session differs")
    refs = tuple(_text(item, "artifact_ref") for item in inputs)
    if refs != (
        tuple(item.artifact_ref for item in selected.ordered_artifact_inputs)
        if isinstance(selected, FixedRunInput)
        else ()
    ):
        raise invalid("Run input envelope differs from ordered input rows")
    terminal = _one(conn, "SELECT * FROM analysis_action_run_terminals WHERE run_ref=?", (ref,))
    outcome: Literal["incomplete", "succeeded", "failed"] = "incomplete"
    output = None
    failure = None
    if terminal is not None:
        if parse_timestamp(_text(terminal, "terminal_at")) < admitted_at:
            raise invalid("terminal predates admission")
        if _text(terminal, "session_ref") != session:
            raise invalid("foreign Run terminal")
        status = _text(terminal, "outcome")
        output = _optional(terminal, "output_artifact_ref")
        text = _optional(terminal, "failure_payload")
        if status == "succeeded" and output is not None and text is None:
            outcome = "succeeded"
        elif status == "failed" and output is None and text is not None:
            outcome, failure = "failed", decode_failure(text)
        else:
            raise invalid("contradictory Run terminal")
    return GraphRun(
        ref,
        session,
        _text(row, "execution_key_digest"),
        selected,
        refs,
        outcome,
        _text(row, "admitted_at"),
        None if terminal is None else _text(terminal, "terminal_at"),
        output,
        failure,
    )


def artifact(store: SessionStore, conn: sqlite3.Connection, ref: str) -> GraphArtifact | None:
    store._require_generation(7)
    row = _one(conn, "SELECT * FROM dataset_artifacts WHERE artifact_ref=?", (ref,))
    if row is None:
        return None
    text = _text(row, "descriptor_payload")
    descriptor = decode(text, DESCRIPTOR)
    root = validate_descriptor(descriptor)
    admitted = descriptor_plan(descriptor, root)
    producer = run(store, conn, descriptor.producing_run_ref)
    session, key = _text(row, "session_ref"), _text(row, "execution_key_digest")
    evidence = _one(conn, "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (ref,))
    if (
        producer is None
        or producer.lifecycle != "succeeded"
        or producer.output_artifact_ref != ref
        or producer.session_ref != session
        or producer.execution_key_digest != key
        or descriptor.execution_key_digest != key
        or descriptor.signature.domain.binding.session_id != session
        or producer.dataset_input.definition_fingerprint != descriptor.definition_fingerprint
        or producer.dataset_input.plan_digest != plan_digest(admitted)
        or evidence is None
        or _text(evidence, "evidence_digest") != digest(text)
        or evidence["finding_count"] != 0
        or _text(evidence, "finding_set_digest") != digest("[]")
        or _text(evidence, "extractor_contract_versions_payload") != "[]"
        or _one(conn, "SELECT finding_ref FROM findings WHERE artifact_ref=?", (ref,)) is not None
    ):
        raise invalid("Artifact, producer, key or Evidence differs")
    from marivo.analysis.materialization.execution_key import (
        FixedKeyInput,
        SourceKeyBinding,
        _fixed_input_occurrences,
        graph_fixed_execution_key,
        graph_source_execution_key,
    )

    selected = producer.dataset_input
    if isinstance(selected, SourceRunInput):
        leaves = admitted.classification.sources
        if len(leaves) != len(selected.ordered_source_bindings) or any(
            leaf.definition.fingerprint != binding[0]
            for leaf, binding in zip(leaves, selected.ordered_source_bindings, strict=True)
        ):
            raise invalid("frozen source occurrences differ from Run input")
        expected_key = graph_source_execution_key(
            admitted,
            tuple(
                SourceKeyBinding(leaf, leaf.definition.shape, binding[1], binding[2])
                for leaf, binding in zip(leaves, selected.ordered_source_bindings, strict=True)
            ),
            producer.run_ref,
        )
    else:
        fixed_leaves = _fixed_input_occurrences(root)
        if len(fixed_leaves) != len(selected.ordered_artifact_inputs) or any(
            leaf.artifact.ref != binding.artifact_ref
            for leaf, binding in zip(fixed_leaves, selected.ordered_artifact_inputs, strict=True)
        ):
            raise invalid("frozen fixed occurrences differ from Run input")
        expected_key = graph_fixed_execution_key(
            admitted,
            tuple(
                FixedKeyInput(
                    leaf,
                    b.session_ref,
                    b.producing_run_ref,
                    b.primary_receipt_digest,
                    b.ordered_parts,
                    b.input_binding,
                    b.method_state_contract_id,
                    b.method_state_version,
                    b.snapshot_digest,
                )
                for leaf, b in zip(fixed_leaves, selected.ordered_artifact_inputs, strict=True)
            ),
        )
    if key != expected_key:
        raise invalid("Run and descriptor key differ from exact frozen execution inputs")
    prefix = store.layout.relative_path(store.layout.artifact_dir(session, ref))
    receipts: tuple[PrimaryReceipt | PartReceipt, ...] = (
        descriptor.primary_receipt,
        *descriptor.parts,
    )
    for receipt in receipts:
        validate_receipt_owner(receipt.local, prefix)
        if any(owns_resource(receipt.local, r) for r in store._resources(conn, session)):
            raise invalid("committed output still has a cleanup obligation")
    return GraphArtifact(ref, session, key, descriptor, producer.run_ref)


def admit(store: SessionStore, session: str, key: str, selected: RunInput, ref: str) -> GraphRun:
    store._require_generation(7)
    payload = encode(selected, RUN_INPUT)
    decode(payload, RUN_INPUT)
    refs = (
        tuple(item.artifact_ref for item in selected.ordered_artifact_inputs)
        if isinstance(selected, FixedRunInput)
        else ()
    )
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
        if (
            _one(
                conn,
                "SELECT artifact_ref FROM dataset_artifacts WHERE session_ref=? AND execution_key_digest=?",
                (session, key),
            )
            is not None
        ):
            raise invalid("execution key already has an Artifact")
        for reference in refs:
            value = artifact(store, conn, reference)
            if value is None or value.session_ref != session:
                raise invalid("absent or foreign fixed input")
        conn.execute(
            "INSERT INTO analysis_action_runs VALUES(?,?,?,?,?)",
            (ref, session, key, _now(), payload),
        )
        conn.executemany(
            "INSERT INTO analysis_action_run_inputs VALUES(?,?,?,?)",
            ((ref, session, i, r) for i, r in enumerate(refs)),
        )
        result = run(store, conn, ref)
        assert result is not None
    return result


def publish(
    store: SessionStore,
    ref: str,
    descriptor: Descriptor,
    resources: tuple[ResourceRecord, ...],
    event: Callable[[str], None],
) -> GraphArtifact:
    store._require_generation(7)
    payload = encode(descriptor, DESCRIPTOR)
    descriptor = decode(payload, DESCRIPTOR)
    validate_descriptor(descriptor)
    with store._write() as conn:
        producer = run(store, conn, descriptor.producing_run_ref)
        if (
            producer is None
            or producer.lifecycle != "incomplete"
            or producer.execution_key_digest != descriptor.execution_key_digest
            or producer.dataset_input.definition_fingerprint != descriptor.definition_fingerprint
        ):
            raise invalid("publication differs from its incomplete Run")
        now = _now()
        conn.execute(
            "INSERT INTO dataset_artifacts VALUES(?,?,?,?,?)",
            (ref, producer.session_ref, producer.execution_key_digest, payload, now),
        )
        event("insert_artifact")
        conn.execute(
            "INSERT INTO dataset_evidence VALUES(?,?,?,?,?)",
            (ref, digest(payload), 0, digest("[]"), "[]"),
        )
        event("insert_evidence")
        conn.execute(
            "INSERT INTO analysis_action_run_terminals VALUES(?,?,?,?,?,?)",
            (producer.run_ref, producer.session_ref, "succeeded", now, ref, None),
        )
        event("insert_terminal")
        store._delete_resources(conn, producer.run_ref, resources)
        if (
            _one(
                conn,
                "SELECT 1 FROM action_resource_journal WHERE run_ref=?",
                (producer.run_ref,),
            )
            is not None
        ):
            raise invalid("successful Run retains resource obligations")
        result = artifact(store, conn, ref)
        assert result is not None
        event("before_commit")
        from marivo.analysis.materialization.execute_deadline import check

        check()
        read_result(store.project_root, descriptor)
        check()
    from marivo.analysis.materialization.execute_deadline import COMMITTED, CURRENT

    if CURRENT.get() is not None:
        COMMITTED.set(True)
    event("after_commit")
    return result
