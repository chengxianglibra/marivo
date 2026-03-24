"""Scope-aware causal basis generation for Factum's evidence engine.

Shared helper used by both the synthesis path (EvidencePipeline.build_synthesis)
and the reflection path (build_reflection_context). Replaces the static
_CAUSAL_CONFOUNDERS / _CAUSAL_VALIDATION lookup tables with rule-based generation.

Rules inspect the claim's scope (metric name, slice keys), its supporting
observations, and a small session-level summary to produce specific confounder
strings keyed by stable gap identifiers.

Design choices (G-3 plan):
- Decision A: shared helper reused by synthesis + reflection paths.
- Decision B: supporting observations + small session summary as rule inputs.
- Decision C: no claim scope expansion; metadata is derived from observations.
- Decision E: stable gap_key (machine identifier) + rendered user-facing text.
"""

from __future__ import annotations

from typing import Any, NamedTuple


# ── Gap key constants ─────────────────────────────────────────────────────────

GAP_MISSING_OBSERVED_WINDOW = "missing_observed_window"
GAP_MISSING_TEMPORAL_ORDERING = "missing_temporal_ordering"
GAP_NORMALISE_WORKLOAD_VOLUME = "normalise_workload_volume"

# Fallback gap keys (used when no specific rules fire)
GAP_CORRELATION_ONLY = "correlation_only"
GAP_CONCURRENT_CHANGES = "concurrent_changes_not_controlled"
GAP_TEMPORAL_PRECEDENCE = "temporal_precedence_not_established"
GAP_CAUSAL_MECHANISM = "causal_mechanism_not_identified"
GAP_CONFOUNDERS_NOT_ELIMINATED = "confounders_not_fully_eliminated"
GAP_EXPERIMENTAL_CONFIRMATION = "experimental_confirmation_pending"


# ── Keyword sets for metric name heuristics ───────────────────────────────────

_TIME_METRIC_KEYWORDS: frozenset[str] = frozenset({
    "elapsed_time", "latency", "duration", "delay", "response_time", "query_time",
    "exec_time", "wall_time",
})
_FAILURE_METRIC_KEYWORDS: frozenset[str] = frozenset({
    "failure_rate", "error_rate", "fail_rate", "failed_pct", "error_pct",
    "failure_count", "error_count",
})
_RESOURCE_SLICE_KEYS: frozenset[str] = frozenset({
    "cluster", "resource_group", "user", "user_name", "username",
    "queue", "pool", "service", "tenant",
})


# ── Per-level fallback gaps (fired when no specific rules produce output) ─────

_FALLBACK_GAPS: dict[str, list[tuple[str, str]]] = {
    "L0": [
        (GAP_CORRELATION_ONLY, "correlation only; directionality not established"),
        (GAP_CONCURRENT_CHANGES, "concurrent changes not controlled"),
    ],
    "L1": [
        (GAP_TEMPORAL_PRECEDENCE, "temporal precedence not established"),
    ],
    "L2": [
        (GAP_CAUSAL_MECHANISM, "causal mechanism not identified"),
    ],
    "L3": [
        (GAP_CONFOUNDERS_NOT_ELIMINATED, "confounders not fully eliminated"),
    ],
    "L4": [
        (GAP_EXPERIMENTAL_CONFIRMATION, "experimental confirmation pending"),
    ],
    "L5": [],
}

_FALLBACK_VALIDATION: dict[str, str] = {
    "L0": (
        "Run `aggregate_query` with `observed_window_column` to establish temporal ordering; "
        "optionally run `correlate_metrics` to test cross-series association"
    ),
    "L1": (
        "Verify temporal ordering with time-sliced `aggregate_query`; "
        "check for dose-response using `correlate_metrics`"
    ),
    "L2": "Identify mechanistic pathway; run `aggregate_query` or intervention to rule out alternatives",
    "L3": "Design experiment to eliminate remaining confounders",
    "L4": "Run randomized controlled experiment to confirm causal link",
    "L5": "Monitor for effect persistence and generalizability",
}


