from __future__ import annotations

import dataclasses
from typing import Any

from app.evidence_engine.schemas import Claim, Recommendation
from app.evidence_engine.synthesizers.claim_formulator import ClaimFormulator
from app.evidence_engine.synthesizers.scope_clusterer import ScopeClusterer
from app.evidence_engine.synthesizers.signal_aligner import SignalAligner
from app.evidence_engine.synthesizers.stages import PipelineAuditLog


class ThreeStagePipeline:
    """Orchestrates ScopeClusterer → SignalAligner → ClaimFormulator.

    Designed to replace the monolithic synthesize_claims() function.
    Returns a 4-tuple (claims, recommendations, edges, audit_log) where
    recommendations and edges are always empty — downstream
    EvidencePipeline.build_synthesis() handles those.
    """

    def __init__(
        self,
        clusterer: ScopeClusterer | None = None,
        aligner: SignalAligner | None = None,
        formulator: ClaimFormulator | None = None,
    ) -> None:
        self._clusterer = clusterer or ScopeClusterer()
        self._aligner = aligner or SignalAligner()
        self._formulator = formulator or ClaimFormulator()

    def run(
        self,
        observations: list[dict[str, Any]],
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]], PipelineAuditLog]:
        """Run the 3-stage pipeline and return (claims, [], [], audit_log)."""
        audit = PipelineAuditLog(observation_count=len(observations))

        if not observations:
            return [], [], [], audit

        try:
            # Stage 1: cluster
            clusters = self._clusterer.cluster(observations)
            audit.scope_clusters = [_cluster_audit(c) for c in clusters]

            if not clusters:
                return [], [], [], audit

            # Stage 2: align
            signals = self._aligner.align_all(clusters)
            audit.alignment_scores = [_signal_audit(s) for s in signals]

            # Stage 3: formulate per-scope claims
            formulations = [self._formulator.formulate(s) for s in signals]
            audit.formulation_decisions = [_formulation_audit(f) for f in formulations]

            claims: list[Claim] = [f.claim for f in formulations]

            # Check for overall_trend across all metric_change observations
            all_metric_obs: list[dict[str, Any]] = []
            for cluster in clusters:
                all_metric_obs.extend(cluster.metric_change_obs)

            trend = self._formulator.formulate_overall_trend(signals, all_metric_obs)
            if trend is not None:
                claims.append(trend.claim)
                audit.formulation_decisions.append(_formulation_audit(trend))
                audit.overall_trend_generated = True

            audit.claims_produced = len(claims)
        except Exception as exc:
            audit.error = str(exc)
            return [], [], [], audit

        return claims, [], [], audit


# ── audit helpers ─────────────────────────────────────────────────────────────


def _cluster_audit(c: Any) -> dict[str, Any]:
    d = dataclasses.asdict(c)
    # Replace full observation dicts with just their IDs to keep audit compact
    for bucket in ("metric_change_obs", "funnel_drop_obs",
                   "contribution_shift_obs", "anomaly_detection_obs", "other_obs"):
        d[bucket] = [o.get("observation_id", "<no-id>") for o in d[bucket]]
    return d


def _signal_audit(s: Any) -> dict[str, Any]:
    return {
        "scope_key": s.scope_cluster.scope_key,
        "primary_obs_id": s.primary_obs.get("observation_id", "<no-id>"),
        "primary_selection_reason": s.primary_selection_reason,
        "supporting_obs_ids": s.supporting_obs_ids,
        "contradicting_obs_ids": s.contradicting_obs_ids,
        "effect_strength": s.effect_strength,
        "consistency": s.consistency,
        "sample_score": s.sample_score,
        "data_quality_score": s.data_quality_score,
        "contradiction_penalty": s.contradiction_penalty,
        "consistency_factors": s.consistency_factors,
        "support_reasons": s.support_reasons,
        "alignment_notes": s.alignment_notes,
    }


def _formulation_audit(f: Any) -> dict[str, Any]:
    return {
        "claim_id": f.claim.get("claim_id", "<no-id>"),
        "claim_type_decision": f.claim_type_decision,
        "claim_type_reason": f.claim_type_reason,
        "text_template": f.text_template,
        "confidence_inputs": f.confidence_inputs,
        "final_confidence": f.final_confidence,
        "is_non_metric": f.is_non_metric,
    }
