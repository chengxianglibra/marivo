"""Canonical evidence repositories for the Factum evidence pipeline.

Provides a typed read/write seam for all canonical evidence objects:

    artifact -> finding -> proposition -> assessment -> action proposal

Each repository wraps a ``MetadataStore`` and handles JSON
serialization/deserialization for the relevant table columns.

Design conventions (matching ``repositories.py``):
- ``create()`` accepts a pre-built dict where JSON columns are already
  ``json.dumps()``-serialized strings.
- ``get()`` / ``list_*()`` return dicts with JSON columns
  ``json.loads()``-deserialized back to Python objects.
- ``FindingRepository.create()`` is idempotent (INSERT OR IGNORE) because
  the ``findings`` table has a UNIQUE index on
  ``(artifact_id, finding_type, canonical_item_key)``.
- ``PropositionRepository.create()`` requires an ``identity_key`` field for
  deduplication (added to propositions via migration in schema.py).

Phase: 4b-1
"""

from __future__ import annotations

import json
from typing import Any

from app.storage.metadata import MetadataStore

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_JSON_FIELDS_FINDING = (
    "step_ref_json",
    "subject_json",
    "observed_window_json",
    "quality_json",
    "provenance_json",
    "payload_json",
)

_JSON_FIELDS_PROPOSITION = (
    "subject_json",
    "origin_json",
    "assessment_anchor_json",
    "lineage_json",
    "seed_finding_refs_json",
    "payload_json",
)

_JSON_FIELDS_ASSESSMENT = (
    "confidence_rationale_json",
    "supporting_finding_ids_json",
    "opposing_finding_ids_json",
    "gap_memberships_json",
    "applied_inference_record_ids_json",
    "payload_json",
)

_JSON_FIELDS_ACTION_PROPOSAL = (
    "primary_assessment_ref_json",
    "related_assessment_refs_json",
    "target_proposition_ref_json",
    "proposal_context_json",
    "priority_axes_json",
    "rationale_json",
    "payload_json",
)

_JSON_FIELDS_EVIDENCE_GAP = (
    "missing_requirement_json",
    "satisfiable_by_json",
    "related_finding_ids_json",
)

_JSON_FIELDS_INFERENCE_RECORD = (
    "input_finding_ids_json",
    "input_assessment_ids_json",
    "opened_gap_ids_json",
    "resolved_gap_ids_json",
    "produced_status_transition_json",
    "confidence_contribution_json",
    "justification_json",
)


def _deserialize(row: dict[str, Any], json_fields: tuple[str, ...]) -> dict[str, Any]:
    """Return a copy of *row* with the named JSON columns parsed to Python objects."""
    result = dict(row)
    for field in json_fields:
        if field in result and result[field] is not None:
            result[field] = json.loads(result[field])
    return result


# ---------------------------------------------------------------------------
# FindingRepository
# ---------------------------------------------------------------------------


