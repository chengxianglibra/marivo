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


class DefaultClaimRelationDiscoveryTests(unittest.TestCase):
    def _obs(self, obs_id: str, metric: str, delta_pct: float, slice_dict: dict) -> dict:
        return {
            "observation_id": obs_id,
            "type": "metric_change",
            "subject": {"metric": metric, "slice": slice_dict},
            "payload": {"delta_pct": delta_pct, "current_value": 100},
            "significance": {"sample_size": 120, "practical_significance": True},
            "quality": {"sample_size_ok": True, "freshness_ok": True},
        }

    def _claim(self, claim_id: str, metric: str, obs_id: str, slice_dict: dict) -> dict:
        return {
            "claim_id": claim_id,
            "type": "root_cause_candidate",
            "text": f"claim {claim_id}",
            "scope": {"metric": metric, "slice": slice_dict},
            "confidence": 0.72,
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

    def test_default_relation_discovery_emits_claim_to_claim_edge_with_provenance(self) -> None:
        observations = [
            self._obs("obs_q", "query_count", 30.0, {"user": "sys_titan"}),
            self._obs("obs_t", "queued_time", 58.5, {"user": "sys_titan"}),
        ]
        claims = [
            self._claim("claim_q", "query_count", "obs_q", {"user": "sys_titan"}),
            self._claim("claim_t", "queued_time", "obs_t", {"user": "sys_titan"}),
        ]

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)

        correlates = [
            edge for edge in result["edges"]
            if edge["edge_type"] == "correlates_with"
            and edge["from_node_type"] == "claim"
            and edge["to_node_type"] == "claim"
        ]
        self.assertEqual(len(correlates), 1)
        relation = correlates[0]
        self.assertEqual(relation["match_basis"]["category"], "exact_match")
        self.assertEqual(relation["match_basis"]["direction"], "up")
        self.assertIn("scope_match", relation["score_components"])
        self.assertEqual(sorted(relation["supporting_observation_ids"]), ["obs_q", "obs_t"])

    def test_default_relation_discovery_skips_non_confirmed_claims(self) -> None:
        observations = [
            self._obs("obs_q", "query_count", 30.0, {"user": "sys_titan"}),
            self._obs("obs_t", "queued_time", 58.5, {"user": "sys_titan"}),
        ]
        claims = [
            self._claim("claim_q", "query_count", "obs_q", {"user": "sys_titan"}),
            {
                **self._claim("claim_t", "queued_time", "obs_t", {"user": "sys_titan"}),
                "status": "tentative",
            },
        ]

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)
        correlates = [edge for edge in result["edges"] if edge["edge_type"] == "correlates_with"]
        self.assertEqual(correlates, [])

    def test_pipeline_promotes_cross_metric_claim_group_to_l1_without_new_causal_edges(self) -> None:
        observations = [
            self._obs("obs_q", "query_count", 30.0, {"user": "sys_titan"}),
            self._obs("obs_c", "cpu_time", 8.6, {"user": "sys_titan"}),
            self._obs("obs_t", "queued_time", 58.5, {"user": "sys_titan"}),
        ]
        claims = [
            self._claim("claim_q", "query_count", "obs_q", {"user": "sys_titan"}),
            self._claim("claim_c", "cpu_time", "obs_c", {"user": "sys_titan"}),
            self._claim("claim_t", "queued_time", "obs_t", {"user": "sys_titan"}),
        ]

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)

        levels = {claim["claim_id"]: claim["inference_level"] for claim in result["claims"]}
        self.assertEqual(levels["claim_q"], "L1")
        self.assertEqual(levels["claim_c"], "L1")
        self.assertEqual(levels["claim_t"], "L1")

        justifications = {
            claim["claim_id"]: claim["inference_justification"]
            for claim in result["claims"]
        }
        self.assertTrue(any("cross_metric_consistency" in token for token in justifications["claim_q"]))

        causal_edges = [
            edge for edge in result["edges"]
            if edge["edge_type"] in {"temporally_precedes", "mechanistically_explains"}
        ]
        self.assertEqual(causal_edges, [])

    def test_pipeline_emits_cross_metric_correlation_derived_observation_for_component(self) -> None:
        observations = [
            self._obs("obs_q", "query_count", 30.0, {"user": "sys_titan"}),
            self._obs("obs_c", "cpu_time", 8.6, {"user": "sys_titan"}),
            self._obs("obs_t", "queued_time", 58.5, {"user": "sys_titan"}),
        ]
        claims = [
            self._claim("claim_q", "query_count", "obs_q", {"user": "sys_titan"}),
            self._claim("claim_c", "cpu_time", "obs_c", {"user": "sys_titan"}),
            self._claim("claim_t", "queued_time", "obs_t", {"user": "sys_titan"}),
        ]

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)

        derived = [
            obs for obs in result["derived_observations"]
            if obs["type"] == "cross_metric_correlation"
        ]
        self.assertEqual(len(derived), 1)
        payload = derived[0]["payload"]
        self.assertEqual(payload["group_direction"], "group_up")
        self.assertEqual(payload["shared_slice"], {"user": "sys_titan"})
        self.assertEqual(payload["component_size"], 3)
        self.assertEqual(
            payload["metrics"],
            ["cpu_time", "query_count", "queued_time"],
        )

    def test_pipeline_emits_temporal_pattern_derived_observation_from_hourly_support(self) -> None:
        observations = [
            self._obs(
                "obs_h1",
                "query_count",
                5.0,
                {"user": "sys_titan", "log_hour": "2024-01-01T01:00"},
            ),
            self._obs(
                "obs_h2",
                "query_count",
                9.0,
                {"user": "sys_titan", "log_hour": "2024-01-01T02:00"},
            ),
            self._obs(
                "obs_h3",
                "query_count",
                6.0,
                {"user": "sys_titan", "log_hour": "2024-01-01T03:00"},
            ),
        ]
        observations[0]["payload"]["current_value"] = 100.0
        observations[1]["payload"]["current_value"] = 180.0
        observations[2]["payload"]["current_value"] = 120.0
        observations[0]["observed_window"] = {"start": "2024-01-01T01:00", "end": "2024-01-01T02:00", "granularity": "hour"}
        observations[1]["observed_window"] = {"start": "2024-01-01T02:00", "end": "2024-01-01T03:00", "granularity": "hour"}
        observations[2]["observed_window"] = {"start": "2024-01-01T03:00", "end": "2024-01-01T04:00", "granularity": "hour"}
        observations[0]["subject"]["temporal_group_by_columns"] = ["log_hour"]
        observations[1]["subject"]["temporal_group_by_columns"] = ["log_hour"]
        observations[2]["subject"]["temporal_group_by_columns"] = ["log_hour"]

        claims = [
            self._claim("claim_q", "query_count", "obs_h1", {"user": "sys_titan"}),
        ]
        claims[0]["supporting_observations"] = ["obs_h1", "obs_h2", "obs_h3"]

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)

        derived = [
            obs for obs in result["derived_observations"]
            if obs["type"] == "temporal_pattern"
        ]
        self.assertEqual(len(derived), 1)
        payload = derived[0]["payload"]
        self.assertEqual(payload["pattern_type"], "spike_and_decay")
        self.assertEqual(payload["supporting_claim_id"], "claim_q")
        self.assertEqual(payload["peak_window"]["start"], "2024-01-01T02:00")
        self.assertEqual(payload["supporting_observation_ids"], ["obs_h1", "obs_h2", "obs_h3"])

    def test_pipeline_skips_temporal_pattern_without_temporal_grouping_metadata(self) -> None:
        observations = [
            self._obs("obs_h1", "query_count", 5.0, {"user": "sys_titan"}),
            self._obs("obs_h2", "query_count", 9.0, {"user": "sys_titan"}),
            self._obs("obs_h3", "query_count", 6.0, {"user": "sys_titan"}),
        ]
        observations[0]["payload"]["current_value"] = 100.0
        observations[1]["payload"]["current_value"] = 180.0
        observations[2]["payload"]["current_value"] = 120.0
        observations[0]["observed_window"] = {"start": "2024-01-01T01:00", "end": "2024-01-01T02:00", "granularity": "hour"}
        observations[1]["observed_window"] = {"start": "2024-01-01T02:00", "end": "2024-01-01T03:00", "granularity": "hour"}
        observations[2]["observed_window"] = {"start": "2024-01-01T03:00", "end": "2024-01-01T04:00", "granularity": "hour"}

        claims = [self._claim("claim_q", "query_count", "obs_h1", {"user": "sys_titan"})]
        claims[0]["supporting_observations"] = ["obs_h1", "obs_h2", "obs_h3"]

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)

        derived = [
            obs for obs in result["derived_observations"]
            if obs["type"] == "temporal_pattern"
        ]
        self.assertEqual(derived, [])

    def test_pipeline_skips_temporal_pattern_for_non_confirmed_claim(self) -> None:
        observations = [
            self._obs(
                "obs_h1",
                "query_count",
                5.0,
                {"user": "sys_titan", "log_hour": "2024-01-01T01:00"},
            ),
            self._obs(
                "obs_h2",
                "query_count",
                9.0,
                {"user": "sys_titan", "log_hour": "2024-01-01T02:00"},
            ),
            self._obs(
                "obs_h3",
                "query_count",
                6.0,
                {"user": "sys_titan", "log_hour": "2024-01-01T03:00"},
            ),
        ]
        for idx, obs in enumerate(observations, start=1):
            obs["payload"]["current_value"] = [100.0, 180.0, 120.0][idx - 1]
            obs["observed_window"] = {
                "start": f"2024-01-01T0{idx}:00",
                "end": f"2024-01-01T0{idx + 1}:00",
                "granularity": "hour",
            }
            obs["subject"]["temporal_group_by_columns"] = ["log_hour"]

        claims = [self._claim("claim_q", "query_count", "obs_h1", {"user": "sys_titan"})]
        claims[0]["supporting_observations"] = ["obs_h1", "obs_h2", "obs_h3"]
        claims[0]["status"] = "insufficient"

        pipeline = EvidencePipeline(lambda _: ([], [], []))
        result = pipeline.build_synthesis(observations, existing_claims=claims)

        derived = [
            obs for obs in result["derived_observations"]
            if obs["type"] == "temporal_pattern"
        ]
        self.assertEqual(derived, [])

if __name__ == "__main__":
    unittest.main()
