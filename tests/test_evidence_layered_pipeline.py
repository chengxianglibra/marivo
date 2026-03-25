from __future__ import annotations

import unittest

from app.evidence_engine.claim_relations import ClaimRelationDiscovery
from app.evidence_engine.pipeline import EvidencePipeline
from app.evidence_engine.recommendation_policy import RecommendationPolicy


def _obs(obs_id: str = "obs_1") -> dict:
    return {
        "observation_id": obs_id,
        "type": "metric_change",
        "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
        "payload": {"delta_pct": -14.2, "current_value": 100},
        "significance": {"sample_size": 120, "practical_significance": True},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


def _claim(claim_id: str, obs_id: str = "obs_1") -> dict:
    return {
        "claim_id": claim_id,
        "type": "root_cause_candidate",
        "text": f"claim {claim_id}",
        "scope": {"metric": "watch_time", "slice": {"platform": "android"}},
        "confidence": 0.7,
        "status": "confirmed",
        "supporting_observations": [obs_id],
        "contradicting_observations": [],
        "confidence_breakdown": {
            "effect_strength": 0.7,
            "consistency": 0.8,
            "sample_score": 0.8,
            "data_quality_score": 0.95,
            "contradiction_penalty": 0.0,
        },
        "inference_level": "L0",
        "inference_justification": [],
    }


class _TrackingRelationDiscovery(ClaimRelationDiscovery):
    name = "tracking"

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def discover(self, claims: list[dict], observations: list[dict], existing_edges: list[dict]) -> list[dict]:
        self.calls.append("relations")
        if len(claims) < 2:
            return []
        return [
            {
                "from_claim_id": claims[0]["claim_id"],
                "to_claim_id": claims[1]["claim_id"],
                "relation_type": "correlates_with",
                "weight": 0.6,
                "match_basis": {"source": "test"},
                "supporting_observation_ids": [],
                "explanation": "test relation",
            }
        ]


class _TrackingRegistry:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.last_relations = None

    def run_all(self, claims: list[dict], observations: list[dict], edges: list[dict], relations=None):
        self.calls.append("causal")
        self.last_relations = relations
        return []


class _TrackingPolicy(RecommendationPolicy):
    name = "tracking"

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.last_relations = None

    def derive(self, observations: list[dict], claims: list[dict], recommendations: list[dict], relations=None) -> list[dict]:
        self.calls.append("recommendations")
        self.last_relations = relations
        if not claims:
            return []
        return [
            {
                "rec_id": "rec_tracking",
                "type": "action_required",
                "claim_id": claims[0]["claim_id"],
                "supporting_claims": [claim["claim_id"] for claim in claims],
                "action_text": "follow up",
                "priority": "P1",
                "expected_impact": "test",
                "risk": "low",
                "validation_metric": {"primary_metric": "watch_time"},
                "causal_basis": None,
            }
        ]


class LayeredEvidencePipelineTests(unittest.TestCase):
    def test_build_synthesis_executes_layered_flow(self) -> None:
        calls: list[str] = []

        def synth(observations: list[dict]):
            calls.append("synthesize")
            return [_claim("c1"), _claim("c2")], [], []

        registry = _TrackingRegistry(calls)
        policy = _TrackingPolicy(calls)
        pipeline = EvidencePipeline(
            synth,
            relation_discoveries={"tracking": _TrackingRelationDiscovery(calls)},
            causal_checker_registry=registry,
            recommendation_policies={"tracking": policy},
        )

        result = pipeline.build_synthesis(
            [_obs()],
            relation_discovery_name="tracking",
            recommendation_policy_name="tracking",
        )

        self.assertEqual(calls, ["synthesize", "relations", "causal", "recommendations"])
        self.assertEqual(result["recommendations"][0]["rec_id"], "rec_tracking")
        self.assertTrue(any(edge["edge_type"] == "supports" for edge in result["edges"]))
        self.assertTrue(any(edge["edge_type"] == "correlates_with" for edge in result["edges"]))
        self.assertTrue(any(edge["edge_type"] == "justifies" for edge in result["edges"]))
        self.assertEqual(len(registry.last_relations), 1)
        self.assertEqual(len(policy.last_relations), 1)

    def test_existing_claims_skips_claim_synthesis_but_runs_remaining_layers(self) -> None:
        calls: list[str] = []

        def synth(observations: list[dict]):
            calls.append("synthesize")
            raise AssertionError("claim synthesis should be skipped when existing_claims is provided")

        registry = _TrackingRegistry(calls)
        policy = _TrackingPolicy(calls)
        pipeline = EvidencePipeline(
            synth,
            relation_discoveries={"tracking": _TrackingRelationDiscovery(calls)},
            causal_checker_registry=registry,
            recommendation_policies={"tracking": policy},
        )

        result = pipeline.build_synthesis(
            [_obs()],
            existing_claims=[_claim("c1"), _claim("c2")],
            relation_discovery_name="tracking",
            recommendation_policy_name="tracking",
        )

        self.assertEqual(calls, ["relations", "causal", "recommendations"])
        self.assertEqual(result["claims"][0]["claim_id"], "c1")
        self.assertTrue(any(edge["edge_type"] == "correlates_with" for edge in result["edges"]))

    def test_empty_existing_claims_still_skips_claim_synthesis(self) -> None:
        calls: list[str] = []

        def synth(observations: list[dict]):
            calls.append("synthesize")
            raise AssertionError("claim synthesis should be skipped when existing_claims is explicit")

        registry = _TrackingRegistry(calls)
        policy = _TrackingPolicy(calls)
        pipeline = EvidencePipeline(
            synth,
            relation_discoveries={"tracking": _TrackingRelationDiscovery(calls)},
            causal_checker_registry=registry,
            recommendation_policies={"tracking": policy},
        )

        result = pipeline.build_synthesis(
            [_obs()],
            existing_claims=[],
            relation_discovery_name="tracking",
            recommendation_policy_name="tracking",
        )

        self.assertEqual(calls, ["relations", "causal", "recommendations"])
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["recommendations"], [])

if __name__ == "__main__":
    unittest.main()