# ── Public types ──────────────────────────────────────────────────────────────

class EvidenceGap(NamedTuple):
    """A single evidence gap with a stable key and rendered user-facing text."""
    key: str
    text: str


class SessionSummary(NamedTuple):
    """Small summary of session-level evidence available to confounder rules."""
    has_comparable_slices: bool      # other slices with same metric exist beyond this claim's slice
    has_windowed_observations: bool  # any session observation has observed_window != None
    metric_names: frozenset[str]     # all metric names referenced in session observations


# ── Session summary derivation ────────────────────────────────────────────────

def derive_session_summary(
    claim_scope: dict[str, Any],
    all_observations: list[dict[str, Any]],
) -> SessionSummary:
    """Derive a small session summary from the full observation list.

    Args:
        claim_scope: The scope dict of the claim being evaluated.
        all_observations: All observations for the session.
    """
    claim_metric = claim_scope.get("metric", "")
    claim_slice = claim_scope.get("slice", {})

    other_slices = False
    has_windowed = False
    metric_names: set[str] = set()

    for obs in all_observations:
        subject = obs.get("subject", {})
        obs_metric = subject.get("metric", "")
        obs_slice = subject.get("slice", {})
        if obs_metric:
            metric_names.add(obs_metric)
        if obs.get("observed_window") is not None:
            has_windowed = True
        # "Comparable slice" means same metric, different slice dict
        if obs_metric == claim_metric and obs_slice != claim_slice:
            other_slices = True

    return SessionSummary(
        has_comparable_slices=other_slices,
        has_windowed_observations=has_windowed,
        metric_names=frozenset(metric_names),
    )


# ── Metric and slice key helpers ──────────────────────────────────────────────

def _metric_is_time_based(metric: str) -> bool:
    lower = metric.lower()
    return any(kw in lower for kw in _TIME_METRIC_KEYWORDS)


def _metric_is_failure_rate(metric: str) -> bool:
    lower = metric.lower()
    return any(kw in lower for kw in _FAILURE_METRIC_KEYWORDS)


def _supporting_have_windows(supporting_observations: list[dict[str, Any]]) -> bool:
    return any(obs.get("observed_window") is not None for obs in supporting_observations)


def _supporting_have_temporal_order(supporting_observations: list[dict[str, Any]]) -> bool:
    return any((obs.get("temporal_order") or 0) > 0 for obs in supporting_observations)


def _slice_has_resource_keys(slice_dict: dict[str, Any]) -> bool:
    return bool({str(k).lower() for k in slice_dict} & _RESOURCE_SLICE_KEYS)


# ── Rule engine ───────────────────────────────────────────────────────────────

