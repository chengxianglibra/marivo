from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.evidence import Assessment, Evidence, Finding, Proposition
from app.contracts.ids import EvidenceRef
from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)

logger = logging.getLogger(__name__)


class MetadataEvidenceStoreAdapter:
    """Wraps evidence repositories -> ``EvidenceStore``.

    Delegates write/read to the existing repository classes for findings,
    propositions, and assessments.

    Phase 3a: ``write`` stores evidence by persisting its findings and
    proposition/assessment via the respective repositories. ``read`` is
    a minimal bridge that reconstructs an ``Evidence`` from stored data.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        proposition_repo: PropositionRepository,
        assessment_repo: AssessmentRepository,
        gap_repo: EvidenceGapRepository | None = None,
        inference_repo: InferenceRecordRepository | None = None,
        action_proposal_repo: ActionProposalRepository | None = None,
    ) -> None:
        self._finding_repo = finding_repo
        self._proposition_repo = proposition_repo
        self._assessment_repo = assessment_repo
        self._gap_repo = gap_repo
        self._inference_repo = inference_repo
        self._action_proposal_repo = action_proposal_repo

    def write(self, evidence: Evidence) -> EvidenceRef:
        """Persist evidence by writing its findings and proposition/assessment.

        Each finding is created via the FindingRepository (idempotent).
        If a proposition is present, it is created via the PropositionRepository.
        If an assessment is present, it is created via the AssessmentRepository.

        Returns a deterministic SHA-256 ref computed from the evidence content
        (excluding the ref field itself), consistent with FileEvidenceStore.
        """
        ref = self._compute_ref(evidence)

        for finding in evidence.findings:
            # Ensure the artifact row exists (FK constraint on findings)
            self._ensure_artifact(finding)
            try:
                self._finding_repo.create(self._finding_to_storage_dict(finding, ref))
            except Exception:
                logger.debug("Finding %s may already exist (idempotent)", finding.finding_id)

        if evidence.proposition is not None:
            try:
                self._proposition_repo.create(
                    self._proposition_to_storage_dict(evidence.proposition)
                )
            except Exception:
                logger.debug(
                    "Proposition %s may already exist", evidence.proposition.proposition_id
                )

        if evidence.assessment is not None:
            try:
                self._assessment_repo.create(self._assessment_to_storage_dict(evidence.assessment))
            except Exception:
                logger.debug("Assessment %s may already exist", evidence.assessment.assessment_id)

        return ref

    def _ensure_artifact(self, finding: Finding) -> None:
        """Insert a stub artifact row if one does not already exist.

        The ``findings`` table has a FK constraint on ``artifact_id``,
        so the referenced artifact must exist before a finding can be
        inserted.  In the normal server flow, artifacts are created by the
        analysis pipeline before findings are extracted; here we ensure a
        minimal stub exists for standalone writes.
        """
        metadata = self._finding_repo.metadata
        existing = metadata.query_one(
            "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
            [finding.artifact_id],
        )
        if existing is None:
            metadata.insert_ignore(
                "artifacts",
                [
                    "artifact_id",
                    "session_id",
                    "step_id",
                    "artifact_type",
                    "name",
                    "content_json",
                    "lifecycle",
                ],
                [
                    finding.artifact_id,
                    finding.session_id,
                    "",
                    finding.finding_type,
                    f"evidence-{finding.finding_id}",
                    json.dumps({}),
                    "committed",
                ],
            )

    def read(self, ref: EvidenceRef) -> Evidence:
        """Reconstruct an Evidence object from SQL tables using the ref.

        Looks up findings by ``canonical_item_key`` (which stores the ref),
        then reconstructs the linked proposition and assessment if present.
        """
        ref_str = str(ref)
        finding_rows = self._finding_repo.metadata.query_rows(
            "SELECT * FROM findings WHERE canonical_item_key = ? LIMIT 1",
            [ref_str],
        )
        if not finding_rows:
            raise NotFoundError(
                code=ErrorCode.EVIDENCE_NOT_FOUND,
                message=f"Evidence not found for ref: {ref_str}",
            )

        row = finding_rows[0]
        content = row.get("payload_json", {})
        if isinstance(content, str):
            content = json.loads(content)
        finding = Finding(
            finding_id=row["finding_id"],
            session_id=row["session_id"],
            artifact_id=row["artifact_id"],
            finding_type=row.get("finding_type", "unknown"),
            proposition_id=row.get("proposition_id"),
            content=content,
        )

        proposition = None
        if finding.proposition_id:
            prop_row = self._proposition_repo.get(finding.proposition_id)
            if prop_row:
                prop_payload = prop_row.get("payload_json", {})
                if isinstance(prop_payload, str):
                    prop_payload = json.loads(prop_payload)
                proposition = Proposition(
                    proposition_id=prop_row["proposition_id"],
                    session_id=prop_row.get("session_id", ""),
                    description=prop_payload.get("description", ""),
                    identity_key=prop_row.get("identity_key", ""),
                )

        assessment = None
        if proposition and proposition.proposition_id:
            latest_assessment = self._assessment_repo.get_latest(proposition.proposition_id)
            if latest_assessment:
                rationale_json = latest_assessment.get("confidence_rationale_json", {})
                rationale = None
                if isinstance(rationale_json, str):
                    parsed = json.loads(rationale_json)
                    rationale = parsed.get("rationale")
                elif isinstance(rationale_json, dict):
                    rationale = rationale_json.get("rationale")
                assessment = Assessment(
                    assessment_id=latest_assessment["assessment_id"],
                    proposition_id=proposition.proposition_id,
                    snapshot_seq=latest_assessment.get("snapshot_seq", 0),
                    status=latest_assessment.get("status", "unknown"),
                    rationale=rationale,
                )

        return Evidence(
            ref=ref,
            findings=[finding],
            proposition=proposition,
            assessment=assessment,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_ref(evidence: Evidence) -> EvidenceRef:
        """Compute a deterministic SHA-256 ref from evidence content.

        Excludes the ``ref`` field itself (mirrors FileEvidenceStore).
        """
        dump = evidence.model_dump(mode="json")
        dump.pop("ref", None)
        canonical = json.dumps(dump, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return EvidenceRef(hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    @staticmethod
    def _finding_to_storage_dict(
        finding: Finding, ref: EvidenceRef | None = None
    ) -> dict[str, Any]:
        """Convert a domain Finding to a storage-compatible dict."""
        import json

        return {
            "finding_id": finding.finding_id,
            "session_id": finding.session_id,
            "artifact_id": finding.artifact_id,
            "step_ref_json": json.dumps({}),
            "finding_type": finding.finding_type,
            "canonical_item_key": str(ref) if ref else finding.finding_id,
            "subject_json": json.dumps({}),
            "observed_window_json": None,
            "quality_json": json.dumps({}),
            "provenance_json": json.dumps({}),
            "payload_json": json.dumps(finding.content),
            "schema_version": "v1",
            "proposition_id": finding.proposition_id,
        }

    @staticmethod
    def _proposition_to_storage_dict(proposition: Proposition) -> dict[str, Any]:
        """Convert a domain Proposition to a storage-compatible dict."""
        import json

        return {
            "proposition_id": proposition.proposition_id,
            "session_id": proposition.session_id,
            "proposition_type": "generic",
            "subject_json": json.dumps({}),
            "origin_json": json.dumps({}),
            "assessment_anchor_json": json.dumps({}),
            "lineage_json": json.dumps({}),
            "seed_finding_refs_json": "[]",
            "payload_json": json.dumps({"description": proposition.description}),
            "schema_version": "v1",
            "identity_key": proposition.identity_key,
        }

    @staticmethod
    def _assessment_to_storage_dict(assessment: Assessment) -> dict[str, Any]:
        """Convert a domain Assessment to a storage-compatible dict."""
        import json

        return {
            "assessment_id": assessment.assessment_id,
            "session_id": "",
            "proposition_id": assessment.proposition_id,
            "assessment_type": "auto",
            "snapshot_seq": assessment.snapshot_seq,
            "status": assessment.status,
            "confidence_grade": None,
            "confidence_rationale_json": json.dumps(
                {"rationale": assessment.rationale} if assessment.rationale else {}
            ),
            "supporting_finding_ids_json": "[]",
            "opposing_finding_ids_json": "[]",
            "gap_memberships_json": "[]",
            "applied_inference_record_ids_json": "[]",
            "supersedes_assessment_id": None,
            "payload_json": "{}",
            "schema_version": "v1",
        }
