from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any
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


# Priority ordering — lower index = higher priority.
_PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _slice_key(claim: Claim) -> tuple[tuple[str, Any], ...]:
    """Convert a claim's slice dict to a hashable, order-independent key."""
    slice_dict = claim.get("scope", {}).get("slice", {})
    return tuple(sorted(slice_dict.items()))


def _best_priority(*priorities: str) -> str:
    """Return the highest (most urgent) priority among the given values."""
    return min(priorities, key=lambda p: _PRIORITY_RANK.get(p, 99))


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

        confirmed_claims = [
            c for c in claims
            if c["type"] == "root_cause_candidate"
            and c["status"] in {"supported", "confirmed"}
        ]

        if confirmed_claims:
            return self._derive_from_confirmed(observations, confirmed_claims)

        # Fallback: use the highest-confidence claim regardless of status
        candidate = max(claims, key=lambda c: c["confidence"], default=None)
        if candidate is None or candidate["confidence"] < 0.2:
            return []
        return [self._single_claim_recommendation(observations, candidate)]

    # ── Multi-claim aggregation ──────────────────────────────────────────────

    def _derive_from_confirmed(
        self,
        observations: list[Observation],
        confirmed_claims: list[Claim],
    ) -> list[Recommendation]:
        # Group by slice
        groups: dict[tuple[tuple[str, Any], ...], list[Claim]] = defaultdict(list)
        for claim in confirmed_claims:
            groups[_slice_key(claim)].append(claim)

        result: list[Recommendation] = []
        for _key, group in groups.items():
            if len(group) >= 2:
                result.append(self._aggregated_recommendation(observations, group))
            else:
                result.append(self._single_claim_recommendation(observations, group[0]))
        return result

    def _aggregated_recommendation(
        self,
        observations: list[Observation],
        group: list[Claim],
    ) -> Recommendation:
        """Build one recommendation that aggregates multiple claims sharing a slice."""
        # Primary claim = highest confidence
        primary = max(group, key=lambda c: c["confidence"])
        all_claim_ids = [c["claim_id"] for c in group]

        slice_dict = primary.get("scope", {}).get("slice", {})
        scope_desc = (
            " / ".join(f"{k}={v}" for k, v in slice_dict.items())
            if slice_dict else "overall"
        )

        # Classify metrics by direction
        obs_map = {o["observation_id"]: o for o in observations}
        increased: list[str] = []
        declined: list[str] = []
        unchanged: list[str] = []
        for claim in group:
            metric = claim.get("scope", {}).get("metric", "unknown")
            # Determine direction from the primary supporting observation
            sup_ids = claim.get("supporting_observations", [])
            delta = None
            for sid in sup_ids:
                obs = obs_map.get(sid)
                if obs:
                    delta = obs.get("payload", {}).get("delta_pct")
                    if delta is not None:
                        break
            if delta is not None:
                pct = abs(float(delta))
                label = f"{metric} ({pct:.1f}%)"
                if float(delta) > 0:
                    increased.append(label)
                else:
                    declined.append(label)
            else:
                unchanged.append(metric)

        parts: list[str] = []
        if increased:
            parts.append(f"increased: {', '.join(increased)}")
        if declined:
            parts.append(f"declined: {', '.join(declined)}")
        if unchanged:
            parts.append(f"observed: {', '.join(unchanged)}")
        trend_summary = "; ".join(parts) if parts else "multiple metrics affected"

        action_text = (
            f"{scope_desc}: {trend_summary}. "
            f"Drill into this slice to identify shared root cause and consider targeted experiments."
        )

        # Priority = most urgent across the group
        priorities = []
        for claim in group:
            if claim["status"] in {"supported", "confirmed"}:
                priorities.append("P1")
            else:
                priorities.append("P2")
        priority = _best_priority(*priorities) if priorities else "P1"

        # Build metric list from the direction buckets (already computed above)
        all_metrics = [c.get("scope", {}).get("metric", "") for c in group]
        expected_impact = (
            f"Validate recovery across {len(group)} correlated metrics "
            f"before rolling strategy changes."
        )

        return {
            "rec_id": f"rec_{uuid4().hex[:12]}",
            "claim_id": primary["claim_id"],
            "supporting_claims": all_claim_ids,
            "action_text": action_text,
            "priority": priority,
            "expected_impact": expected_impact,
            "risk": "Experiment duration may delay full rollout decisions.",
            "validation_metric": {
                "primary_metric": all_metrics[0] if all_metrics else "metric_under_investigation",
                "correlated_metrics": all_metrics[1:],
            },
        }

    # ── Single claim recommendation (existing logic) ─────────────────────────

    def _single_claim_recommendation(
        self,
        observations: list[Observation],
        claim: Claim,
    ) -> Recommendation:
        impacted_slice = claim.get("scope", {}).get("slice", {})
        metric = claim.get("scope", {}).get("metric", "")
        claim_status = claim.get("status", "")

        scope_desc = (
            " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
            if impacted_slice else "overall"
        )
        metric_desc = f" for {metric}" if metric and metric != "aggregate" else ""

        # Extract one numeric value from primary observation for context
        _obs_map = {o["observation_id"]: o for o in observations}
        _primary_obs_id = (claim.get("supporting_observations") or [None])[0]
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

        return {
            "rec_id": f"rec_{uuid4().hex[:12]}",
            "claim_id": claim["claim_id"],
            "supporting_claims": None,
            "action_text": action_text,
            "priority": priority,
            "expected_impact": expected_impact,
            "risk": risk,
            "validation_metric": {
                "primary_metric": metric or "metric_under_investigation",
            },
        }
