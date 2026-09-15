"""Operation-scoped metadata reads within one SQLite snapshot."""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from datetime import datetime
from typing import Literal

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.datasets.descriptors import _exact_byte_count, _unavailable_byte_count
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    ArtifactNotFoundError,
    RunNotFoundError,
    SessionNotFoundError,
    SessionStateError,
)
from marivo.analysis.evidence._dataset_types import ArtifactEvidenceSummary, ArtifactIssueCounts
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
    SessionRecord,
    invalid,
    parse_timestamp,
)
from marivo.analysis.materialization.store import SessionStore, _one, _rows, _text
from marivo.analysis.observation.contracts import make_ids
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_read_model import (
    ArtifactSummary,
    FailedRun,
    IncompleteRun,
    RunLifecycle,
    RunPage,
    RunRecord,
    SessionRuntimeRecap,
    SucceededRun,
)
from marivo.introspection.live.model import LiveHelpTarget

OVERALL_GRAPH_SCAN_LIMIT = 5_000


def aware_datetime(value: str) -> datetime:
    return parse_timestamp(value)


_ReadOperation = Literal["runs", "recent", "inspect"]


def _selection_error(
    operation: _ReadOperation, parameter: str, *, expected: str, received: str
) -> SessionStateError:
    if operation == "runs":
        action = "Call runs(limit=20, status=None) without a cursor; continue with the returned next_cursor and the same status."
        target = "runtime.runs"
    elif operation == "recent":
        action = "Call recent(limit=20) without a cursor; continue with the returned next_cursor."
        target = "session.recent"
    else:
        action = "Call inspect(name, run_limit=5) without run_cursor; continue with the returned runs.next_cursor."
        target = "session.inspect"
        parameter = "run_" + parameter
    return SessionStateError(
        message="The selected runtime read argument is outside its supported contract.",
        expected=expected,
        received=received,
        location=f"session.{operation}.{parameter}",
        repair=AnalysisRepair(
            kind="retry",
            action=action,
            help_target=LiveHelpTarget(surface="analysis", canonical_id=target),
        ),
    )


def page_after(
    limit: int, cursor: str | None, *, operation: _ReadOperation = "runs"
) -> tuple[str, str] | None:
    if type(limit) is not int or not 1 <= limit <= 100:
        received = (
            "limit is not an exact integer"
            if type(limit) is not int
            else "limit is outside [1, 100]"
        )
        raise _selection_error(
            operation, "limit", expected="one exact integer within [1, 100]", received=received
        )
    if cursor is None:
        return None
    expected = (
        "None for the first page or the exact next_cursor returned by the same read operation"
    )
    if type(cursor) is not str or not cursor or len(cursor) > 16_384:
        raise _selection_error(
            operation, "cursor", expected=expected, received="cursor is not bounded non-empty text"
        )
    decoded: tuple[str | int, str] | None = None
    with suppress(ValueError, RecursionError):
        decoded = decode_keyset_cursor(cursor)
    if decoded is None:
        raise _selection_error(
            operation,
            "cursor",
            expected=expected,
            received="cursor is not a supported keyset encoding",
        )
    timestamp, identity = decoded
    identity_bytes: int | None = None
    with suppress(UnicodeEncodeError):
        identity_bytes = len(identity.encode("utf-8"))
    if (
        type(timestamp) is not str
        or not identity
        or identity_bytes is None
        or identity_bytes > 4096
    ):
        raise _selection_error(
            operation,
            "cursor",
            expected=expected,
            received="cursor does not contain a bounded timestamp and identity",
        )
    parsed: datetime | None = None
    with suppress(AnalysisError):
        parsed = aware_datetime(timestamp)
    if parsed is None or encode_keyset_cursor(timestamp, identity) != cursor:
        raise _selection_error(
            operation,
            "cursor",
            expected=expected,
            received="cursor is not a canonical timezone-aware continuation",
        )
    return timestamp, identity


