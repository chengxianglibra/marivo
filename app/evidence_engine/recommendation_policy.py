from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

from app.evidence_engine.schemas import Claim, Observation, Recommendation


class RecommendationPolicy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def derive(
        self,
        observations: list[Observation],
        claims: list[Claim],
        recommendations: list[Recommendation],
    ) -> list[Recommendation]:
        raise NotImplementedError


class DefaultRecommendationPolicy(RecommendationPolicy):
    name = "default"

    def derive(
        self,
        observations: list[Observation],
        claims: list[Claim],
        recommendations: list[Recommendation],
    ) -> list[Recommendation]:
        if recommendations:
            return recommendations

        primary_claim = next(
            (
                claim
                for claim in claims
                if claim["type"] == "root_cause_candidate" and claim["status"] in {"supported", "confirmed"}
            ),
            None,
        )

        if not primary_claim:
            # Fallback: use the highest-confidence claim regardless of status
            # (handles M-03 promotion mode where claims may be "insufficient")
            candidate = max(claims, key=lambda c: c["confidence"], default=None)
            if candidate is None or candidate["confidence"] < 0.2:
                return recommendations
            primary_claim = candidate

        impacted_slice = primary_claim.get("scope", {}).get("slice", {})
        metric = primary_claim.get("scope", {}).get("metric", "")
        claim_status = primary_claim.get("status", "")

        scope_desc = (
            " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
            if impacted_slice else "overall"
        )
        metric_desc = f" for {metric}" if metric and metric != "aggregate" else ""

        # Extract one numeric value from primary observation for context
        _obs_map = {o["observation_id"]: o for o in observations}
        _primary_obs_id = (primary_claim.get("supporting_observations") or [None])[0]
        _primary_obs = _obs_map.get(_primary_obs_id) if _primary_obs_id else None
        _kv_hint = ""
        if _primary_obs:
            _payload = _primary_obs.get("payload", {})
            SKIP_KEYS = {"current_value", "delta_pct"}
            _numeric = [(k, v) for k, v in _payload.items()
                        if k not in SKIP_KEYS and isinstance(v, (int, float))]
            if _numeric:
                k, v = _numeric[0]
                _fmt_v = f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"
                _kv_hint = f" (observed {k}={_fmt_v})"

        if claim_status in {"supported", "confirmed"}:
            action_text = (
                f"Drill into {scope_desc} to identify root cause of "
                f"{metric or 'the observed signal'}{_kv_hint} and consider targeted experiments."
            )
            priority = "P1"
            expected_impact = "Validate metric recovery before rolling strategy changes to all users."
            risk = "Experiment duration may delay full rollout decisions."
        else:
            # Insufficient evidence — recommend further investigation
            action_text = (
                f"Collect more data{metric_desc} in scope [{scope_desc}]{_kv_hint} "
                "to confirm or refute this signal before drawing conclusions."
            )
            priority = "P2"
            expected_impact = (
                "Determines whether the observed signal is statistically significant "
                "and warrants further investigation."
            )
            risk = "Signal may be noise; avoid acting on insufficient evidence."

        derived: list[Recommendation] = []
        derived.append(
            {
                "rec_id": f"rec_{uuid4().hex[:12]}",
                "claim_id": primary_claim["claim_id"],
                "action_text": action_text,
                "priority": priority,
                "expected_impact": expected_impact,
                "risk": risk,
                "validation_metric": {
                    "primary_metric": metric or "metric_under_investigation",
                },
            }
        )
        return derived
