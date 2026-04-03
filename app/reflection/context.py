"""Reflection Context — legacy compact summary stub (Phase 6).

The canonical read surfaces are GET /sessions/{id}/state and
GET /sessions/{id}/propositions/{pid}/context.  This endpoint is kept
as a minimal legacy stub only; do not add new canonical fields here.
"""

from __future__ import annotations

from typing import Any

from app.analysis_core.primitives import STEP_TAXONOMY
from app.storage.metadata import MetadataStore


def build_reflection_context(
    metadata_store: MetadataStore,
    session_id: str,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Return a minimal reflection context stub for the given session.

    Raises:
        KeyError: If the session is not found.
    """
    row = metadata_store.query_one(
        "SELECT session_id FROM sessions WHERE session_id = ?",
        [session_id],
    )
    if row is None:
        raise KeyError(f"Unknown session: {session_id}")

    return {
        "session_id": session_id,
        "plan_id": plan_id,
        "tentative_claims": [],
        "evidence_gaps": [],
        "available_step_types": list(STEP_TAXONOMY.keys()),
    }
