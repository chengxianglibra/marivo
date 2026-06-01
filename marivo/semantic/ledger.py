"""Evidence ledger: provenance DTOs, structural fingerprint, and on-disk store.

Provenance metadata only — never executable expression bodies, never a parallel
semantic definition. Python files remain the only semantic source of truth.
Contracts spec sections 3, 5, 6.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.datasources.metadata import TableMetadata


@dataclass(frozen=True)
class DecisionRecord:
    decision_kind: str
    chosen: object
    agreement_confidence: str
    qualifying_sources: tuple[str, ...]
    materiality: str
    blast_radius: int
    evidence_fingerprint: str
    question_id: str | None
    decided_at: str
    cited_table: str | None = None
    cited_columns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_kind": self.decision_kind,
            "chosen": self.chosen,
            "agreement_confidence": self.agreement_confidence,
            "qualifying_sources": list(self.qualifying_sources),
            "materiality": self.materiality,
            "blast_radius": self.blast_radius,
            "evidence_fingerprint": self.evidence_fingerprint,
            "question_id": self.question_id,
            "decided_at": self.decided_at,
            "cited_table": self.cited_table,
            "cited_columns": list(self.cited_columns),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> DecisionRecord:
        cited_columns_raw = data.get("cited_columns", [])
        return cls(
            decision_kind=str(data["decision_kind"]),
            chosen=data["chosen"],
            agreement_confidence=str(data["agreement_confidence"]),
            qualifying_sources=tuple(str(s) for s in data["qualifying_sources"]),  # type: ignore[attr-defined]
            materiality=str(data["materiality"]),
            blast_radius=int(data["blast_radius"]),  # type: ignore[call-overload]
            evidence_fingerprint=str(data["evidence_fingerprint"]),
            question_id=None if data["question_id"] is None else str(data["question_id"]),
            decided_at=str(data["decided_at"]),
            cited_table=None if data.get("cited_table") is None else str(data["cited_table"]),
            cited_columns=tuple(str(c) for c in cited_columns_raw),  # type: ignore[attr-defined]
        )


@dataclass(frozen=True)
class RejectedCandidate:
    decision_kind: str
    candidate: str
    reason: str
    evidence_fingerprint: str
    rejected_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_kind": self.decision_kind,
            "candidate": self.candidate,
            "reason": self.reason,
            "evidence_fingerprint": self.evidence_fingerprint,
            "rejected_at": self.rejected_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RejectedCandidate:
        return cls(
            decision_kind=str(data["decision_kind"]),
            candidate=str(data["candidate"]),
            reason=str(data["reason"]),
            evidence_fingerprint=str(data["evidence_fingerprint"]),
            rejected_at=str(data["rejected_at"]),
        )


@dataclass(frozen=True)
class ConfirmationRecord:
    ts: str
    question_id: str
    decision_kind: str
    subject_refs: tuple[str, ...]
    answer: object
    evidence_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "question_id": self.question_id,
            "decision_kind": self.decision_kind,
            "subject_refs": list(self.subject_refs),
            "answer": self.answer,
            "evidence_fingerprint": self.evidence_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ConfirmationRecord:
        return cls(
            ts=str(data["ts"]),
            question_id=str(data["question_id"]),
            decision_kind=str(data["decision_kind"]),
            subject_refs=tuple(str(s) for s in data["subject_refs"]),  # type: ignore[attr-defined]
            answer=data["answer"],
            evidence_fingerprint=str(data["evidence_fingerprint"]),
        )


def evidence_fingerprint(
    columns: Mapping[str, str],
    table_comment: str | None,
    column_comments: Mapping[str, str | None],
) -> str:
    """Structural, single-tier fingerprint (contracts spec section 6). Hashes the
    cited columns + comments only. Sample values are deliberately excluded -- data
    drift is not detected here."""
    payload = {
        "columns": [{"name": name, "type": columns[name]} for name in sorted(columns)],
        "table_comment": table_comment,
        "column_comments": {name: column_comments[name] for name in sorted(column_comments)},
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class ObjectEvidence:
    semantic_id: str
    authored_at: str
    decisions: tuple[DecisionRecord, ...]
    rejected_candidates: tuple[RejectedCandidate, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "semantic_id": self.semantic_id,
            "authored_at": self.authored_at,
            "decisions": [d.to_dict() for d in self.decisions],
            "rejected_candidates": [r.to_dict() for r in self.rejected_candidates],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ObjectEvidence:
        return cls(
            semantic_id=str(data["semantic_id"]),
            authored_at=str(data["authored_at"]),
            decisions=tuple(DecisionRecord.from_dict(d) for d in data["decisions"]),  # type: ignore[attr-defined]
            rejected_candidates=tuple(
                RejectedCandidate.from_dict(r)
                for r in data["rejected_candidates"]  # type: ignore[attr-defined]
            ),
        )


def _model_of(semantic_id: str) -> str:
    return semantic_id.split(".", 1)[0]


class LedgerStore:
    """Canonical-JSON file IO under <semantic_root>/<model>/_evidence/."""

    def __init__(self, semantic_root: str | Path) -> None:
        self._root = Path(semantic_root)

    def _evidence_dir(self, model: str) -> Path:
        return self._root / model / "_evidence"

    def _object_path(self, semantic_id: str) -> Path:
        return self._evidence_dir(_model_of(semantic_id)) / "objects" / f"{semantic_id}.json"

    def write_object(self, obj: ObjectEvidence) -> None:
        path = self._object_path(obj.semantic_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj.to_dict(), sort_keys=True, indent=2) + "\n")

    def read_object(self, semantic_id: str) -> ObjectEvidence | None:
        path = self._object_path(semantic_id)
        if not path.exists():
            return None
        return ObjectEvidence.from_dict(json.loads(path.read_text()))

    def _confirmations_path(self, model: str) -> Path:
        return self._evidence_dir(model) / "confirmations.jsonl"

    def append_confirmation(self, record: ConfirmationRecord) -> None:
        model = _model_of(record.subject_refs[0]) if record.subject_refs else "_global"
        path = self._confirmations_path(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def read_confirmations(self, model: str) -> tuple[ConfirmationRecord, ...]:
        path = self._confirmations_path(model)
        if not path.exists():
            return ()
        records = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(ConfirmationRecord.from_dict(json.loads(line)))
        return tuple(records)

    def iter_object_records(self) -> tuple[ObjectEvidence, ...]:
        records: list[ObjectEvidence] = []
        for objects_dir in sorted(self._root.glob("*/_evidence/objects")):
            for path in sorted(objects_dir.glob("*.json")):
                records.append(ObjectEvidence.from_dict(json.loads(path.read_text())))
        return tuple(records)


def is_decision_stale(record: DecisionRecord, metadata: TableMetadata) -> bool:
    """True if recomputing the decision's structural fingerprint over current
    metadata differs from the stored one. A decision with no cited_table cannot be
    recomputed and is treated as not stale (contracts spec accepts this)."""
    if record.cited_table is None:
        return False
    cited = set(record.cited_columns)
    columns = {col.name: col.type for col in metadata.columns if col.name in cited}
    comments = {col.name: col.comment for col in metadata.columns if col.name in cited}
    current = evidence_fingerprint(columns, metadata.comment, comments)
    return current != record.evidence_fingerprint
