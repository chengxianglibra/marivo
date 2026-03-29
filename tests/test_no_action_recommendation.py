"""Tests for no_action_required recommendation type (roadmap 1.3)."""

from __future__ import annotations

import unittest

from app.evidence_engine.recommendation_policy import (
    DefaultRecommendationPolicy,
    _get_claim_delta,
)
from app.evidence_engine.schemas import REC_TYPE_ACTION, REC_TYPE_NO_ACTION


def _obs(obs_id: str, metric: str, delta_pct: float) -> dict:
    return {
        "observation_id": obs_id,
        "type": "metric_observation",
        "subject": {"metric": metric, "slice": {}},
        "payload": {"delta_pct": delta_pct, "current_value": 100},
        "significance": {"practical_significance": True},
        "quality": {},
    }


def _claim(
    claim_id: str,
    metric: str,
    obs_id: str,
    delta_pct: float,
    *,
    status: str = "confirmed",
    confidence: float = 0.9,
    slice_dict: dict | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "type": "root_cause_candidate",
        "text": f"{metric} changed",
        "scope": {"metric": metric, "slice": slice_dict or {}},
        "confidence": confidence,
        "status": status,
        "supporting_observations": [obs_id],
        "contradicting_observations": [],
        "confidence_breakdown": {
            "primary_delta_pct": delta_pct,
            "primary_direction": "up" if delta_pct > 0 else "down",
            "current_value": 100,
        },
        "inference_level": "L0",
        "inference_justification": [],
    }


class NoActionRecommendationTests(unittest.TestCase):
    """Tests for no_action_required recommendation detection."""

    def test_small_delta_produces_no_action(self) -> None:
        """delta_pct < 5% should produce no_action_required regardless of direction."""
        obs = [_obs("obs1", "cpu_time", 3.2)]
        claims = [_claim("c1", "cpu_time", "obs1", 3.2)]
        policy = DefaultRecommendationPolicy()
        recs = policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["type"], REC_TYPE_NO_ACTION)
        self.assertEqual(recs[0]["priority"], "P3")

    def test_desired_direction_aligned_produces_no_action(self) -> None:
        """Metric with desired_direction='down' and negative delta → no action."""
        directions = {"queued_time": "down"}
        resolver = lambda name: directions.get(name)
        obs = [_obs("obs1", "queued_time", -71.5)]
        claims = [_claim("c1", "queued_time", "obs1", -71.5)]
        policy = DefaultRecommendationPolicy(metric_direction_resolver=resolver)
        recs = policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["type"], REC_TYPE_NO_ACTION)

    def test_desired_direction_up_aligned_produces_no_action(self) -> None:
        """Metric with desired_direction='up' and positive delta → no action."""
        resolver = lambda name: "up" if name == "throughput" else None
        obs = [_obs("obs1", "throughput", 25.0)]
        claims = [_claim("c1", "throughput", "obs1", 25.0)]
        policy = DefaultRecommendationPolicy(metric_direction_resolver=resolver)
        recs = policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["type"], REC_TYPE_NO_ACTION)

    def test_desired_direction_misaligned_produces_action(self) -> None:
        """Metric with desired_direction='down' but positive delta → action required."""
        resolver = lambda name: "down" if name == "queued_time" else None
        obs = [_obs("obs1", "queued_time", 58.5)]
        claims = [_claim("c1", "queued_time", "obs1", 58.5)]
        policy = DefaultRecommendationPolicy(metric_direction_resolver=resolver)
        recs = policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["type"], REC_TYPE_ACTION)

    def test_no_direction_large_delta_produces_action(self) -> None:
        """No desired_direction and large delta → action required."""
        obs = [_obs("obs1", "query_count", 30.0)]
        claims = [_claim("c1", "query_count", "obs1", 30.0)]
        policy = DefaultRecommendationPolicy()
        recs = policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["type"], REC_TYPE_ACTION)

    def test_neutral_direction_large_delta_produces_action(self) -> None:
        """desired_direction='neutral' treated like None — large delta → action."""
        resolver = lambda name: "neutral"
        obs = [_obs("obs1", "query_count", 30.0)]
        claims = [_claim("c1", "query_count", "obs1", 30.0)]
        policy = DefaultRecommendationPolicy(metric_direction_resolver=resolver)
        recs = policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["type"], REC_TYPE_ACTION)

    def test_no_action_has_p3_priority_and_none_risk(self) -> None:
        """No-action recs should have P3 priority and 'none' risk."""
        obs = [_obs("obs1", "cpu_time", 2.0)]
        claims = [_claim("c1", "cpu_time", "obs1", 2.0)]
        policy = DefaultRecommendationPolicy()
        recs = policy.derive(obs, claims, [])
        self.assertEqual(recs[0]["priority"], "P3")
        self.assertEqual(recs[0]["risk"], "none")
        self.assertIsNone(recs[0]["causal_basis"])

    def test_mixed_claims_split_correctly(self) -> None:
        """Some claims need action, some don't — both types produced."""
        resolver = lambda name: "down" if name == "queued_time" else None
        obs = [
            _obs("obs1", "query_count", 30.0),
            _obs("obs2", "queued_time", -71.5),
            _obs("obs3", "cpu_time", 2.0),  # small delta
        ]
        claims = [
            _claim("c1", "query_count", "obs1", 30.0, slice_dict={"user": "sys_titan"}),
            _claim("c2", "queued_time", "obs2", -71.5, slice_dict={"user": "sys_oneservice"}),
            _claim("c3", "cpu_time", "obs3", 2.0, slice_dict={"user": "sys_titan"}),
        ]
        policy = DefaultRecommendationPolicy(metric_direction_resolver=resolver)
        recs = policy.derive(obs, claims, [])

        action_recs = [r for r in recs if r["type"] == REC_TYPE_ACTION]
        no_action_recs = [r for r in recs if r["type"] == REC_TYPE_NO_ACTION]

        self.assertEqual(len(action_recs), 1)  # query_count for sys_titan
        self.assertEqual(len(no_action_recs), 2)  # queued_time aligned + cpu_time small delta

    def test_no_action_excluded_from_existing_recommendations(self) -> None:
        """Existing recommendations are no longer passed through by the policy layer."""
        existing = [
            {
                "rec_id": "rec_existing",
                "type": REC_TYPE_ACTION,
                "claim_id": "c1",
                "action_text": "existing",
                "priority": "P1",
                "expected_impact": "",
                "risk": "P1",
                "validation_metric": {},
                "causal_basis": None,
                "supporting_claims": None,
            }
        ]
        policy = DefaultRecommendationPolicy()
        recs = policy.derive([], [], existing)
        self.assertEqual(recs, [])


class GetClaimDeltaTests(unittest.TestCase):
    """Tests for the _get_claim_delta helper."""

    def test_extracts_delta_from_observation(self) -> None:
        claim = _claim("c1", "m", "obs1", -14.2)
        self.assertAlmostEqual(_get_claim_delta(claim), -14.2)

    def test_returns_none_when_no_observations(self) -> None:
        claim = _claim("c1", "m", "obs_missing", 0.0)
        claim["confidence_breakdown"] = {}
        self.assertIsNone(_get_claim_delta(claim))


if __name__ == "__main__":
    unittest.main()
