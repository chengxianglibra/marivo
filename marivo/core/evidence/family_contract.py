"""Family-level empty semantics contract for canonical finding extraction.

Extracted from ``marivo.evidence_engine.family_contract`` as part of Phase 3c.
All definitions here are pure: no I/O, no repository access.

Centralises Decision D4 from
``docs/analysis/evidence-engine/artifact-finding-generation-rules.md``:

    D4 (approved): empty finding set legality is per-family, not a global rule.
      - observe / detect : allow committed success-empty
      - compare / decompose / correlate / test / forecast : success must be non-empty
"""

from __future__ import annotations

from typing import Literal

ArtifactFamily = Literal[
    "observe",
    "compare",
    "decompose",
    "detect",
    "correlate",
    "test",
    "forecast",
]

FAMILY_ALLOWS_EMPTY: dict[ArtifactFamily, bool] = {
    "observe": True,
    "detect": True,
    "compare": False,
    "decompose": False,
    "correlate": False,
    "test": False,
    "forecast": False,
}

ALLOWS_EMPTY_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        "observation",
        "anomaly_candidates",
    }
)


class FamilyEmptyError(ValueError):
    def __init__(self, family: str) -> None:
        super().__init__(
            f"Artifact family '{family}' requires at least 1 finding (got 0). "
            "Per D4, only 'observe' and 'detect' allow success-empty committed "
            "finding sets."
        )
        self.family = family


def check_finding_count(family: str, count: int) -> None:
    if count <= 0 and not FAMILY_ALLOWS_EMPTY.get(family, False):  # type: ignore[call-overload]
        raise FamilyEmptyError(family)


__all__ = [
    "ALLOWS_EMPTY_ARTIFACT_TYPES",
    "FAMILY_ALLOWS_EMPTY",
    "ArtifactFamily",
    "FamilyEmptyError",
    "check_finding_count",
]