def _build_scope_aware_gaps(
    claim: dict[str, Any],
    supporting_observations: list[dict[str, Any]],
    session_summary: SessionSummary,
) -> list[EvidenceGap]:
    """Apply confounder rules and return matching gaps (no duplicates, in rule order).

    Rules only fire when they have a specific, evidenced reason to do so — the rule
    engine is intentionally conservative (Decision G-3 plan §7).
    """
    scope = claim.get("scope", {})
    metric = scope.get("metric", "")
    slice_dict = scope.get("slice", {})
    level = claim.get("inference_level", "L0")

    gaps: list[EvidenceGap] = []
    seen: set[str] = set()

    def _add(gap: EvidenceGap) -> None:
        if gap.key not in seen:
            seen.add(gap.key)
            gaps.append(gap)

    # Rule 1 — missing observed_window on existing supporting observations.
    # Fires only when the claim HAS supporting observations but none have a window.
    # (If there are no observations yet, the fallback is more appropriate.)
    if supporting_observations and not _supporting_have_windows(supporting_observations):
        _add(EvidenceGap(
            key=GAP_MISSING_OBSERVED_WINDOW,
            text=(
                "populate `observed_window` (use `observed_window_column` param in "
                "`aggregate_query`) to enable temporal precedence checking"
            ),
        ))

    # Rule 2 — time/failure metric with no temporal ordering in supporting observations.
    # Only fires when we know the metric is time/failure-sensitive AND we have
    # observations that lack both temporal order and observed_window.
    if supporting_observations and (_metric_is_time_based(metric) or _metric_is_failure_rate(metric)):
        if (
            not _supporting_have_temporal_order(supporting_observations)
            and not _supporting_have_windows(supporting_observations)
        ):
            _add(EvidenceGap(
                key=GAP_MISSING_TEMPORAL_ORDERING,
                text=(
                    f"run `aggregate_query` grouped by a time column with `observed_window_column` "
                    f"to establish temporal ordering for `{metric}`; "
                    "optionally run `correlate_metrics` to test cross-series association"
                ),
            ))

    # Rule 3 — resource/group slice with comparable slices elsewhere in the session.
    # Fires when the claim slice uses a resource-like dimension (cluster, user, etc.)
    # and there are other slices for the same metric in the session.
    if _slice_has_resource_keys(slice_dict) and session_summary.has_comparable_slices:
        _add(EvidenceGap(
            key=GAP_NORMALISE_WORKLOAD_VOLUME,
            text=(
                "check whether overall workload volume accounts for the difference "
                "(normalise by total query count across the same slice dimension)"
            ),
        ))

    # Fallback — use level-based gaps when no specific rules fired.
    if not gaps:
        for key, text in _FALLBACK_GAPS.get(level, _FALLBACK_GAPS["L0"]):
            _add(EvidenceGap(key=key, text=text))

    return gaps


def _build_suggested_validation(
    gaps: list[EvidenceGap],
    claim: dict[str, Any],
) -> str:
    """Generate concrete suggested_validation from fired gap keys."""
    gap_keys = {g.key for g in gaps}
    level = claim.get("inference_level", "L0")
    metric = claim.get("scope", {}).get("metric", "")

    parts: list[str] = []

    if GAP_MISSING_OBSERVED_WINDOW in gap_keys:
        parts.append(
            "Run `aggregate_query` with `observed_window_column` set to a time column "
            "to enable temporal ordering."
        )
    if GAP_MISSING_TEMPORAL_ORDERING in gap_keys:
        if metric:
            parts.append(
                f"Run `aggregate_query` grouped by a time column with `observed_window_column` "
                f"to establish temporal ordering for `{metric}`."
            )
        else:
            parts.append(
                "Run `aggregate_query` grouped by a time column with `observed_window_column` "
                "to establish temporal ordering."
            )
    if GAP_NORMALISE_WORKLOAD_VOLUME in gap_keys:
        parts.append(
            "Run `aggregate_query` to compute the metric normalised by total query count "
            "across the same slice dimension."
        )

    if parts:
        return " ".join(parts)

    # Fallback to corrected level-based guidance.
    return _FALLBACK_VALIDATION.get(level, _FALLBACK_VALIDATION["L0"])


# ── Public API ────────────────────────────────────────────────────────────────

def build_causal_basis(
    claim: dict[str, Any],
    supporting_observations: list[dict[str, Any]],
    session_summary: SessionSummary,
) -> dict[str, Any]:
    """Build causal_basis metadata from claim + observation context.

    Returns a dict compatible with the ``causal_basis`` field on recommendations.
    ``unresolved_confounders`` is a list of ``{"key": ..., "text": ...}`` dicts
    rather than plain strings, enabling stable deduplication in reflection context.

    Args:
        claim: The claim dict (must have inference_level, confidence, text, scope).
        supporting_observations: Observations that support this specific claim.
        session_summary: A small session-level summary derived from all observations.
    """
    level = claim.get("inference_level", "L0")
    confidence = claim.get("confidence", 0.0)
    gaps = _build_scope_aware_gaps(claim, supporting_observations, session_summary)
    suggested_validation = _build_suggested_validation(gaps, claim)

    return {
        "inference_level": level,
        "strongest_evidence_summary": f"{claim.get('text', '')} (confidence={confidence:.2f})",
        "unresolved_confounders": [{"key": g.key, "text": g.text} for g in gaps],
        "suggested_validation": suggested_validation,
    }