class FindingRepository:
    """Repository for the ``findings`` table (Phase 4b-1).

    ``create()`` is idempotent: the ``findings`` table has a
    UNIQUE constraint on ``(artifact_id, finding_type, canonical_item_key)``,
    so inserting the same finding twice is silently ignored (INSERT OR IGNORE).
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, finding: dict[str, Any]) -> None:
        """Persist *finding* to the store (idempotent on finding_id and item key)."""
        self.metadata.execute(
            """
            INSERT OR IGNORE INTO findings (
                finding_id,
                session_id,
                artifact_id,
                step_ref_json,
                finding_type,
                canonical_item_key,
                subject_json,
                observed_window_json,
                quality_json,
                provenance_json,
                payload_json,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                finding["finding_id"],
                finding["session_id"],
                finding["artifact_id"],
                finding["step_ref_json"],
                finding["finding_type"],
                finding.get("canonical_item_key", ""),
                finding["subject_json"],
                finding.get("observed_window_json"),
                finding["quality_json"],
                finding["provenance_json"],
                finding["payload_json"],
                finding.get("schema_version", "v1"),
            ],
        )

    def get(self, finding_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM findings WHERE finding_id = ?", [finding_id])
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_FINDING)

    def list_by_artifact(self, artifact_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM findings WHERE artifact_id = ? ORDER BY created_at ASC",
            [artifact_id],
        )
        return [_deserialize(r, _JSON_FIELDS_FINDING) for r in rows]

    def list_by_session(
        self,
        session_id: str,
        *,
        finding_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM findings WHERE session_id = ?"
        params: list[Any] = [session_id]
        if finding_type is not None:
            query += " AND finding_type = ?"
            params.append(finding_type)
        query += " ORDER BY created_at ASC"
        rows = self.metadata.query_rows(query, params)
        return [_deserialize(r, _JSON_FIELDS_FINDING) for r in rows]

    def list_by_proposition_seed(self, proposition_id: str) -> list[dict[str, Any]]:
        """Return all findings that were used as seeds for *proposition_id*.

        Joins ``proposition_seed_finding_refs`` to retrieve the linked findings.
        """
        rows = self.metadata.query_rows(
            """
            SELECT f.*
            FROM findings f
            JOIN proposition_seed_finding_refs psr ON psr.finding_id = f.finding_id
            WHERE psr.proposition_id = ?
            ORDER BY psr.id ASC
            """,
            [proposition_id],
        )
        return [_deserialize(r, _JSON_FIELDS_FINDING) for r in rows]


# ---------------------------------------------------------------------------
# PropositionRepository
# ---------------------------------------------------------------------------


class PropositionRepository:
    """Repository for the ``propositions`` table (Phase 4b-1).

    ``identity_key`` is a caller-supplied normalized key for deduplication
    (see Phase 4e-2).  Stored in the ``identity_key`` column which is added
    via migration in ``schema.py``.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, proposition: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT OR IGNORE INTO propositions (
                proposition_id,
                session_id,
                proposition_type,
                subject_json,
                origin_json,
                assessment_anchor_json,
                lineage_json,
                seed_finding_refs_json,
                payload_json,
                schema_version,
                identity_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                proposition["proposition_id"],
                proposition["session_id"],
                proposition["proposition_type"],
                proposition["subject_json"],
                proposition["origin_json"],
                proposition["assessment_anchor_json"],
                proposition["lineage_json"],
                proposition.get("seed_finding_refs_json", "[]"),
                proposition.get("payload_json", "{}"),
                proposition.get("schema_version", "v1"),
                proposition.get("identity_key", ""),
            ],
        )

    def get(self, proposition_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM propositions WHERE proposition_id = ?", [proposition_id]
        )
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_PROPOSITION)

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM propositions WHERE session_id = ? ORDER BY created_at ASC",
            [session_id],
        )
        return [_deserialize(r, _JSON_FIELDS_PROPOSITION) for r in rows]

    def get_by_identity_key(
        self,
        session_id: str,
        proposition_type: str,
        identity_key: str,
    ) -> dict[str, Any] | None:
        """Look up an existing proposition by its normalized identity key.

        Used by Phase 4e-2 proposition registration to deduplicate
        system-seeded propositions within a session.
        """
        row = self.metadata.query_one(
            """
            SELECT * FROM propositions
            WHERE session_id = ?
              AND proposition_type = ?
              AND identity_key = ?
            """,
            [session_id, proposition_type, identity_key],
        )
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_PROPOSITION)

    def add_seed_finding_refs(
        self,
        proposition_id: str,
        refs: list[dict[str, Any]],
    ) -> None:
        """Append seed finding refs to the ``proposition_seed_finding_refs`` junction table.

        Each entry in *refs* must have ``finding_id`` (str) and ``role`` (str) keys.
        The junction table has a UNIQUE(proposition_id, finding_id) constraint so
        duplicate inserts are silently ignored.

        **Important**: this method writes only to the junction table.  It does NOT
        update ``propositions.seed_finding_refs_json``.  That JSON blob is
        authoritative for the seed set at creation time and is not modified after
        ``PropositionRepository.create()`` is called.  The junction table is the
        live index for reverse lookups ("which propositions were seeded by finding X?").
        See the schema comment in ``schema.py`` for the two-surface design.
        """
        for ref in refs:
            self.metadata.execute(
                """
                INSERT OR IGNORE INTO proposition_seed_finding_refs
                    (proposition_id, finding_id, role)
                VALUES (?, ?, ?)
                """,
                [proposition_id, ref["finding_id"], ref.get("role", "primary")],
            )

    def get_seed_finding_refs(self, proposition_id: str) -> list[dict[str, Any]]:
        """Return the seed finding ref rows for *proposition_id* from the junction table."""
        return self.metadata.query_rows(
            """
            SELECT finding_id, role, created_at
            FROM proposition_seed_finding_refs
            WHERE proposition_id = ?
            ORDER BY id ASC
            """,
            [proposition_id],
        )

    def list_seeded_proposition_ids(self, finding_id: str) -> list[str]:
        """Return proposition_ids that were seeded by *finding_id*.

        Used by seeding-run tracking (Phase 4e-3).
        """
        rows = self.metadata.query_rows(
            "SELECT proposition_id FROM proposition_seed_finding_refs WHERE finding_id = ?",
            [finding_id],
        )
        return [r["proposition_id"] for r in rows]


# ---------------------------------------------------------------------------
# AssessmentRepository
# ---------------------------------------------------------------------------


class AssessmentRepository:
    """Repository for the ``assessments`` table (Phase 4b-1).

    Assessments are immutable snapshots; ``get_latest`` returns the snapshot
    with the highest ``snapshot_seq`` for a given proposition.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, assessment: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO assessments (
                assessment_id,
                session_id,
                proposition_id,
                assessment_type,
                snapshot_seq,
                status,
                confidence_grade,
                confidence_rationale_json,
                supporting_finding_ids_json,
                opposing_finding_ids_json,
                gap_memberships_json,
                applied_inference_record_ids_json,
                supersedes_assessment_id,
                payload_json,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                assessment["assessment_id"],
                assessment["session_id"],
                assessment["proposition_id"],
                assessment["assessment_type"],
                assessment["snapshot_seq"],
                assessment["status"],
                assessment["confidence_grade"],
                assessment["confidence_rationale_json"],
                assessment.get("supporting_finding_ids_json", "[]"),
                assessment.get("opposing_finding_ids_json", "[]"),
                assessment.get("gap_memberships_json", "[]"),
                assessment.get("applied_inference_record_ids_json", "[]"),
                assessment.get("supersedes_assessment_id"),
                assessment.get("payload_json", "{}"),
                assessment.get("schema_version", "v1"),
            ],
        )

    def get(self, assessment_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM assessments WHERE assessment_id = ?", [assessment_id]
        )
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_ASSESSMENT)

    def get_latest(self, proposition_id: str) -> dict[str, Any] | None:
        """Return the assessment snapshot with the highest snapshot_seq.

        Returns ``None`` if no assessments exist for *proposition_id*.
        """
        row = self.metadata.query_one(
            """
            SELECT * FROM assessments
            WHERE proposition_id = ?
            ORDER BY snapshot_seq DESC
            LIMIT 1
            """,
            [proposition_id],
        )
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_ASSESSMENT)

    def list_by_proposition(self, proposition_id: str) -> list[dict[str, Any]]:
        """Return all snapshots ordered by snapshot_seq ascending."""
        rows = self.metadata.query_rows(
            """
            SELECT * FROM assessments
            WHERE proposition_id = ?
            ORDER BY snapshot_seq ASC
            """,
            [proposition_id],
        )
        return [_deserialize(r, _JSON_FIELDS_ASSESSMENT) for r in rows]

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM assessments WHERE session_id = ? ORDER BY created_at ASC",
            [session_id],
        )
        return [_deserialize(r, _JSON_FIELDS_ASSESSMENT) for r in rows]

    def next_snapshot_seq(self, proposition_id: str) -> int:
        """Return the next snapshot_seq for a new assessment on *proposition_id*.

        Returns 1 if no assessments exist yet.
        """
        row = self.metadata.query_one(
            "SELECT MAX(snapshot_seq) AS max_seq FROM assessments WHERE proposition_id = ?",
            [proposition_id],
        )
        if row is None or row["max_seq"] is None:
            return 1
        return int(row["max_seq"]) + 1


