"""M-11.1 Reflection Context — deterministic evidence gap summary for agents.

Design: Factum produces structured signals (facts by code). Agents decide
what patches to apply or what steps to run next (language by model).

G-3c breaking change: ``evidence_gaps`` is now a session-level deduplicated
list of ``{"gap_key", "text", "suggested_validation", "affected_claims"}``
dicts rather than a per-recommendation list.  Each unique gap_key appears at
most once; ``affected_claims`` lists every claim that contributes the gap.
"""

from __future__ import annotations

import json
from typing import Any

from app.analysis_core.primitives import STEP_TAXONOMY
from app.evidence_engine.confounder_resolution import filter_resolved_gap_keys
from app.evidence_engine.causal_basis import (
    _build_scope_aware_gaps,
    derive_session_summary,
)
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

        ``evidence_gaps`` (G-3c): a session-level deduplicated list of
        ``{"gap_key", "text", "suggested_validation", "affected_claims"}``
        dicts.  Each unique gap_key appears at most once across all claims.

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

    all_live = load_live_claims(metadata_store, session_id)
    readiness_signal = compute_readiness(metadata_store, session_id, budget, claims=all_live)
    readiness_score = round(
        sum(readiness_signal[k] for k in _NUMERIC_READINESS_DIMS) / len(_NUMERIC_READINESS_DIMS),
        4,
    )

    # Load all session observations once for use by the rule engine.
    all_observations = _load_session_observations(metadata_store, session_id)
    obs_map = {o["observation_id"]: o for o in all_observations}

    # tentative_claims: claims with status='tentative' OR inference_level in ('L0', 'L1')
    # unresolved_confounders is now scope-aware (G-3a) when supporting observations exist.
    # Gaps already resolved by confirmed claims are filtered out (roadmap 1.1).
    _confirmed_claims = [c for c in all_live if c.get("status") == "confirmed"]
    tentative_claims = [
        {
            "claim_id": c["claim_id"],
            "text": c["text"],
            "scope": c["scope"],
            "confidence": c["confidence"],
            "inference_level": c.get("inference_level", "L0"),
            "unresolved_confounders": _confounders_for(c, obs_map, all_observations, _confirmed_claims),
        }
        for c in all_live
        if c.get("status") == "tentative" or c.get("inference_level", "L0") in ("L0", "L1")
    ]

    # evidence_gaps (G-3c): session-level deduplicated across all recommendations.
    evidence_gaps = _load_evidence_gaps(metadata_store, session_id)

    # entity_update_suggestions (G-5c): sourced from recommendations with entity_patch_json
    entity_update_suggestions = _load_entity_update_suggestions(metadata_store, session_id)

    return {
        "session_id": session_id,
        "plan_id": plan_id,
        "readiness_signal": readiness_signal,
        "readiness_score": readiness_score,
        "tentative_claims": tentative_claims,
        "evidence_gaps": evidence_gaps,
        "entity_update_suggestions": entity_update_suggestions,
        "available_step_types": list(STEP_TAXONOMY.keys()),
    }


