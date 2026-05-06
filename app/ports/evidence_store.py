from __future__ import annotations

from typing import Protocol

from app.contracts.evidence import Evidence
from app.contracts.ids import EvidenceRef


class EvidenceStore(Protocol):
    def write(self, evidence: Evidence) -> EvidenceRef: ...
    def read(self, ref: EvidenceRef) -> Evidence: ...
