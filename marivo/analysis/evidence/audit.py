"""Direct reads over persisted typed findings and artifact digests."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from marivo._compat import UTC
from marivo.analysis._pages import _BoundedPage, decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.errors import EvidenceLimitError, FindingNotFoundError
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.evidence.types import (
    DerivationRule,
    EvidenceSubjectAdapter,
    Finding,
    FindingValueAdapter,
    TimeWindow,
)


class _PersistedFindingPage(_BoundedPage[Finding]):
    pass


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
) -> _PersistedFindingPage:
    """Return one bounded newest-first page of canonical typed findings."""
    if not 1 <= limit <= 100:
        raise EvidenceLimitError(
            message="findings limit must be within [1, 100]",
            context={
                "limit": limit,
                "location": "artifact.findings(...)",
                "help_target_id": "artifact.findings",
                "default_limit": 50,
            },
        )
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
    return _PersistedFindingPage(
        items=tuple(_row_to_finding(row) for row in visible),
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
            location="artifact.finding(finding_id)",
        )
    return _row_to_finding(row)


__all__ = [
    "get_finding",
    "query_findings",
]
