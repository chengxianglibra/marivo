"""Family-level empty semantics contract for canonical finding extraction.

This module centralises Decision D4 from
``docs/analysis/evidence-engine/artifact-finding-generation-rules.md``:

    D4 (approved): empty finding set legality is per-family, not a global rule.
      - observe / detect : allow committed success-empty
      - compare / decompose / correlate / test / forecast : success must be non-empty

All commit-path code that needs to enforce this rule should call
``check_finding_count`` rather than duplicating the per-family logic.

## Relationship to Other Modules

- ``canonical_finding.py`` defines the finding types and ``FindingExtractionResult``
  that extractors produce.
- ``family_contract.py`` (this file) validates whether the extractor's
  ``finding_count`` is a legal outcome for the given artifact family.
- ``app/evidence_engine/`` (4b-2 registry, 4c-* commit path) will import both.
"""

from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# ArtifactFamily type alias
# ---------------------------------------------------------------------------

ArtifactFamily = Literal[
    "observe",
    "compare",
    "decompose",
    "detect",
    "correlate",
    "test",
    "forecast",
]

# ---------------------------------------------------------------------------
# FAMILY_ALLOWS_EMPTY: machine-readable D4 contract
#
# True  → committed success-empty finding set is a legal outcome
# False → success requires at least 1 committed finding
# ---------------------------------------------------------------------------

FAMILY_ALLOWS_EMPTY: dict[ArtifactFamily, bool] = {
    "observe": True,  # scope resolved + executed; no canonical value/bucket/segment = legal
    "detect": True,  # scan complete; total_candidate_count = 0 = legal
    "compare": False,  # must produce ≥1 delta finding or fail with not_comparable
    "decompose": False,  # must produce ≥1 decomposition_item or fail with not_attributable
    "correlate": False,  # must produce exactly 1 correlation_result finding
    "test": False,  # must produce exactly 1 test_result finding
    "forecast": False,  # must produce ≥1 forecast_point finding
}


# ---------------------------------------------------------------------------
# FamilyEmptyError
# ---------------------------------------------------------------------------


class FamilyEmptyError(ValueError):
    """Raised by ``check_finding_count`` when a mandatory-non-empty family
    produces zero findings.

    Attributes
    ----------
    family : str
        The artifact family that violated the contract.
    """

    def __init__(self, family: str) -> None:
        super().__init__(
            f"Artifact family '{family}' requires at least 1 finding (got 0). "
            "Per D4, only 'observe' and 'detect' allow success-empty committed "
            "finding sets."
        )
        self.family = family


# ---------------------------------------------------------------------------
# check_finding_count
# ---------------------------------------------------------------------------


def check_finding_count(family: str, count: int) -> None:
    """Validate *count* against the family-level empty-semantics contract.

    Parameters
    ----------
    family:
        The artifact family string (e.g. ``"compare"``).  Unknown families
        are treated as **non-empty required** (fail-safe default).
    count:
        The number of findings produced by the extractor
        (``FindingExtractionResult.finding_count``).

    Raises
    ------
    FamilyEmptyError
        If *count* is 0 or negative and the family does **not** allow
        success-empty.

    Notes
    -----
    This function is a no-op when *count* > 0 regardless of family.
    It is also a no-op when *family* is ``"observe"`` or ``"detect"`` and
    *count* is 0 or negative — those families explicitly allow success-empty
    outcomes.
    """
    if count <= 0 and not FAMILY_ALLOWS_EMPTY.get(family, False):  # type: ignore[call-overload]
        raise FamilyEmptyError(family)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "FAMILY_ALLOWS_EMPTY",
    "ArtifactFamily",
    "FamilyEmptyError",
    "check_finding_count",
]