def _validate_status(status: RunLifecycle | None) -> None:
    if status is not None and (
        type(status) is not str or status not in ("incomplete", "succeeded", "failed")
    ):
        raise _selection_error(
            "runs",
            "status",
            expected="None or one of incomplete, succeeded, failed",
            received="status is not a supported Run lifecycle selection",
        )


def require_session(
    store: SessionStore, conn: sqlite3.Connection, session_ref: str
) -> SessionRecord:
    row = _one(conn, "SELECT * FROM sessions WHERE session_ref=?", (session_ref,))
    if row is None:
        raise SessionNotFoundError(
            message="The selected Session does not exist in this project.",
            expected="an existing Session from recent()",
            received=session_ref,
            location="session.runtime",
            repair=AnalysisRepair(
                kind="inspect",
                action="Read recent() and select one existing Session.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
            ),
        )
    return store._session(row)


def count(conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...]) -> int:
    row = _one(conn, sql, parameters)
    if row is None:
        raise invalid("missing runtime aggregate")
    value: object = row[0]
    if type(value) is not int or value < 0:
        raise invalid("invalid runtime aggregate")
    return value


def run_in_snapshot(
    store: SessionStore, conn: sqlite3.Connection, session_ref: str, run_id: str
) -> RunRecord:
    # Ownership selection precedes body decoding, including for foreign corrupt rows.
    row = _one(
        conn,
        "SELECT run_ref FROM analysis_action_runs WHERE session_ref=? AND run_ref=?",
        (session_ref, run_id),
    )
    if row is None:
        raise RunNotFoundError.for_id(run_id)
    value = store._run(conn, run_id)
    if value is None or value.session_ref != session_ref:
        raise invalid("selected Run identity mismatch")
    admitted_at = aware_datetime(value.admitted_at)
    inputs = tuple(ArtifactRef(ref=ref) for ref in value.input_artifact_refs)
    for ref in inputs:
        if (
            _one(
                conn, "SELECT artifact_ref FROM dataset_artifacts WHERE artifact_ref=?", (ref.ref,)
            )
            is None
        ):
            raise invalid("selected Run input Artifact is missing")
    if value.lifecycle == "incomplete":
        return IncompleteRun(
            run_id=run_id,
            session_id=session_ref,
            admitted_at=admitted_at,
            dataset_input=value.dataset_input,
            input_artifact_refs=inputs,
        )
    if value.terminal_at is None:
        raise invalid("terminal Run has no terminal time")
    terminal_at = aware_datetime(value.terminal_at)
    if terminal_at < admitted_at:
        raise invalid("terminal Run precedes admission")
    if value.lifecycle == "succeeded":
        if value.output_artifact_ref is None:
            raise invalid("succeeded Run has no output Artifact")
        artifact = _one(
            conn,
            "SELECT session_ref,execution_key_digest,committed_at FROM dataset_artifacts WHERE artifact_ref=?",
            (value.output_artifact_ref,),
        )
        if (
            artifact is None
            or _text(artifact, "session_ref") != session_ref
            or _text(artifact, "execution_key_digest") != value.execution_key_digest
            or aware_datetime(_text(artifact, "committed_at")) != terminal_at
        ):
            raise invalid("succeeded Run output identity or publication time mismatch")
        producers = _rows(
            conn,
            "SELECT run_ref FROM analysis_action_run_terminals WHERE output_artifact_ref=?",
            (value.output_artifact_ref,),
        )
        if len(producers) != 1 or _text(producers[0], "run_ref") != run_id:
            raise invalid("succeeded Run output has a different producer")
        return SucceededRun(
            run_id=run_id,
            session_id=session_ref,
            admitted_at=admitted_at,
            dataset_input=value.dataset_input,
            input_artifact_refs=inputs,
            finished_at=terminal_at,
            output_artifact_ref=ArtifactRef(ref=value.output_artifact_ref),
        )
    if value.failure is None:
        raise invalid("failed Run has no structured failure")
    return FailedRun(
        run_id=run_id,
        session_id=session_ref,
        admitted_at=admitted_at,
        dataset_input=value.dataset_input,
        input_artifact_refs=inputs,
        failed_at=terminal_at,
        failure=value.failure,
    )


