from __future__ import annotations

import math
from typing import Any

from app.evidence_engine.synthesizers.stages import AlignedSignal, ScopeCluster


class SignalAligner:
    """Stage 2: Aligns signal direction and strength within a ScopeCluster.

    For metric_observation clusters with deltas: selects the primary observation by
    max(|delta_pct| * log1p(sample_size)), then classifies all other
    observations as supporting (same direction or practical significance
    agreement) or contradicting (opposite delta_pct sign).

    For non-metric clusters: selects primary by max sample_size and
    classifies all practically-significant secondary observations as
    supporting.
    """

    def align(self, cluster: ScopeCluster) -> AlignedSignal:
        """Align one ScopeCluster into an AlignedSignal.

        Audit contract: every returned AlignedSignal has
        - primary_selection_reason populated
        - consistency_factors list populated (one entry per observation)
        - support_reasons list populated
        - alignment_notes list populated with anomalies detected
        """
        if cluster.metric_observation_obs:
            return self._align_metric(cluster)
        return self._align_non_metric(cluster)

    def align_all(self, clusters: list[ScopeCluster]) -> list[AlignedSignal]:
        return [self.align(c) for c in clusters]

    # ── private ───────────────────────────────────────────────────────────────

    def _align_metric(self, cluster: ScopeCluster) -> AlignedSignal:
        metric_obs = cluster.metric_observation_obs

        # Select primary observation
        primary = max(
            metric_obs,
            key=lambda o: abs(float(o["payload"]["delta_pct"]))
            * math.log1p(o["significance"]["sample_size"]),
        )

        primary_delta = float(primary["payload"]["delta_pct"])
        supporting_ids: list[str] = [primary["observation_id"]]
        contradicting_ids: list[str] = []
        consistency_factors: list[float] = [1.0]
        support_reasons: list[str] = []
        alignment_notes: list[str] = []

        # Classify other metric_observation rows carrying delta fields
        for obs in metric_obs:
            if obs is primary:
                continue
            obs_delta = float(obs["payload"]["delta_pct"])
            if _same_direction(primary_delta, obs_delta):
                supporting_ids.append(obs["observation_id"])
                metric_name = obs.get("subject", {}).get("metric", "metric")
                support_reasons.append(f"{metric_name} observation")
                consistency_factors.append(0.9)
            else:
                contradicting_ids.append(obs["observation_id"])
                alignment_notes.append(
                    f"contradiction: obs {obs['observation_id']} delta={obs_delta:.1f}% "
                    f"vs primary delta={primary_delta:.1f}%"
                )

        # Incorporate funnel observations
        for obs in cluster.funnel_drop_obs:
            if obs["significance"]["practical_significance"]:
                supporting_ids.append(obs["observation_id"])
                stage = obs["payload"].get("worst_stage", "unknown")
                support_reasons.append(f"funnel drop at {stage}")
                consistency_factors.append(0.85)
                alignment_notes.append(f"funnel_drop added: {stage}")

        # Incorporate contribution_shift observations
        for obs in cluster.contribution_shift_obs:
            if obs["significance"]["practical_significance"]:
                supporting_ids.append(obs["observation_id"])
                segment = obs["payload"].get("biggest_shift_segment", "unknown")
                support_reasons.append(f"contribution shift in {segment}")
                consistency_factors.append(0.80)
                alignment_notes.append(f"contribution_shift added: {segment}")

        # Incorporate anomaly observations
        for obs in cluster.anomaly_detection_obs:
            if obs["significance"]["practical_significance"]:
                supporting_ids.append(obs["observation_id"])
                support_reasons.append("statistical anomaly detected")
                consistency_factors.append(0.90)
                alignment_notes.append("anomaly_detection added")

        # Compute scores
        effect_strength = min(1.0, abs(primary_delta) / 20.0)
        consistency = sum(consistency_factors) / len(consistency_factors)
        sample_score = min(1.0, primary["significance"]["sample_size"] / 150.0)
        data_quality_score = 0.95 if primary["quality"]["sample_size_ok"] else 0.60
        contradiction_penalty = min(0.5, len(contradicting_ids) * 0.15)

        return AlignedSignal(
            scope_cluster=cluster,
            primary_obs=primary,
            primary_selection_reason="max |delta_pct| * log1p(sample_size)",
            supporting_obs_ids=supporting_ids,
            contradicting_obs_ids=contradicting_ids,
            effect_strength=effect_strength,
            consistency=consistency,
            sample_score=sample_score,
            data_quality_score=data_quality_score,
            contradiction_penalty=contradiction_penalty,
            consistency_factors=consistency_factors,
            support_reasons=support_reasons,
            alignment_notes=alignment_notes,
        )

    def _align_non_metric(self, cluster: ScopeCluster) -> AlignedSignal:
        all_obs = (
            cluster.funnel_drop_obs
            + cluster.contribution_shift_obs
            + cluster.anomaly_detection_obs
            + cluster.other_obs
        )
        if not all_obs:
            raise ValueError(f"Empty non-metric cluster: {cluster.scope_key}")

        primary = max(all_obs, key=lambda o: o["significance"]["sample_size"])

        supporting_ids: list[str] = [primary["observation_id"]]
        consistency_factors: list[float] = [1.0]
        support_reasons: list[str] = []
        alignment_notes: list[str] = []

        for obs in all_obs:
            if obs is primary:
                continue
            if obs["significance"].get("practical_significance"):
                supporting_ids.append(obs["observation_id"])
                obs_type = obs["type"]
                if obs_type == "funnel_drop":
                    reason = f"funnel drop at {obs['payload'].get('worst_stage', 'unknown')}"
                elif obs_type == "contribution_shift":
                    reason = f"contribution shift in {obs['payload'].get('biggest_shift_segment', 'unknown')}"
                elif obs_type == "anomaly_detection":
                    reason = "statistical anomaly detected"
                else:
                    reason = f"{obs_type} finding"
                support_reasons.append(reason)
                consistency_factors.append(0.85)
                alignment_notes.append(f"non_metric supporting: {reason}")

        consistency = sum(consistency_factors) / len(consistency_factors)
        sample_score = min(1.0, primary["significance"]["sample_size"] / 150.0)
        data_quality_score = 0.95 if primary["quality"].get("sample_size_ok", True) else 0.60

        return AlignedSignal(
            scope_cluster=cluster,
            primary_obs=primary,
            primary_selection_reason="max sample_size (non-metric cluster)",
            supporting_obs_ids=supporting_ids,
            contradicting_obs_ids=[],
            effect_strength=0.50,
            consistency=consistency,
            sample_score=sample_score,
            data_quality_score=data_quality_score,
            contradiction_penalty=0.0,
            consistency_factors=consistency_factors,
            support_reasons=support_reasons,
            alignment_notes=alignment_notes,
        )


def _same_direction(delta_a: float, delta_b: float) -> bool:
    """Return True if two deltas point in the same direction."""
    if delta_a == 0.0 or delta_b == 0.0:
        return True
    return (delta_a > 0) == (delta_b > 0)
