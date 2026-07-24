"""Direct reads over persisted typed findings and artifact digests."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.errors import (
    EvidenceDigestNotAvailableError,
    FindingNotFoundError,
)
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.evidence.types import (
    ArtifactDigest,
    ArtifactDigestPage,
    DerivationRule,
    EvidenceDerivationTrace,
    EvidenceSubjectAdapter,
    Finding,
    FindingPage,
    FindingValueAdapter,
    TimeWindow,
)


def _loads(raw: str | None, default: Any) -> Any:
    return json.loads(raw) if raw else default


def _row_to_finding(row: sqlite3.Row) -> Finding:
    observed = _loads(row["observed_window_payload"], None)
    return Finding(
        finding_id=row["finding_id"],
        finding_type=row["finding_type"],
        epistemic_kind=row["epistemic_kind"],
        artifact_id=row["artifact_id"],
        session_id=row["session_id"],
        subject=EvidenceSubjectAdapter.validate_json(row["subject_payload"]),
        canonical_item_key=row["canonical_item_key"],
        value=FindingValueAdapter.validate_python(_loads(row["value_payload"], {})),
        derivation=DerivationRule.model_validate_json(row["derivation_payload"]),
        source_refs=tuple(_loads(row["source_refs_payload"], [])),
        observed_window=TimeWindow.model_validate(observed) if observed else None,
        quality_status=row["quality_status"],
        committed_at=datetime.fromtimestamp(row["committed_at_us"] / 1_000_000, tz=UTC),
        extractor_version=row["extractor_version"],
        artifact_schema_version=row["artifact_schema_version"],
    )


def query_findings(
    *,
    store: EvidenceStore,
    session_id: str,
    artifact_ref: str | None = None,
    kind: str | None = None,
    subject: Any = None,
    limit: int = 50,
    cursor: str | None = None,
) -> FindingPage:
    """Return one bounded newest-first page of canonical typed findings."""
    if not 1 <= limit <= 100:
        raise ValueError("findings limit must be within [1, 100]")
    clauses = ["session_id = ?"]
    params: list[object] = [session_id]
    if artifact_ref is not None:
        clauses.append("artifact_id = ?")
        params.append(artifact_ref)
    if kind is not None:
        clauses.append("finding_type = ?")
        params.append(kind)
    if subject is not None:
        payload = EvidenceSubjectAdapter.validate_python(subject).model_dump(mode="json")
        clauses.append("subject_payload = ?")
        params.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if cursor is not None:
        committed_at, identity = decode_keyset_cursor(cursor)
        if not isinstance(committed_at, int):
            raise ValueError("findings cursor has an invalid sort key")
        clauses.append("(committed_at_us < ? OR (committed_at_us = ? AND finding_id < ?))")
        params.extend((committed_at, committed_at, identity))
    params.append(limit + 1)
    rows = list(
        store.read().execute(
            f"SELECT * FROM findings WHERE {' AND '.join(clauses)} "
            "ORDER BY committed_at_us DESC, finding_id DESC LIMIT ?",
            tuple(params),
        )
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = (
        encode_keyset_cursor(visible[-1]["committed_at_us"], visible[-1]["finding_id"])
        if has_more
        else None
    )
    return FindingPage(
        items=tuple(_row_to_finding(row) for row in visible),
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


def query_digests(
    *,
    store: EvidenceStore,
    session_id: str,
    operator: str | None = None,
    subject: Any = None,
    limit: int = 10,
    cursor: str | None = None,
) -> ArtifactDigestPage:
    """Return one bounded newest-first page of persisted digest snapshots."""
    from marivo.analysis.evidence.identity import canonical_subject_key

    if not 1 <= limit <= 100:
        raise ValueError("digests limit must be within [1, 100]")
    clauses = ["session_id = ?"]
    params: list[object] = [session_id]
    if operator is not None:
        clauses.append("operator = ?")
        params.append(operator)
    if subject is not None:
        clauses.append("subject_key = ?")
        params.append(canonical_subject_key(EvidenceSubjectAdapter.validate_python(subject)))
    if cursor is not None:
        committed_at, identity = decode_keyset_cursor(cursor)
        if not isinstance(committed_at, int):
            raise ValueError("digests cursor has an invalid sort key")
        clauses.append("(committed_at_us < ? OR (committed_at_us = ? AND artifact_id < ?))")
        params.extend((committed_at, committed_at, identity))
    params.append(limit + 1)
    rows = list(
        store.read().execute(
            f"SELECT * FROM artifact_digests WHERE {' AND '.join(clauses)} "
            "ORDER BY committed_at_us DESC, artifact_id DESC LIMIT ?",
            tuple(params),
        )
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = (
        encode_keyset_cursor(visible[-1]["committed_at_us"], visible[-1]["artifact_id"])
        if has_more
        else None
    )
    return ArtifactDigestPage(
        items=tuple(ArtifactDigest.model_validate_json(row["digest_payload"]) for row in visible),
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


def get_finding(*, store: EvidenceStore, finding_id: str) -> Finding:
    """Return one typed finding or raise a typed not-found error."""
    row = (
        store.read()
        .execute("SELECT * FROM findings WHERE finding_id = ?", (finding_id,))
        .fetchone()
    )
    if row is None:
        raise FindingNotFoundError(
            message=f"finding {finding_id!r} does not exist",
            expected="an existing canonical finding id",
            received=finding_id,
            location="session.evidence.finding",
        )
    return _row_to_finding(row)


def get_digest(*, store: EvidenceStore, artifact_ref: str) -> ArtifactDigest:
    """Return one persisted digest without rebuilding it from findings."""
    row = (
        store.read()
        .execute(
            "SELECT digest_payload FROM artifact_digests WHERE artifact_id = ?",
            (artifact_ref,),
        )
        .fetchone()
    )
    if row is not None:
        return ArtifactDigest.model_validate_json(row["digest_payload"])
    artifact = (
        store.read()
        .execute("SELECT evidence_status FROM artifacts WHERE artifact_id = ?", (artifact_ref,))
        .fetchone()
    )
    finding_exists = (
        store.read()
        .execute("SELECT 1 FROM findings WHERE artifact_id = ? LIMIT 1", (artifact_ref,))
        .fetchone()
        is not None
    )
    status = artifact["evidence_status"] if artifact is not None else "unavailable"
    raise EvidenceDigestNotAvailableError(
        message=f"artifact {artifact_ref!r} has no persisted evidence digest",
        expected="a complete persisted ArtifactDigest",
        received=f"evidence_status={status}, findings_available={finding_exists}",
        location="session.evidence.digest",
        context={
            "artifact_ref": artifact_ref,
            "evidence_status": status,
            "findings_available": finding_exists,
            "fallback": "session.evidence.findings(...) or session.get_frame(ref)",
        },
    )


def build_evidence_trace(*, store: EvidenceStore, finding_id: str) -> EvidenceDerivationTrace:
    """Trace one finding directly to its sources and retained digest items."""
    finding = get_finding(store=store, finding_id=finding_id)
    retained: list[str] = []
    row = (
        store.read()
        .execute(
            "SELECT digest_payload FROM artifact_digests WHERE artifact_id = ?",
            (finding.artifact_id,),
        )
        .fetchone()
    )
    if row is not None:
        digest = ArtifactDigest.model_validate_json(row["digest_payload"])
        retained = [
            item.item_id
            for item in digest.items
            if finding_id in item.derivation.source_finding_refs
        ]
    return EvidenceDerivationTrace(
        finding=finding,
        derivation=finding.derivation,
        source_artifact_ref=finding.artifact_id,
        source_fields=finding.derivation.source_fields,
        source_refs=finding.source_refs,
        retained_digest_item_refs=tuple(retained),
    )


__all__ = [
    "build_evidence_trace",
    "get_digest",
    "get_finding",
    "query_digests",
    "query_findings",
]