# ---------------------------------------------------------------------------
# ActionProposalRepository
# ---------------------------------------------------------------------------


class ActionProposalRepository:
    """Repository for the ``action_proposals`` table (Phase 4b-1)."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, proposal: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO action_proposals (
                action_proposal_id,
                session_id,
                action_kind,
                primary_assessment_ref_json,
                related_assessment_refs_json,
                target_proposition_ref_json,
                proposal_context_json,
                priority_axes_json,
                priority_rank,
                rationale_json,
                payload_json,
                policy_version,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                proposal["action_proposal_id"],
                proposal["session_id"],
                proposal["action_kind"],
                proposal["primary_assessment_ref_json"],
                proposal.get("related_assessment_refs_json", "[]"),
                proposal["target_proposition_ref_json"],
                proposal["proposal_context_json"],
                proposal.get("priority_axes_json", "[]"),
                proposal.get("priority_rank", 0.0),
                proposal["rationale_json"],
                proposal.get("payload_json", "{}"),
                proposal.get("policy_version", "v1"),
                proposal.get("schema_version", "v1"),
            ],
        )

    def get(self, action_proposal_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM action_proposals WHERE action_proposal_id = ?",
            [action_proposal_id],
        )
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_ACTION_PROPOSAL)

    def list_by_session(
        self,
        session_id: str,
        *,
        action_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return proposals ordered by priority_rank ascending (highest priority first)."""
        query = "SELECT * FROM action_proposals WHERE session_id = ?"
        params: list[Any] = [session_id]
        if action_kind is not None:
            query += " AND action_kind = ?"
            params.append(action_kind)
        query += " ORDER BY priority_rank ASC, created_at ASC, action_proposal_id ASC"
        rows = self.metadata.query_rows(query, params)
        return [_deserialize(r, _JSON_FIELDS_ACTION_PROPOSAL) for r in rows]

    def list_by_assessment(self, session_id: str, assessment_id: str) -> list[dict[str, Any]]:
        """Return proposals whose primary_assessment_ref matches *assessment_id*.

        Uses SQLite ``json_extract`` to query the JSON column without requiring
        a separate indexed column.  Results are ordered by the canonical default:
        ``priority_rank ASC, created_at ASC, action_proposal_id ASC``.
        """
        rows = self.metadata.query_rows(
            """
            SELECT * FROM action_proposals
            WHERE session_id = ?
              AND json_extract(primary_assessment_ref_json, '$.assessment_id') = ?
            ORDER BY priority_rank ASC, created_at ASC, action_proposal_id ASC
            """,
            [session_id, assessment_id],
        )
        return [_deserialize(r, _JSON_FIELDS_ACTION_PROPOSAL) for r in rows]

    def list_by_proposition(self, session_id: str, proposition_id: str) -> list[dict[str, Any]]:
        """Return proposals whose target_proposition_ref matches *proposition_id*.

        Uses SQLite ``json_extract`` to query the JSON column.  Results are
        ordered by the canonical default:
        ``priority_rank ASC, created_at ASC, action_proposal_id ASC``.
        """
        rows = self.metadata.query_rows(
            """
            SELECT * FROM action_proposals
            WHERE session_id = ?
              AND json_extract(target_proposition_ref_json, '$.proposition_id') = ?
            ORDER BY priority_rank ASC, created_at ASC, action_proposal_id ASC
            """,
            [session_id, proposition_id],
        )
        return [_deserialize(r, _JSON_FIELDS_ACTION_PROPOSAL) for r in rows]


