from __future__ import annotations

from typing import Any

from app.evidence_engine.schemas import Claim, Observation, Recommendation
from app.evidence_engine.synthesizers.base import ClaimSynthesizer
from app.evidence_engine.synthesizers.stages import PipelineAuditLog
from app.evidence_engine.synthesizers.three_stage_pipeline import ThreeStagePipeline


class DefaultClaimSynthesizer(ClaimSynthesizer):
    name = "default"

    def __init__(self) -> None:
        self._pipeline = ThreeStagePipeline()
        self.last_audit_log: PipelineAuditLog | None = None

    def synthesize(
        self,
        observations: list[Observation],
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
        claims, recs, edges, audit_log = self._pipeline.run(observations)
        self.last_audit_log = audit_log
        return claims, recs, edges
