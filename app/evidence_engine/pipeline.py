from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.evidence_engine.schemas import Claim, Observation, Recommendation


Synthesizer = Callable[[list[Observation]], tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]]


class EvidencePipeline:
    """Small compatibility seam before the full evidence split lands."""

    def __init__(self, synthesizer: Synthesizer) -> None:
        self._synthesizer = synthesizer

    def synthesize(
        self,
        observations: list[Observation],
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
        return self._synthesizer(observations)
