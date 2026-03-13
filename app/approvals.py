"""Compatibility facade for approval runtime."""

from __future__ import annotations

from app.governance_engine import ApprovalRuntime, GovernanceRepository
from app.storage.metadata import MetadataStore


class ApprovalService(ApprovalRuntime):
    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        repository = GovernanceRepository(metadata)
        self.repository = repository
        super().__init__(repository)
