"""Family-level empty semantics contract — re-export shim.

Pure logic extracted to ``marivo.core.evidence.family_contract`` in Phase 3c.
This module re-exports for backward compatibility during the drain.
"""

from marivo.core.evidence.family_contract import (
    ALLOWS_EMPTY_ARTIFACT_TYPES,
    FAMILY_ALLOWS_EMPTY,
    ArtifactFamily,
    FamilyEmptyError,
    check_finding_count,
)

__all__ = [
    "ALLOWS_EMPTY_ARTIFACT_TYPES",
    "FAMILY_ALLOWS_EMPTY",
    "ArtifactFamily",
    "FamilyEmptyError",
    "check_finding_count",
]
