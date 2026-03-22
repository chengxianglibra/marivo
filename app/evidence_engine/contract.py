from __future__ import annotations

from abc import ABC
from typing import ClassVar

from app.evidence_engine.extractors.base import ObservationExtractor


class ExtractorContract(ObservationExtractor, ABC):
    """Extended extractor base with registry metadata ClassVars."""

    artifact_type: ClassVar[str]
    observation_types: ClassVar[list[str]]
    preconditions: ClassVar[list[str]]
