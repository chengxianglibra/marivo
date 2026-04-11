"""Shared utilities for deriving lifecycle and readiness status from storage status.

Phase A (current): Status derivation is purely computed, not persisted.
  - `draft` storage status → `draft` lifecycle, `not_ready` readiness
  - `published` storage status → `active` lifecycle, `ready` readiness
  - `deprecated` storage status → `deprecated` lifecycle, `not_ready` readiness

Phase B (future): Will introduce `validated` lifecycle status as a persisted state
between `draft` and `active`, and `stale` readiness for previously-ready objects
that became unavailable due to dependency changes.

The Literal types in `app.api.models.base` include reserved values (`validated`, `stale`)
for Phase B compatibility; current derivation never produces these values.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Known storage status values (Phase A)
_KNOWN_STORAGE_STATUS = {"draft", "published", "deprecated"}


def derive_lifecycle_status(status: str) -> str:
    """Derive lifecycle_status from storage status.

    Phase A mapping:
      - "draft" → "draft"
      - "published" → "active"
      - "deprecated" → "deprecated"

    Phase B reserved value: "validated" (never produced by this function).

    Args:
        status: Storage status string from database.

    Returns:
        Derived lifecycle status string.

    Raises:
        ValueError: If status is not a known value (fail-safe to catch data issues).
    """
    if status == "published":
        return "active"
    if status == "draft":
        return "draft"
    if status == "deprecated":
        return "deprecated"
    # Fail-safe: unknown status indicates potential data integrity issue
    raise ValueError(
        f"Unknown storage status: {status!r}. Expected one of {sorted(_KNOWN_STORAGE_STATUS)}."
    )


def derive_readiness_status(status: str) -> str:
    """Derive readiness_status from storage status.

    Phase A mapping:
      - "published" → "ready"
      - "draft" or "deprecated" → "not_ready"

    Phase B reserved value: "stale" (never produced by this function).
    Stale will be produced when readiness evaluator detects a previously-ready
    object that became unavailable due to dependency changes.

    Args:
        status: Storage status string from database.

    Returns:
        Derived readiness status string.

    Raises:
        ValueError: If status is not a known value.
    """
    if status == "published":
        return "ready"
    if status in ("draft", "deprecated"):
        return "not_ready"
    raise ValueError(
        f"Unknown storage status: {status!r}. Expected one of {sorted(_KNOWN_STORAGE_STATUS)}."
    )


def default_readiness_contract(status: str) -> dict[str, Any]:
    """Build default readiness contract dict for a given storage status.

    Returns a dict with keys:
      - lifecycle_status: derived lifecycle
      - readiness_status: derived readiness
      - blocking_requirements: empty list (no blockers in Phase A default)
      - capabilities: empty dict (no capabilities in Phase A default)

    Phase B will compute blocking_requirements and capabilities per object type
    via readiness evaluators, not use this default.

    Args:
        status: Storage status string from database.

    Returns:
        Dict with derived lifecycle/readiness fields.

    Raises:
        ValueError: If status is not a known value.
    """
    return {
        "lifecycle_status": derive_lifecycle_status(status),
        "readiness_status": derive_readiness_status(status),
        "blocking_requirements": [],
        "capabilities": {},
    }
