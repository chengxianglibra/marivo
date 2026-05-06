from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .ids import (
    ArtifactId,
    AssessmentId,
    EvidenceRef,
    FindingId,
    PropositionId,
    SessionId,
)


class Finding(BaseModel):
    finding_id: FindingId
    session_id: SessionId
    artifact_id: ArtifactId
    proposition_id: PropositionId | None = None
    finding_type: str
    content: dict[str, Any]
    invalidated: bool = False


class Proposition(BaseModel):
    proposition_id: PropositionId
    session_id: SessionId
    identity_key: str
    description: str | None = None
    externally_visible_assessment: str | None = None
    invalidated: bool = False


class Assessment(BaseModel):
    assessment_id: AssessmentId
    proposition_id: PropositionId
    status: str
    rationale: str | None = None
    snapshot_seq: int = 0


class Evidence(BaseModel):
    """Container for a coherent evidence unit."""

    ref: EvidenceRef
    findings: list[Finding]
    proposition: Proposition | None = None
    assessment: Assessment | None = None