def runs_in_snapshot(
    store: SessionStore,
    conn: sqlite3.Connection,
    session_ref: str,
    *,
    status: RunLifecycle | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> RunPage:
    after = page_after(limit, cursor)
    _validate_status(status)
    predicates = ["a.session_ref=?"]
    parameters: list[object] = [session_ref]
    if status == "incomplete":
        predicates.append("t.run_ref IS NULL")
    elif status is not None:
        predicates.append("t.outcome=?")
        parameters.append(status)
    if after is not None:
        predicates.append("(a.admitted_at,a.run_ref)<(?,?)")
        parameters.extend(after)
    parameters.append(limit + 1)
    rows = _rows(
        conn,
        "SELECT a.run_ref,a.admitted_at FROM analysis_action_runs a "
        "LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE "
        + " AND ".join(predicates)
        + " ORDER BY a.admitted_at DESC,a.run_ref DESC LIMIT ?",
        tuple(parameters),
    )
    selected = rows[:limit]
    items = tuple(
        run_in_snapshot(store, conn, session_ref, _text(row, "run_ref")) for row in selected
    )
    has_more = len(rows) > limit
    return RunPage(
        items=items,
        limit=limit,
        has_more=has_more,
        next_cursor=encode_keyset_cursor(
            _text(selected[-1], "admitted_at"), _text(selected[-1], "run_ref")
        )
        if has_more
        else None,
    )


def runs(
    store: SessionStore,
    session_ref: str,
    *,
    status: RunLifecycle | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> RunPage:
    page_after(limit, cursor)
    _validate_status(status)
    with store._read() as conn:
        require_session(store, conn, session_ref)
        return runs_in_snapshot(store, conn, session_ref, status=status, limit=limit, cursor=cursor)


def get_run(store: SessionStore, session_ref: str, run_id: str) -> RunRecord:
    with store._read() as conn:
        require_session(store, conn, session_ref)
        return run_in_snapshot(store, conn, session_ref, run_id)


def summary_in_snapshot(
    store: SessionStore, conn: sqlite3.Connection, record: ArtifactRecord
) -> ArtifactSummary:
    producer = store._run(conn, record.producing_run_ref)
    if producer is None or producer.terminal_at is None or producer.lifecycle != "succeeded":
        raise invalid("Artifact summary has no succeeded producer")
    admitted = aware_datetime(producer.admitted_at)
    finished = aware_datetime(producer.terminal_at)
    committed = aware_datetime(record.committed_at)
    if finished < admitted or committed < admitted:
        raise invalid("Artifact publication precedes producer admission")
    descriptor = record.descriptor
    receipt = descriptor.storage_receipt
    evidence = record.evidence
    return ArtifactSummary(
        artifact_ref=ArtifactRef(ref=record.artifact_ref),
        artifact_session_ref=record.session_ref,
        run_admitted_at=admitted,
        run_finished_at=finished,
        family_id=descriptor.row_contract.shape_id.family_id,
        shape_id=str(descriptor.row_contract.shape_id),
        definition_fingerprint=descriptor.definition_fingerprint,
        committed_at=committed,
        producing_run_ref=record.producing_run_ref,
        realized_row_count=receipt.realized_row_count,
        realized_byte_count=_exact_byte_count(receipt.realized_byte_count)
        if receipt.realized_byte_count is not None
        else _unavailable_byte_count("not_measured", ids=make_ids(())),
        storage_kind_id="parquet" if receipt.kind == "local" else receipt.kind,
        content_authority_digest=receipt.identity_digest,
        evidence=ArtifactEvidenceSummary(
            quality_summary_digest=evidence.quality_summary_digest,
            typed_issue_digest=evidence.typed_issue_digest,
            evidence_digest=evidence.evidence_digest,
            finding_count=evidence.finding_count,
            finding_set_digest=evidence.finding_set_digest,
        ),
        issue_counts=ArtifactIssueCounts(
            warning=sum(item.severity == "warning" for item in descriptor.typed_issues),
            blocking=sum(item.severity == "blocking" for item in descriptor.typed_issues),
        ),
    )


def missing_artifact(artifact_ref: str) -> ArtifactNotFoundError:
    return ArtifactNotFoundError(
        message="The selected Artifact does not exist in this Store.",
        expected="an exact committed Artifact ref from this Store",
        received=artifact_ref,
        location="session.artifact",
        repair=AnalysisRepair(
            kind="inspect",
            action="Read bounded Run outputs or the Session graph and select an exact Artifact ref.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
        ),
    )


def artifact_summary(store: SessionStore, artifact_ref: str) -> ArtifactSummary:
    with store._read() as conn:
        record = store._artifact(conn, artifact_ref)
        if record is None:
            raise missing_artifact(artifact_ref)
        return summary_in_snapshot(store, conn, record)


_HEAD_SQL = (
    "SELECT d.artifact_ref,d.committed_at FROM dataset_artifacts d WHERE d.session_ref=? "
    "AND NOT EXISTS (SELECT 1 FROM analysis_action_run_inputs i "
    "JOIN analysis_action_run_terminals t USING(run_ref) WHERE i.artifact_ref=d.artifact_ref "
    "AND i.session_ref=d.session_ref AND t.outcome='succeeded')"
)


def recap(store: SessionStore, session_ref: str) -> SessionRuntimeRecap:
    with store._read() as conn:
        require_session(store, conn, session_ref)
        counts = {
            _text(row, "status"): int(row["total"])
            for row in _rows(
                conn,
                "SELECT coalesce(t.outcome,'incomplete') status,count(*) total "
                "FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) "
                "WHERE a.session_ref=? GROUP BY status",
                (session_ref,),
            )
        }
        run_count = sum(counts.values())
        artifact_count = count(
            conn, "SELECT count(*) FROM dataset_artifacts WHERE session_ref=?", (session_ref,)
        )
        head_count = count(conn, "SELECT count(*) FROM (" + _HEAD_SQL + ")", (session_ref,))
        heads = _rows(
            conn,
            _HEAD_SQL + " ORDER BY d.committed_at DESC,d.artifact_ref DESC LIMIT 3",
            (session_ref,),
        )
        attention = _rows(
            conn,
            "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) "
            "WHERE a.session_ref=? AND (t.outcome='failed' OR t.run_ref IS NULL) "
            "ORDER BY a.admitted_at DESC,a.run_ref DESC LIMIT 3",
            (session_ref,),
        )
        foreign_count = count(
            conn,
            "SELECT count(DISTINCT i.artifact_ref) FROM analysis_action_run_inputs i "
            "JOIN dataset_artifacts d USING(artifact_ref) WHERE i.session_ref=? AND d.session_ref<>i.session_ref",
            (session_ref,),
        )
        return SessionRuntimeRecap(
            session_id=session_ref,
            run_count=run_count,
            artifact_count=artifact_count,
            head_artifact_count=head_count,
            head_artifact_refs=tuple(ArtifactRef(ref=_text(row, "artifact_ref")) for row in heads),
            succeeded_run_count=counts.get("succeeded", 0),
            failed_run_count=counts.get("failed", 0),
            incomplete_run_count=counts.get("incomplete", 0),
            attention_run_ids=tuple(_text(row, "run_ref") for row in attention),
            overall_graph_available=run_count + artifact_count + foreign_count
            <= OVERALL_GRAPH_SCAN_LIMIT,
        )


__all__: list[str] = []