# ---------------------------------------------------------------------------
# EvidenceGapRepository
# ---------------------------------------------------------------------------


class EvidenceGapRepository:
    """Repository for the ``evidence_gaps`` table.

    Provides the seam for Phase 4f-* (assessment recompute / gap tracking).
    Phase 4b-1 delivers the basic create/get/list interface.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, gap: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO evidence_gaps (
                gap_id,
                session_id,
                proposition_id,
                gap_kind,
                title,
                description,
                status,
                missing_requirement_json,
                satisfiable_by_json,
                related_finding_ids_json,
                opened_by_inference_record_id,
                resolved_by_inference_record_id,
                schema_version,
                resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                gap["gap_id"],
                gap["session_id"],
                gap["proposition_id"],
                gap["gap_kind"],
                gap.get("title", ""),
                gap.get("description", ""),
                gap.get("status", "open"),
                gap["missing_requirement_json"],
                gap.get("satisfiable_by_json", "[]"),
                gap.get("related_finding_ids_json", "[]"),
                gap["opened_by_inference_record_id"],
                gap.get("resolved_by_inference_record_id"),
                gap.get("schema_version", "v1"),
                gap.get("resolved_at"),
            ],
        )

    def get(self, gap_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one("SELECT * FROM evidence_gaps WHERE gap_id = ?", [gap_id])
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_EVIDENCE_GAP)

    def list_by_proposition(
        self,
        proposition_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evidence_gaps WHERE proposition_id = ?"
        params: list[Any] = [proposition_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC"
        rows = self.metadata.query_rows(query, params)
        return [_deserialize(r, _JSON_FIELDS_EVIDENCE_GAP) for r in rows]

    def list_by_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evidence_gaps WHERE session_id = ?"
        params: list[Any] = [session_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC"
        rows = self.metadata.query_rows(query, params)
        return [_deserialize(r, _JSON_FIELDS_EVIDENCE_GAP) for r in rows]

    def resolve(
        self,
        gap_id: str,
        *,
        resolved_by_inference_record_id: str,
        resolved_at: str,
    ) -> None:
        """Mark an open gap as resolved.

        Sets ``status = 'resolved'``, ``resolved_by_inference_record_id`` and
        ``resolved_at`` on the gap row.  The gap object is retained (not deleted)
        per the immutable-history contract.

        Raises :exc:`ValueError` if the gap does not exist or is already resolved.
        """
        existing = self.get(gap_id)
        if existing is None:
            raise ValueError(f"evidence_gap {gap_id!r} not found; cannot resolve.")
        if existing.get("status") == "resolved":
            raise ValueError(f"evidence_gap {gap_id!r} is already resolved.")
        self.metadata.execute(
            """
            UPDATE evidence_gaps
            SET status = 'resolved',
                resolved_by_inference_record_id = ?,
                resolved_at = ?
            WHERE gap_id = ?
            """,
            [resolved_by_inference_record_id, resolved_at, gap_id],
        )


# ---------------------------------------------------------------------------
# InferenceRecordRepository
# ---------------------------------------------------------------------------


class InferenceRecordRepository:
    """Repository for the ``inference_records`` table.

    Provides the seam for Phase 4f-* (assessment recompute / inference rules).
    Phase 4b-1 delivers the basic create/get/list interface.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def create(self, record: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO inference_records (
                inference_record_id,
                session_id,
                proposition_id,
                assessment_id,
                rule_id,
                rule_version,
                result,
                input_finding_ids_json,
                input_assessment_ids_json,
                opened_gap_ids_json,
                resolved_gap_ids_json,
                produced_status_transition_json,
                confidence_contribution_json,
                justification_json,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record["inference_record_id"],
                record["session_id"],
                record["proposition_id"],
                record["assessment_id"],
                record["rule_id"],
                record.get("rule_version", "v1"),
                record["result"],
                record.get("input_finding_ids_json", "[]"),
                record.get("input_assessment_ids_json", "[]"),
                record.get("opened_gap_ids_json", "[]"),
                record.get("resolved_gap_ids_json", "[]"),
                record.get("produced_status_transition_json"),
                record.get("confidence_contribution_json", "{}"),
                record.get("justification_json", "{}"),
                record.get("schema_version", "v1"),
            ],
        )

    def get(self, inference_record_id: str) -> dict[str, Any] | None:
        row = self.metadata.query_one(
            "SELECT * FROM inference_records WHERE inference_record_id = ?",
            [inference_record_id],
        )
        if row is None:
            return None
        return _deserialize(row, _JSON_FIELDS_INFERENCE_RECORD)

    def list_by_assessment(self, assessment_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT * FROM inference_records
            WHERE assessment_id = ?
            ORDER BY created_at ASC
            """,
            [assessment_id],
        )
        return [_deserialize(r, _JSON_FIELDS_INFERENCE_RECORD) for r in rows]

    def list_by_proposition(self, proposition_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT * FROM inference_records
            WHERE proposition_id = ?
            ORDER BY created_at ASC
            """,
            [proposition_id],
        )
        return [_deserialize(r, _JSON_FIELDS_INFERENCE_RECORD) for r in rows]


__all__ = [
    "ActionProposalRepository",
    "AssessmentRepository",
    "EvidenceGapRepository",
    "FindingRepository",
    "InferenceRecordRepository",
    "PropositionRepository",
]
