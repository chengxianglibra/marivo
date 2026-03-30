from __future__ import annotations

from uuid import uuid4

from app.evidence_engine.schemas import Claim, Observation
from app.evidence_engine.scoring import score_confidence
from app.evidence_engine.synthesizers.stages import AlignedSignal, ClaimFormulation


class ClaimFormulator:
    """Stage 3: Generates claim text and computes confidence from AlignedSignals.

    Uses score_confidence() from app.evidence_engine.scoring for all
    numeric confidence calculations.  Determines claim type
    (root_cause_candidate / overall_trend / finding) from the structure
    of aligned signals.  Text templates are extracted from the existing
    synthesize_claims() heuristics for full parity.
    """

    def formulate(self, signal: AlignedSignal) -> ClaimFormulation:
        """Produce a ClaimFormulation for one AlignedSignal.

        Audit contract: every returned ClaimFormulation has
        - claim_type_decision + claim_type_reason populated
        - text_template populated
        - confidence_inputs populated (all 5 score_confidence params)
        - final_confidence == score_confidence(**confidence_inputs)
        """
        primary = signal.primary_obs
        obs_type = primary["type"]

        if (
            obs_type == "metric_observation"
            and primary.get("payload", {}).get("delta_pct") is not None
        ):
            return self._formulate_metric(signal)
        return self._formulate_non_metric(signal)

    def formulate_overall_trend(
        self,
        signals: list[AlignedSignal],
        metric_observation_obs: list[Observation],
    ) -> ClaimFormulation | None:
        """Optionally generate an overall_trend claim across multiple signals.

        Returns None when fewer than 2 distinct metrics are present.
        """
        metric_observation_with_delta = [
            observation
            for observation in metric_observation_obs
            if observation.get("payload", {}).get("delta_pct") is not None
        ]
        distinct_metrics = {
            o.get("subject", {}).get("metric") for o in metric_observation_with_delta
        }
        if len(distinct_metrics) < 2:
            return None

        declining = [
            o for o in metric_observation_with_delta if float(o["payload"]["delta_pct"]) < 0
        ]
        improving = [
            o for o in metric_observation_with_delta if float(o["payload"]["delta_pct"]) > 0
        ]
        trend_parts = []
        if declining:
            trend_parts.append(f"{len(declining)} declining")
        if improving:
            trend_parts.append(f"{len(improving)} improving")
        broad_direction = "decline" if len(declining) >= len(improving) else "shift"
        trend_text = (
            f"Across {len(distinct_metrics)} metrics ({', '.join(trend_parts)}), "
            f"the overall pattern suggests a broad {broad_direction}."
        )

        claim: Claim = {
            "claim_id": f"claim_{uuid4().hex[:12]}",
            "type": "overall_trend",
            "text": trend_text,
            "scope": {"slice": {}},
            "confidence": 0.0,
            "status": "supported",
            "supporting_observations": [o["observation_id"] for o in metric_observation_with_delta],
            "contradicting_observations": [],
            "confidence_breakdown": {},
            "inference_level": "L0",
            "inference_justification": [],
        }

        return ClaimFormulation(
            claim=claim,
            claim_type_decision="overall_trend",
            claim_type_reason=f"{len(distinct_metrics)} distinct metrics observed",
            text_template="multi_metric_trend",
            confidence_inputs={},
            final_confidence=0.0,
            is_non_metric=False,
        )

    # ── private ───────────────────────────────────────────────────────────────

    def _formulate_metric(self, signal: AlignedSignal) -> ClaimFormulation:
        primary = signal.primary_obs
        impacted_slice = primary["subject"].get("slice", {})

        if impacted_slice:
            slice_label = " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
        else:
            slice_label = "overall"

        reason_label = (
            " and ".join(signal.support_reasons)
            if signal.support_reasons
            else "localized traffic changes"
        )

        text = (
            f"Metric decline is concentrated in {slice_label} traffic, "
            f"with {reason_label} acting as the leading driver."
        )

        confidence_inputs = {
            "effect_strength": round(signal.effect_strength, 2),
            "consistency": round(signal.consistency, 2),
            "sample_score": round(signal.sample_score, 2),
            "data_quality_score": round(signal.data_quality_score, 2),
            "contradiction_penalty": round(signal.contradiction_penalty, 2),
        }
        recommendation_metadata = {
            "primary_delta_pct": round(float(primary["payload"].get("delta_pct", 0.0)), 2),
            "primary_direction": "up"
            if float(primary["payload"].get("delta_pct", 0.0)) > 0
            else "down",
            "current_value": primary["payload"].get("current_value"),
        }
        final_confidence = score_confidence(**confidence_inputs)

        claim: Claim = {
            "claim_id": f"claim_{uuid4().hex[:12]}",
            "type": "root_cause_candidate",
            "text": text,
            "scope": {
                "metric": primary["subject"].get("metric", ""),
                "slice": impacted_slice,
            },
            "confidence": 0.0,
            "status": "supported",
            "supporting_observations": signal.supporting_obs_ids,
            "contradicting_observations": signal.contradicting_obs_ids,
            "confidence_breakdown": {
                **confidence_inputs,
                **recommendation_metadata,
            },
            "inference_level": "L0",
            "inference_justification": [],
        }

        return ClaimFormulation(
            claim=claim,
            claim_type_decision="root_cause_candidate",
            claim_type_reason="dominant metric_observation scope",
            text_template="metric_decline_concentrated",
            confidence_inputs=confidence_inputs,
            final_confidence=final_confidence,
            is_non_metric=False,
        )

    def _formulate_non_metric(self, signal: AlignedSignal) -> ClaimFormulation:
        primary = signal.primary_obs
        obs_type = primary["type"]
        impacted_slice = primary["subject"].get("slice", {})
        payload = primary.get("payload", {})

        if obs_type == "metric_observation":
            current_value = payload.get("current_value")
            if impacted_slice:
                slice_label = " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
            else:
                slice_label = "overall"
            if isinstance(current_value, (int, float)):
                text = f"Current window observation for {slice_label}: value={current_value}."
            else:
                text = f"Current window observation recorded for {slice_label}."
            template = "metric_current_window_observation"
        elif obs_type == "funnel_drop":
            stage = payload.get("worst_stage", "unknown")
            text = f"Funnel drop detected at stage '{stage}' with significant conversion loss."
            template = "funnel_drop_detected"
        elif obs_type == "contribution_shift":
            segment = payload.get("biggest_shift_segment", "unknown")
            text = f"Contribution shift detected in segment '{segment}' indicating redistribution."
            template = "contribution_shift_detected"
        elif obs_type == "anomaly_detection":
            z_score = payload.get("z_score", 0)
            text = (
                f"Statistical anomaly detected (z-score: {z_score}) indicating abnormal behavior."
            )
            template = "anomaly_detected"
        else:
            text = f"Finding of type '{obs_type}' detected with practical significance."
            template = "generic_finding"

        if signal.support_reasons:
            if impacted_slice:
                slice_label = " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
            else:
                slice_label = "overall"
            text += f" Corroborated by {' and '.join(signal.support_reasons)} in {slice_label}."

        confidence_inputs = {
            "effect_strength": 0.50,
            "consistency": round(signal.consistency, 2),
            "sample_score": round(signal.sample_score, 2),
            "data_quality_score": round(signal.data_quality_score, 2),
            "contradiction_penalty": 0.0,
        }
        final_confidence = score_confidence(**confidence_inputs)

        claim: Claim = {
            "claim_id": f"claim_{uuid4().hex[:12]}",
            "type": "finding",
            "text": text,
            "scope": {
                "metric": primary["subject"].get("metric", ""),
                "slice": impacted_slice,
            },
            "confidence": 0.0,
            "status": "supported",
            "supporting_observations": signal.supporting_obs_ids,
            "contradicting_observations": signal.contradicting_obs_ids,
            "confidence_breakdown": confidence_inputs,
            "inference_level": "L0",
            "inference_justification": [],
        }

        return ClaimFormulation(
            claim=claim,
            claim_type_decision="finding",
            claim_type_reason="non-metric observation cluster",
            text_template=template,
            confidence_inputs=confidence_inputs,
            final_confidence=final_confidence,
            is_non_metric=True,
        )
