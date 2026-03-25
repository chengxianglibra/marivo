"""Auto-resolve confounders against confirmed claims in the session.

When ``build_causal_basis`` generates ``unresolved_confounders`` for a
recommendation, some of those gaps may already be answered by other confirmed
claims in the same session.  This module moves such confounders from
``unresolved_confounders`` to a new ``resolved_confounders`` list on the
recommendation's ``causal_basis`` dict.

Design choices (roadmap 1.1):
- Resolution happens as a post-processing pass in ``EvidencePipeline.build_synthesis``.
- Matching uses a hardcoded rule table (``RESOLUTION_RULES``) keyed by gap key.
- Same-slice confirmed claims are preferred over session-wide matches.
- Resolution is purely informational; inference_level is not affected.
"""

from __future__ import annotations

from typing import Any, Callable

from app.evidence_engine.causal_basis import GAP_NORMALISE_WORKLOAD_VOLUME

# ---------------------------------------------------------------------------
# Volume / count keywords used to identify "workload volume" claims.
# ---------------------------------------------------------------------------
_VOLUME_KEYWORDS: frozenset[str] = frozenset({
    "count", "volume", "throughput", "qps", "requests", "queries",
    "num_queries", "query_count", "request_count",
})

# Metrics that contain a volume keyword as a substring but are not volume metrics.
_VOLUME_FALSE_POSITIVES: frozenset[str] = frozenset({
    "discount", "account", "counter_example",
})


def _is_volume_claim(claim: dict[str, Any]) -> bool:
    metric = claim.get("scope", {}).get("metric", "").lower()
    if any(fp in metric for fp in _VOLUME_FALSE_POSITIVES):
        return False
    return any(kw in metric for kw in _VOLUME_KEYWORDS)


# ---------------------------------------------------------------------------
# Resolution rules: gap_key -> predicate(confirmed_claim) -> bool
# ---------------------------------------------------------------------------
RESOLUTION_RULES: dict[str, Callable[[dict[str, Any]], bool]] = {
    GAP_NORMALISE_WORKLOAD_VOLUME: _is_volume_claim,
}


def _scope_overlap(slice_a: dict[str, Any], slice_b: dict[str, Any]) -> float:
    """Return Jaccard-like overlap between two slice dicts (key+value pairs).

    Values are coerced to ``str`` before comparison so that unhashable types
    (lists, nested dicts) do not raise ``TypeError``.
    """
    if not slice_a and not slice_b:
        return 1.0
    if not slice_a or not slice_b:
        return 0.0
    pairs_a = {(k, str(v)) for k, v in slice_a.items()}
    pairs_b = {(k, str(v)) for k, v in slice_b.items()}
    intersection = pairs_a & pairs_b
    union = pairs_a | pairs_b
    return len(intersection) / len(union) if union else 0.0


def _find_best_match(
    predicate: Callable[[dict[str, Any]], bool],
    confirmed_claims: list[dict[str, Any]],
    backing_slice: dict[str, Any],
) -> dict[str, Any] | None:
    """Find the best matching confirmed claim, preferring same-slice."""
    best: dict[str, Any] | None = None
    best_overlap: float = -1.0

    for claim in confirmed_claims:
        if not predicate(claim):
            continue
        overlap = _scope_overlap(
            backing_slice,
            claim.get("scope", {}).get("slice", {}),
        )
        if overlap > best_overlap:
            best = claim
            best_overlap = overlap

    return best


def resolve_confounders(
    recommendations: list[dict[str, Any]],
    confirmed_claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Post-process recommendations: move auto-resolvable confounders to resolved_confounders.

    For each recommendation's ``unresolved_confounders``, checks whether any
    confirmed claim in the session satisfies the resolution rule for that
    gap key.  Same-slice claims are preferred.

    Returns a new list of recommendations (original list is not mutated).
    """
    if not confirmed_claims:
        # Still ensure resolved_confounders field exists for API consistency.
        return [
            {
                **rec,
                "causal_basis": {**rec["causal_basis"], "resolved_confounders": []}
                if isinstance(rec.get("causal_basis"), dict)
                else rec.get("causal_basis"),
            }
            for rec in recommendations
        ]

    result: list[dict[str, Any]] = []
    for rec in recommendations:
        causal_basis = rec.get("causal_basis")
        if not causal_basis or not isinstance(causal_basis, dict):
            result.append(rec)
            continue

        unresolved = causal_basis.get("unresolved_confounders", [])
        if not unresolved:
            result.append({
                **rec,
                "causal_basis": {**causal_basis, "resolved_confounders": []},
            })
            continue

        # Determine the backing claim's slice for same-slice preference.
        backing_claim_id = rec.get("claim_id", "")
        backing_slice: dict[str, Any] = {}
        for c in confirmed_claims:
            if c.get("claim_id") == backing_claim_id:
                backing_slice = c.get("scope", {}).get("slice", {})
                break

        still_unresolved: list[dict[str, Any]] = []
        resolved: list[dict[str, Any]] = []

        for gap in unresolved:
            gap_key = gap.get("key", "") if isinstance(gap, dict) else ""
            predicate = RESOLUTION_RULES.get(gap_key)
            if predicate is None:
                still_unresolved.append(gap)
                continue

            match = _find_best_match(predicate, confirmed_claims, backing_slice)
            if match is None:
                still_unresolved.append(gap)
                continue

            resolved.append({
                "key": gap_key,
                "resolved_by": match["claim_id"],
                "summary": match.get("text", "")[:200],
            })

        result.append({
            **rec,
            "causal_basis": {
                **causal_basis,
                "unresolved_confounders": still_unresolved,
                "resolved_confounders": resolved,
            },
        })

    return result


def filter_resolved_gap_keys(
    gaps: list[Any],
    confirmed_claims: list[dict[str, Any]],
) -> list[Any]:
    """Remove gaps whose keys have been resolved by confirmed claims.

    Used by the reflection context path where gaps are ``EvidenceGap``
    NamedTuples (with a ``.key`` attribute).  Returns the filtered list.
    """
    if not confirmed_claims:
        return gaps

    resolved_keys: set[str] = set()
    for gap in gaps:
        gap_key = gap.key if hasattr(gap, "key") else ""
        predicate = RESOLUTION_RULES.get(gap_key)
        if predicate and any(predicate(c) for c in confirmed_claims):
            resolved_keys.add(gap_key)

    if not resolved_keys:
        return gaps

    return [g for g in gaps if (g.key if hasattr(g, "key") else "") not in resolved_keys]
