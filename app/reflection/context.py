"""M-11.1 Reflection Context — deterministic evidence gap summary for agents.

Design: Factum produces structured signals (facts by code). Agents decide
what patches to apply or what steps to run next (language by model).
"""

from __future__ import annotations

import json
from typing import Any

from app.analysis_core.primitives import STEP_TAXONOMY
from app.evidence_engine.readiness import compute_readiness, load_live_claims
from app.storage.metadata import MetadataStore

_NUMERIC_READINESS_DIMS = (
    "goal_coverage",
    "evidence_sufficiency",
    "contradiction_resolution",
    "budget_remaining",
    "diminishing_returns",
)


def build_reflection_context(
    metadata_store: MetadataStore,
    session_id: str,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Build a compact, token-friendly reflection context for the given session.

    Args:
        metadata_store: The metadata store to query.
        session_id: The session to reflect on.
        plan_id: Optional plan to include in the context (for agent reference).

    Returns:
        A dict with keys:
            session_id, plan_id, readiness_signal, readiness_score,
            tentative_claims, evidence_gaps, available_step_types

    Raises:
        KeyError: If the session is not found.
    """
    row = metadata_store.query_one(
        "SELECT budget_json FROM sessions WHERE session_id = ?",
        [session_id],
    )
    if row is None:
        raise KeyError(f"Unknown session: {session_id}")
    budget = json.loads(row["budget_json"])

    readiness_signal = compute_readiness(metadata_store, session_id, budget)
    readiness_score = round(
        sum(readiness_signal[k] for k in _NUMERIC_READINESS_DIMS) / len(_NUMERIC_READINESS_DIMS),
        4,
    )

    # tentative_claims: claims with status='tentative' OR inference_level in ('L0', 'L1')
    # These represent claims where more evidence could strengthen the analysis.
    all_live = load_live_claims(metadata_store, session_id)
    tentative_claims = [
        {
            "claim_id": c["claim_id"],
            "text": c["text"],
            "scope": c["scope"],
            "confidence": c["confidence"],
            "inference_level": c.get("inference_level", "L0"),
            "unresolved_confounders": _confounders_for(c),
        }
        for c in all_live
        if c.get("status") == "tentative" or c.get("inference_level", "L0") in ("L0", "L1")
    ]

    # evidence_gaps: recommendations with causal_basis where unresolved_confounders is non-empty
    evidence_gaps = _load_evidence_gaps(metadata_store, session_id)

    return {
        "session_id": session_id,
        "plan_id": plan_id,
        "readiness_signal": readiness_signal,
        "readiness_score": readiness_score,
        "tentative_claims": tentative_claims,
        "evidence_gaps": evidence_gaps,
        "available_step_types": list(STEP_TAXONOMY.keys()),
    }


def _confounders_for(claim: dict[str, Any]) -> list[str]:
    """Extract unresolved confounders from a claim dict.

    Prefers the stored causal_basis confounders if available,
    otherwise falls back to the inference_level lookup table.
    """
    from app.evidence_engine.schemas import _CAUSAL_CONFOUNDERS  # noqa: PLC0415

    level = claim.get("inference_level", "L0")
    return list(_CAUSAL_CONFOUNDERS.get(level, _CAUSAL_CONFOUNDERS["L0"]))


def _load_evidence_gaps(
    metadata_store: MetadataStore,
    session_id: str,
) -> list[dict[str, Any]]:
    """Load recommendations with non-empty unresolved_confounders as evidence gaps."""
    rows = metadata_store.query_rows(
        """
        SELECT rec_id, claim_id, causal_basis_json
        FROM recommendations
        WHERE session_id = ? AND causal_basis_json IS NOT NULL
        """,
        [session_id],
    )
    gaps: list[dict[str, Any]] = []
    for row in rows:
        causal_basis = json.loads(row["causal_basis_json"])
        confounders = causal_basis.get("unresolved_confounders", [])
        if confounders:
            gaps.append({
                "rec_id": row["rec_id"],
                "claim_id": row["claim_id"],
                "inference_level": causal_basis.get("inference_level", "L0"),
                "suggested_validation": causal_basis.get("suggested_validation", ""),
                "unresolved_confounders": confounders,
            })
    return gaps
