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
    from marivo.datasource.metadata import TableMetadata


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
    cited_source: dict[str, object] | None = None
    cited_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.chosen is None:
            raise ValueError("DecisionRecord.chosen must not be None")
        _validate_blast_radius(self.blast_radius)

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
            "cited_source": self.cited_source,
            "cited_columns": list(self.cited_columns),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> DecisionRecord:
        cited_columns_raw = data.get("cited_columns", [])
        cited_source_raw = data.get("cited_source")
        return cls(
            decision_kind=str(data["decision_kind"]),
            chosen=data["chosen"],
            agreement_confidence=str(data["agreement_confidence"]),
            qualifying_sources=tuple(str(s) for s in data["qualifying_sources"]),  # type: ignore[attr-defined]
            materiality=str(data["materiality"]),
            blast_radius=_validate_blast_radius(data["blast_radius"]),
            evidence_fingerprint=str(data["evidence_fingerprint"]),
            question_id=None if data["question_id"] is None else str(data["question_id"]),
            decided_at=str(data["decided_at"]),
            cited_source=(
                dict(cited_source_raw) if isinstance(cited_source_raw, Mapping) else None
            ),
            cited_columns=tuple(str(c) for c in cited_columns_raw),  # type: ignore[attr-defined]
        )


def _validate_blast_radius(value: object, *, field: str = "DecisionRecord.blast_radius") -> int:
    if type(value) is not int:
        raise TypeError(
            f"{field} must be a non-negative int; got {type(value).__name__}: {value!r}"
        )
    if value < 0:
        raise ValueError(f"{field} must be a non-negative int; got {value!r}")
    return value


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


def _read_object_evidence(path: Path) -> ObjectEvidence:
    try:
        return ObjectEvidence.from_dict(json.loads(path.read_text()))
    except TypeError as exc:
        raise TypeError(f"Invalid evidence ledger object at {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"Invalid evidence ledger object at {path}: {exc}") from exc
    except KeyError as exc:
        raise KeyError(f"Invalid evidence ledger object at {path}: missing field {exc}") from exc


class LedgerStore:
    """Canonical-JSON file IO under <state_root>/evidence/<model>/."""

    def __init__(self, state_root: str | Path) -> None:
        self._root = Path(state_root)

    def _evidence_dir(self, model: str) -> Path:
        return self._root / "evidence" / model

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
        return _read_object_evidence(path)

    def iter_object_records(self) -> tuple[ObjectEvidence, ...]:
        records: list[ObjectEvidence] = []
        for objects_dir in sorted(self._root.glob("evidence/*/objects")):
            for path in sorted(objects_dir.glob("*.json")):
                records.append(_read_object_evidence(path))
        return tuple(records)

    def record_decision(self, semantic_id: str, record: DecisionRecord) -> None:
        """Append a decision record to the object evidence for *semantic_id*.

        Creates the object evidence file if it does not already exist.
        """
        obj = self.read_object(semantic_id)
        if obj is None:
            obj = ObjectEvidence(
                semantic_id=semantic_id,
                authored_at=record.decided_at,
                decisions=(record,),
                rejected_candidates=(),
            )
        else:
            obj = ObjectEvidence(
                semantic_id=obj.semantic_id,
                authored_at=obj.authored_at,
                decisions=(*obj.decisions, record),
                rejected_candidates=obj.rejected_candidates,
            )
        self.write_object(obj)

    def write_rejected_candidate(self, candidate: RejectedCandidate) -> None:
        """Persist a rejected candidate alongside the relevant object evidence.

        The candidate is stored under a dedicated rejected-candidates file keyed
        by its candidate name so that list_rejected_candidates can enumerate
        them without scanning every object evidence file.
        """
        path = self._rejected_candidates_path()
        existing = list(self.list_rejected_candidates())
        existing.append(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rejected_candidates": [c.to_dict() for c in existing]}
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

    def list_rejected_candidates(self) -> tuple[RejectedCandidate, ...]:
        """Return all rejected candidates recorded in this project."""
        path = self._rejected_candidates_path()
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return ()
        raw = payload.get("rejected_candidates", [])
        return tuple(RejectedCandidate.from_dict(item) for item in raw)

    def _rejected_candidates_path(self) -> Path:
        return self._root / "evidence" / "rejected_candidates.json"


def is_decision_stale(record: DecisionRecord, metadata: TableMetadata) -> bool:
    """True if recomputing the decision's structural fingerprint over current
    metadata differs from the stored one. A decision with no cited_source cannot be
    recomputed and is treated as not stale (contracts spec accepts this)."""
    if record.cited_source is None:
        return False
    cited = set(record.cited_columns)
    columns = {col.name: col.type for col in metadata.columns if col.name in cited}
    comments = {col.name: col.comment for col in metadata.columns if col.name in cited}
    current = evidence_fingerprint(columns, metadata.comment, comments)
    return current != record.evidence_fingerprint
