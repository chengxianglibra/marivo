from __future__ import annotations

from app.evidence import synthesize_claims
from app.evidence_engine.schemas import Claim, Observation, Recommendation
from app.evidence_engine.synthesizers.base import ClaimSynthesizer


class DefaultClaimSynthesizer(ClaimSynthesizer):
    name = "default"

    def synthesize(
        self,
        observations: list[Observation],
    ) -> tuple[list[Claim], list[Recommendation], list[dict]]:
        return synthesize_claims(observations)
