from __future__ import annotations

from abc import ABC, abstractmethod

from app.evidence_engine.schemas import Claim, Observation, Recommendation


class ClaimSynthesizer(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def synthesize(
        self,
        observations: list[Observation],
    ) -> tuple[list[Claim], list[Recommendation], list[dict]]:
        raise NotImplementedError
