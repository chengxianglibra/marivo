"""Private render budget for complete Artifact mechanical contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactContractRenderBudget:
    """Closed limits for one complete ``ArtifactContract`` rendering."""

    max_lines: int
    max_codepoints: int
    max_affordances: int


ARTIFACT_CONTRACT_RENDER_BUDGET = ArtifactContractRenderBudget(
    max_lines=120,
    max_codepoints=12_000,
    max_affordances=24,
)


__all__ = ["ARTIFACT_CONTRACT_RENDER_BUDGET", "ArtifactContractRenderBudget"]
