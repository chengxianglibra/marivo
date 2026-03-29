"""M-04 Readiness Signal — fully deterministic, no LLM."""

from __future__ import annotations

import json
from typing import Any

from app.storage.metadata import MetadataStore


def load_live_claims(metadata_store: MetadataStore, session_id: str) -> list[dict[str, Any]]:
    """Load tentative + confirmed claims for a session."""
    rows = metadata_store.query_rows(
        """
        SELECT claim_id, claim_type, text, scope_json, confidence, status,
               supporting_observation_ids_json, contradicting_observation_ids_json,
               confidence_breakdown_json, inference_level, inference_justification_json
        FROM claims
        WHERE session_id = ? AND status IN ('tentative', 'confirmed')
        ORDER BY rowid
        """,
        [session_id],
    )
    result = []
    for row in rows:
        claim = dict(row)
        claim["type"] = claim.pop("claim_type")
        claim["scope"] = json.loads(claim.pop("scope_json"))
        claim["supporting_observations"] = json.loads(claim.pop("supporting_observation_ids_json"))
        claim["contradicting_observations"] = json.loads(
            claim.pop("contradicting_observation_ids_json")
        )
        claim["confidence_breakdown"] = json.loads(claim.pop("confidence_breakdown_json"))
        claim["inference_justification"] = json.loads(claim.pop("inference_justification_json"))
        result.append(claim)
    return result


def compute_readiness(
    metadata_store: MetadataStore,
    session_id: str,
    budget: dict[str, Any],
    *,
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute five-dimensional readiness signal for a session.

    Returns a dict with keys:
        goal_coverage, evidence_sufficiency, contradiction_resolution,
        budget_remaining, diminishing_returns, suggested_action
    """
    if claims is None:
        claims = load_live_claims(metadata_store, session_id)

    # 1. goal_coverage: claims with confidence >= 0.5 / 5, clipped [0,1]
    qualifying = [c for c in claims if c.get("confidence", 0) >= 0.5]
    goal_coverage = min(len(qualifying) / 5.0, 1.0)

    # 2. evidence_sufficiency: avg supporting obs count / 3, clipped [0,1]
    if claims:
        avg_supporting = sum(len(c.get("supporting_observations", [])) for c in claims) / len(
            claims
        )
        evidence_sufficiency = min(avg_supporting / 3.0, 1.0)
    else:
        evidence_sufficiency = 0.0

    # 3. contradiction_resolution: fraction of claims with no contradicting obs
    if claims:
        no_contradiction_count = sum(1 for c in claims if not c.get("contradicting_observations"))
        contradiction_resolution = no_contradiction_count / len(claims)
    else:
        contradiction_resolution = 1.0

    # 4. budget_remaining
    max_steps = budget.get("max_steps") if budget else None
    if max_steps:
        step_count_row = metadata_store.query_one(
            "SELECT COUNT(*) AS cnt FROM steps WHERE session_id = ? AND step_type != 'synthesize_findings'",
            [session_id],
        )
        step_count = step_count_row["cnt"] if step_count_row else 0
        budget_remaining = max((max_steps - step_count) / max_steps, 0.0)
    else:
        budget_remaining = 1.0

    # 5. diminishing_returns: recent 3 primitive steps that produced new claims
    primitive_steps = metadata_store.query_rows(
        """
        SELECT step_id, created_at
        FROM steps
        WHERE session_id = ? AND step_type != 'synthesize_findings'
        ORDER BY rowid DESC
        LIMIT 3
        """,
        [session_id],
    )

    if len(primitive_steps) < 3:
        diminishing_returns = 1.0
    else:
        # For each of the last 3 steps, check if any claims were created after the step's created_at.
        # We use rowid ordering for tie-breaking (consistent with SQLite).
        steps_with_new_claims = 0
        for i, step in enumerate(primitive_steps):
            # Get the created_at of the step after this one (i.e. the next newer step in reverse order)
            # primitive_steps is in DESC order: [newest, middle, oldest]
            # For the oldest step (index 2), check claims after it but before middle step
            if i == len(primitive_steps) - 1:
                # oldest step — check claims after it up to middle step
                next_step = primitive_steps[i - 1] if i > 0 else None
                if next_step:
                    new_claim_row = metadata_store.query_one(
                        """
                        SELECT COUNT(*) AS cnt FROM claims
                        WHERE session_id = ? AND created_at >= ? AND created_at < ?
                        """,
                        [session_id, step["created_at"], next_step["created_at"]],
                    )
                else:
                    new_claim_row = metadata_store.query_one(
                        "SELECT COUNT(*) AS cnt FROM claims WHERE session_id = ? AND created_at >= ?",
                        [session_id, step["created_at"]],
                    )
            else:
                # for other steps, check claims after the previous (older) step
                older_step = primitive_steps[i + 1]
                new_claim_row = metadata_store.query_one(
                    """
                    SELECT COUNT(*) AS cnt FROM claims
                    WHERE session_id = ? AND created_at >= ? AND created_at <= ?
                    """,
                    [session_id, older_step["created_at"], step["created_at"]],
                )
            cnt = new_claim_row["cnt"] if new_claim_row else 0
            if cnt > 0:
                steps_with_new_claims += 1
        diminishing_returns = steps_with_new_claims / 3.0

    # suggested_action — priority cascade
    if contradiction_resolution < 1.0:
        suggested_action = "resolve_contradiction"
    elif goal_coverage >= 0.7 and evidence_sufficiency >= 0.7:
        suggested_action = "synthesize"
    elif budget_remaining <= (1.0 / max_steps if max_steps else 0.10) or (
        diminishing_returns < 0.2 and evidence_sufficiency >= 0.6
    ):
        suggested_action = "stop"
    else:
        suggested_action = "continue_exploring"

    return {
        "goal_coverage": goal_coverage,
        "evidence_sufficiency": evidence_sufficiency,
        "contradiction_resolution": contradiction_resolution,
        "budget_remaining": budget_remaining,
        "diminishing_returns": diminishing_returns,
        "suggested_action": suggested_action,
    }
