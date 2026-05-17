from __future__ import annotations

from typing import Any

from marivo.adapters.metadata import MetadataStore
from marivo.adapters.server.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)


def build_evidence_repos(metadata_store: MetadataStore) -> dict[str, Any]:
    """Build canonical evidence repositories over a shared MetadataStore."""
    return {
        "proposition_repo": PropositionRepository(metadata_store),
        "assessment_repo": AssessmentRepository(metadata_store),
        "finding_repo": FindingRepository(metadata_store),
        "gap_repo": EvidenceGapRepository(metadata_store),
        "inference_record_repo": InferenceRecordRepository(metadata_store),
        "proposal_repo": ActionProposalRepository(metadata_store),
    }
