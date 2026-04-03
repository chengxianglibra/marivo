"""Version bump classification and migration-status vocabulary (Phase 4h-1).

Implements the three-class taxonomy defined in
``docs/analysis/evidence-engine/migration-and-invalidation.md §Version Bump Classes``.

Version axes
------------
The Evidence Engine tracks multiple independent version axes, each with a
different *bump class* that determines the required runtime response:

``forward_compatible``
    Only affects new writes; old objects remain valid without replay.
``replay_required``
    Existing canonical output may differ under the new logic; ``latest`` /
    live results must be replayed before they can be considered current.
``identity_breaking``
    The identity normalization boundary changes; new and old objects must
    not be mixed in the same externally visible bundle.

Migration status labels
-----------------------
``migration_required``, ``migration_in_progress``, and ``migration_blocked``
are **runtime truth only**.  They must never be stored in ``session``,
``state``, or ``context`` canonical objects (see
``migration-and-invalidation.md §Session Migration Boundary``).
:data:`MIGRATION_STATUS_LABELS` enumerates them for validation use.

Phase: 4h-1
"""

from __future__ import annotations

from typing import Literal, TypedDict

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

VersionBumpClass = Literal["forward_compatible", "replay_required", "identity_breaking"]


class VersionAxisDeclaration(TypedDict):
    """Declaration of a single version axis and its bump semantics.

    Fields
    ------
    axis:
        Machine-readable axis name (e.g. ``"extractor_version"``).
    current_version:
        Version string active in this build (e.g. ``"v1"``).
    bump_class_on_change:
        The :data:`VersionBumpClass` that applies whenever this axis is
        incremented.
    description:
        Human-readable description of what this axis governs.
    """

    axis: str
    current_version: str
    bump_class_on_change: VersionBumpClass
    description: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

VERSION_AXES: list[VersionAxisDeclaration] = [
    VersionAxisDeclaration(
        axis="artifact_schema_version",
        current_version="v1",
        bump_class_on_change="forward_compatible",
        description=(
            "Schema version of an artifact payload.  Shape changes only affect "
            "new writes; existing artifacts remain valid without replay."
        ),
    ),
    VersionAxisDeclaration(
        axis="extractor_version",
        current_version="v1",
        bump_class_on_change="replay_required",
        description=(
            "Version of the FindingExtractor implementation.  A bump may change "
            "the canonical payload of findings produced from the same artifact, "
            "so live results must be replayed."
        ),
    ),
    VersionAxisDeclaration(
        axis="template_version",
        current_version="v1",
        bump_class_on_change="identity_breaking",
        description=(
            "Version of a proposition seed template.  A bump can alter the "
            "proposition type or subject shape, which changes the identity "
            "normalization boundary."
        ),
    ),
    VersionAxisDeclaration(
        axis="derivation_version",
        current_version="v1",
        bump_class_on_change="identity_breaking",
        description=(
            "Version that governs the proposition identity boundary itself "
            "(``normalize_proposition_identity`` inputs).  Any bump creates a "
            "new identity space; old and new propositions must not share an "
            "externally visible bundle."
        ),
    ),
    VersionAxisDeclaration(
        axis="rule_version",
        current_version="v1",
        bump_class_on_change="replay_required",
        description=(
            "Version of the assessment rule registry.  A bump may change "
            "judgment outcomes for the same evidence context, so "
            "assessments must be replayed."
        ),
    ),
    VersionAxisDeclaration(
        axis="policy_version",
        current_version="v1",
        bump_class_on_change="replay_required",
        description=(
            "Version of the action proposal policy engine.  A bump may change "
            "which proposals are generated or their ranking, requiring a "
            "proposal refresh."
        ),
    ),
]

# Keyed lookup for O(1) access by axis name.
_AXIS_INDEX: dict[str, VersionAxisDeclaration] = {d["axis"]: d for d in VERSION_AXES}

# ---------------------------------------------------------------------------
# Migration status labels (runtime truth only)
# ---------------------------------------------------------------------------

MIGRATION_STATUS_LABELS: frozenset[str] = frozenset(
    {
        "migration_required",
        "migration_in_progress",
        "migration_blocked",
    }
)
"""Labels that describe version-migration state at runtime.

These values are **runtime truth** and must never be written into
``session``, ``state``, or ``context`` canonical objects.  They belong
exclusively in the operator-facing runtime status surface
(``runtime-status-surface.md``).
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_version_bump(axis: str, _from_version: str, _to_version: str) -> VersionBumpClass:
    """Return the :data:`VersionBumpClass` for a change on *axis*.

    The bump class is determined solely by the axis — not by the specific
    version pair.  The *_from_version* and *_to_version* arguments are
    accepted for call-site documentation and future per-pair overrides;
    they are intentionally unused in v1.

    Parameters
    ----------
    axis:
        The version axis being bumped (must be a key in :data:`VERSION_AXES`).
    _from_version:
        The old version string (unused in v1; accepted for documentation).
    _to_version:
        The new version string (unused in v1; accepted for documentation).

    Returns
    -------
    VersionBumpClass

    Raises
    ------
    ValueError
        If *axis* is not registered in :data:`VERSION_AXES`.
    """
    declaration = _AXIS_INDEX.get(axis)
    if declaration is None:
        registered = sorted(_AXIS_INDEX.keys())
        raise ValueError(f"Unknown version axis {axis!r}.  Registered axes: {registered}")
    return declaration["bump_class_on_change"]


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "MIGRATION_STATUS_LABELS",
    "VERSION_AXES",
    "VersionAxisDeclaration",
    "VersionBumpClass",
    "classify_version_bump",
]
