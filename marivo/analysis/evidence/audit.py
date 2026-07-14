"""Surface 3 audit helpers for evidence objects."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marivo.analysis.errors import PropositionNotFoundError
from marivo.analysis.evidence.types import (
    Assessment,
    EvidenceTrace,
    Finding,
    Proposition,
    Subject,
)


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _loads_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _loads_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    value = json.loads(raw)
    return [str(item) for item in value] if isinstance(value, list) else []


def _row_to_finding(row: sqlite3.Row) -> Finding:
    subject = Subject.model_validate(json.loads(row["subject_payload"]))
    return Finding(
        finding_id=row["finding_id"],
        finding_type=row["finding_type"],
        artifact_id=row["artifact_id"],
        session_id=row["session_id"],
        subject=subject,
        canonical_item_key=row["canonical_item_key"],
        payload=_loads_dict(row["payload"]),
        committed_at=datetime.fromtimestamp(row["committed_at_us"] / 1_000_000, tz=UTC),
        quality_status=row["quality_status"],
    )


def _row_to_proposition(row: sqlite3.Row) -> Proposition:
    return Proposition(
        proposition_id=row["proposition_id"],
        session_id=row["session_id"],
        proposition_type=row["proposition_type"],
        origin_kind=row["origin_kind"],
        derivation_version=row["derivation_version"],
        subject_key=row["subject_key"],
        payload=_loads_dict(row["payload"]),
        seed_finding_refs=_loads_list(row["seed_finding_refs"]),
        created_at=datetime.fromtimestamp(row["created_at_us"] / 1_000_000, tz=UTC),
    )


def _row_to_assessment(row: sqlite3.Row) -> Assessment:
    return Assessment(
        snapshot_id=row["snapshot_id"],
        proposition_id=row["proposition_id"],
        session_id=row["session_id"],
        supersedes_id=row["supersedes_id"],
        status=row["status"],
        confidence=row["confidence"],
        confidence_basis=row["confidence_basis"] or "",
        payload=_loads_dict(row["payload"]),
        created_at=datetime.fromtimestamp(row["created_at_us"] / 1_000_000, tz=UTC),
        is_latest=bool(row["is_latest"]),
    )


def query_findings(
    *,
    db_path: Path,
    session_id: str,
    artifact_id: str | None = None,
    finding_type: str | None = None,
    subject: Subject | None = None,
) -> Iterator[Finding]:
    from marivo.analysis.evidence.identity import canonical_json

    sql = "SELECT * FROM findings WHERE session_id=?"
    args: list[Any] = [session_id]
    if artifact_id is not None:
        sql += " AND artifact_id=?"
        args.append(artifact_id)
    if finding_type is not None:
        sql += " AND finding_type=?"
        args.append(finding_type)
    if subject is not None:
        sql += " AND subject_payload=?"
        args.append(canonical_json(subject.model_dump(mode="json")))
    sql += " ORDER BY committed_at_us, finding_id"

    conn = _open(db_path)
    try:
        for row in conn.execute(sql, args):
            yield _row_to_finding(row)
    finally:
        conn.close()


def query_propositions(
    *,
    db_path: Path,
    session_id: str,
    proposition_type: str | None = None,
    subject: Subject | None = None,
    status: str | None = None,
) -> Iterator[Proposition]:
    from marivo.analysis.evidence.identity import canonical_subject_key

    sql = "SELECT * FROM propositions WHERE session_id=?"
    args: list[Any] = [session_id]
    if proposition_type is not None:
        sql += " AND proposition_type=?"
        args.append(proposition_type)
    if subject is not None:
        sql += " AND subject_key=?"
        args.append(canonical_subject_key(subject))
    if status is not None:
        sql += (
            " AND EXISTS (SELECT 1 FROM assessment_snapshots a "
            "WHERE a.proposition_id=propositions.proposition_id "
            "AND a.is_latest=1 AND a.status=?)"
        )
        args.append(status)
    sql += " ORDER BY created_at_us, proposition_id"

    conn = _open(db_path)
    try:
        for row in conn.execute(sql, args):
            yield _row_to_proposition(row)
    finally:
        conn.close()


def query_assessments(
    *,
    db_path: Path,
    session_id: str,
    proposition_id: str | None = None,
    latest_only: bool = True,
) -> Iterator[Assessment]:
    sql = "SELECT * FROM assessment_snapshots WHERE session_id=?"
    args: list[Any] = [session_id]
    if proposition_id is not None:
        sql += " AND proposition_id=?"
        args.append(proposition_id)
    if latest_only:
        sql += " AND is_latest=1"
    sql += " ORDER BY created_at_us, snapshot_id"

    conn = _open(db_path)
    try:
        for row in conn.execute(sql, args):
            yield _row_to_assessment(row)
    finally:
        conn.close()


def get_proposition(*, db_path: Path, proposition_id: str) -> Proposition:
    conn = _open(db_path)
    try:
        try:
            row = conn.execute(
                "SELECT * FROM propositions WHERE proposition_id=?",
                (proposition_id,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            row = None
    finally:
        conn.close()
    if row is None:
        raise PropositionNotFoundError(
            message=f"proposition_id={proposition_id!r} not found",
            context={"proposition_id": proposition_id},
        )
    return _row_to_proposition(row)


def get_latest_assessment(*, db_path: Path, proposition_id: str) -> Assessment | None:
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM assessment_snapshots WHERE proposition_id=? AND is_latest=1",
            (proposition_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_assessment(row) if row is not None else None


def build_evidence_trace(*, db_path: Path, proposition_id: str) -> EvidenceTrace:
    proposition = get_proposition(db_path=db_path, proposition_id=proposition_id)
    latest = get_latest_assessment(db_path=db_path, proposition_id=proposition_id)
    conn = _open(db_path)
    try:
        seed_findings: list[Finding] = []
        if proposition.seed_finding_refs:
            placeholders = ",".join("?" for _ in proposition.seed_finding_refs)
            rows = conn.execute(
                f"SELECT * FROM findings WHERE finding_id IN ({placeholders})",
                proposition.seed_finding_refs,
            )
            seed_findings = [_row_to_finding(row) for row in rows]

        support_findings: list[Finding] = []
        oppose_findings: list[Finding] = []
        if latest is not None:
            rows = conn.execute(
                """
                SELECT f.*, e.role FROM assessment_edges e
                JOIN findings f ON f.finding_id = e.finding_id
                WHERE e.snapshot_id=?
                """,
                (latest.snapshot_id,),
            )
            for row in rows:
                finding = _row_to_finding(row)
                if row["role"] == "support":
                    support_findings.append(finding)
                elif row["role"] == "oppose":
                    oppose_findings.append(finding)

        source_artifacts = sorted(
            {finding.artifact_id for finding in seed_findings + support_findings + oppose_findings}
        )
    finally:
        conn.close()

    return EvidenceTrace(
        proposition=proposition,
        latest_assessment=latest,
        seed_findings=seed_findings,
        support_findings=support_findings,
        oppose_findings=oppose_findings,
        source_artifacts=source_artifacts,
        source_steps=[],
    )


__all__ = [
    "build_evidence_trace",
    "get_latest_assessment",
    "get_proposition",
    "query_assessments",
    "query_findings",
    "query_propositions",
]