def _confounders_for(
    claim: dict[str, Any],
    obs_map: dict[str, Any],
    all_observations: list[dict[str, Any]],
    confirmed_claims: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Build scope-aware confounder strings for a claim.

    Uses the rule engine (G-3a) when supporting observations are available.
    Gaps already resolved by confirmed claims in the session are filtered out
    (roadmap 1.1).
    Returns a plain list[str] so that the tentative_claims API shape is unchanged.
    """
    supporting_obs = [
        obs_map[oid]
        for oid in claim.get("supporting_observations", [])
        if oid in obs_map
    ]
    session_summary = derive_session_summary(claim.get("scope", {}), all_observations)
    gaps = _build_scope_aware_gaps(claim, supporting_obs, session_summary)
    if confirmed_claims:
        gaps = filter_resolved_gap_keys(gaps, confirmed_claims)
    return [g.text for g in gaps]


def _load_session_observations(
    metadata_store: MetadataStore,
    session_id: str,
) -> list[dict[str, Any]]:
    """Load all observations for a session, including observed_window and temporal_order."""
    rows = metadata_store.query_rows(
        """
        SELECT observation_id, observation_type, subject_json, payload_json,
               significance_json, quality_json,
               observed_window_json, temporal_order
        FROM observations
        WHERE session_id = ?
        ORDER BY created_at
        """,
        [session_id],
    )
    result = []
    for row in rows:
        obs: dict[str, Any] = {
            "observation_id": row["observation_id"],
            "type": row["observation_type"],
            "subject": json.loads(row["subject_json"]),
            "payload": json.loads(row["payload_json"]),
            "significance": json.loads(row["significance_json"]),
            "quality": json.loads(row["quality_json"]),
            "temporal_order": row.get("temporal_order") or 0,
        }
        raw_window = row.get("observed_window_json")
        obs["observed_window"] = json.loads(raw_window) if raw_window else None
        result.append(obs)
    return result


def _load_entity_update_suggestions(
    metadata_store: MetadataStore,
    session_id: str,
) -> list[dict[str, Any]]:
    """Load entity update suggestions from recommendations with non-null entity_patch_json.

    G-5c: Returns a token-friendly list of unique (entity_id, field) patches sourced
    from confirmed recommendations.  Deduplicates by (entity_id, field, suggested_value)
    so the same patch is not repeated across multiple recommendations.
    """
    rows = metadata_store.query_rows(
        """
        SELECT rec_id, entity_patch_json
        FROM recommendations
        WHERE session_id = ? AND entity_patch_json IS NOT NULL
        """,
        [session_id],
    )

    seen: set[tuple[str, str, str]] = set()
    suggestions: list[dict[str, Any]] = []

    for row in rows:
        patch = json.loads(row["entity_patch_json"])
        dedup_key = (
            patch.get("entity_id", ""),
            patch.get("field", ""),
            str(patch.get("suggested_value", "")),
        )
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        suggestions.append({
            "entity_id": patch.get("entity_id"),
            "entity_name": patch.get("entity_name"),
            "column_name": patch.get("column_name"),
            "field": patch.get("field"),
            "current_value": patch.get("current_value"),
            "suggested_value": patch.get("suggested_value"),
            "confidence": patch.get("confidence"),
            "source": patch.get("source"),
            "metric_name": patch.get("metric_name"),
            "rec_id": row["rec_id"],
        })

    return suggestions


def _load_evidence_gaps(
    metadata_store: MetadataStore,
    session_id: str,
) -> list[dict[str, Any]]:
    """Load session-level deduplicated evidence gaps from persisted recommendations.

    G-3c: rather than returning one entry per recommendation, this aggregates
    all confounders across recommendations and deduplicates by gap_key.  The
    returned list has one entry per unique gap_key, with ``affected_claims``
    listing every claim that contributes the gap.

    Handles both structured gap format ({"key": ..., "text": ...}) and the
    legacy plain-string format for backward compatibility with older rows.
    """
    rows = metadata_store.query_rows(
        """
        SELECT rec_id, claim_id, causal_basis_json
        FROM recommendations
        WHERE session_id = ? AND causal_basis_json IS NOT NULL
        """,
        [session_id],
    )

    # (gap_key, text) → {gap_key, text, suggested_validation, affected_claims (set)}.
    # Using (gap_key, text) as the composite dedup key so that metric-specific
    # variants of the same rule (e.g. missing_temporal_ordering for elapsed_time
    # vs failure_rate) are kept as separate entries rather than collapsed into one.
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        causal_basis = json.loads(row["causal_basis_json"])
        confounders = causal_basis.get("unresolved_confounders", [])
        suggested_validation = causal_basis.get("suggested_validation", "")
        claim_id = row["claim_id"]

        for item in confounders:
            if isinstance(item, dict):
                gap_key = item.get("key", "unknown")
                text = item.get("text", "")
            else:
                # Legacy plain-string format: use the string as both key and text.
                gap_key = str(item)
                text = str(item)

            dedup_key = (gap_key, text)
            if dedup_key not in aggregated:
                aggregated[dedup_key] = {
                    "gap_key": gap_key,
                    "text": text,
                    "suggested_validation": suggested_validation,
                    "affected_claims": set(),
                }
            aggregated[dedup_key]["affected_claims"].add(claim_id)

    # Convert sets to sorted lists for stable serialisation.
    return [
        {
            "gap_key": entry["gap_key"],
            "text": entry["text"],
            "suggested_validation": entry["suggested_validation"],
            "affected_claims": sorted(entry["affected_claims"]),
        }
        for entry in aggregated.values()
    ]
