from __future__ import annotations

import unittest

from app.evidence import synthesize_claims
from app.evidence_engine import (
    ConfidenceScorer,
    EvidencePipeline,
    RecommendationPolicy,
)
from app.evidence_engine.extractors import ComparisonRowExtractor


class EvidencePluginTests(unittest.TestCase):
    def test_comparison_row_extractor_builds_observations(self) -> None:
        extractor = ComparisonRowExtractor()

        observations = extractor.extract(
            [
                {
                    "platform": "android",
                    "app_version": "8.3.1",
                    "network_type": "4g",
                    "content_type": "short",
                    "current_watch_time": 82,
                    "baseline_watch_time": 96,
                    "delta_pct": -14.2,
                    "current_sessions": 280,
                    "baseline_sessions": 285,
                }
            ],
            context={
                "metric": "watch_time",
                "observation_type": "metric_change",
                "payload_fields": {
                    "current_value": "current_watch_time",
                    "baseline_value": "baseline_watch_time",
                    "delta_pct": "delta_pct",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["type"], "metric_change")
        self.assertEqual(observations[0]["payload"]["current_value"], 82)

    def test_pipeline_supports_extractor_and_default_synthesizer(self) -> None:
        pipeline = EvidencePipeline(synthesize_claims)

        observations = pipeline.extract_observations(
            "comparison_rows",
            [
                {
                    "platform": "android",
                    "app_version": "8.3.1",
                    "network_type": "4g",
                    "content_type": "short",
                    "current_watch_time": 82,
                    "baseline_watch_time": 96,
                    "delta_pct": -14.2,
                    "current_sessions": 280,
                    "baseline_sessions": 285,
                },
            ],
            context={
                "metric": "watch_time",
                "observation_type": "metric_change",
                "payload_fields": {
                    "current_value": "current_watch_time",
                    "baseline_value": "baseline_watch_time",
                    "delta_pct": "delta_pct",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )
        observations.extend(
            pipeline.extract_observations(
                "comparison_rows",
                [
                    {
                        "platform": "android",
                        "app_version": "8.3.1",
                        "network_type": "4g",
                        "content_type": "short",
                        "current_first_frame_ms": 2200,
                        "baseline_first_frame_ms": 1800,
                        "delta_pct": 18.0,
                        "delta_ms": 400,
                        "current_sessions": 280,
                        "baseline_sessions": 285,
                    }
                ],
                context={
                    "metric": "first_frame_time",
                    "observation_type": "qoe_regression",
                    "payload_fields": {
                        "current_value": "current_first_frame_ms",
                        "baseline_value": "baseline_first_frame_ms",
                        "delta_pct": "delta_pct",
                        "delta_ms": "delta_ms",
                        "current_sessions": "current_sessions",
                        "baseline_sessions": "baseline_sessions",
                    },
                    "quality_builder": lambda row: {
                        "freshness_ok": True,
                        "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                    },
                },
            )
        )

        synthesis = pipeline.build_synthesis(observations)

        self.assertGreaterEqual(len(synthesis["claims"]), 1)
        self.assertGreaterEqual(len(synthesis["recommendations"]), 1)
        self.assertTrue(any(edge["edge_type"] == "supports" for edge in synthesis["edges"]))

    def test_pipeline_supports_custom_scorer_and_recommendation_policy(self) -> None:
        class FixedConfidenceScorer(ConfidenceScorer):
            name = "fixed"

            def score(self, observations: list[dict], claims: list[dict]) -> list[dict]:
                return [
                    {
                        **claim,
                        "confidence": 0.42,
                        "confidence_breakdown": {
                            **claim["confidence_breakdown"],
                            "scorer": self.name,
                        },
                    }
                    for claim in claims
                ]

        class FixedRecommendationPolicy(RecommendationPolicy):
            name = "fixed"

            def derive(
                self,
                observations: list[dict],
                claims: list[dict],
                recommendations: list[dict],
                relations=None,
            ) -> list[dict]:
                return [
                    {
                        "rec_id": "rec_fixed",
                        "type": "action_required",
                        "claim_id": claims[0]["claim_id"],
                        "supporting_claims": None,
                        "action_text": "Escalate fixed follow-up",
                        "priority": "P2",
                        "expected_impact": "Validate custom policy wiring.",
                        "risk": "Low",
                        "validation_metric": {"primary_metric": "watch_time"},
                        "causal_basis": None,
                    }
                ]

        pipeline = EvidencePipeline(
            synthesize_claims,
            confidence_scorers={"fixed": FixedConfidenceScorer()},
            recommendation_policies={"fixed": FixedRecommendationPolicy()},
        )
        observations = [
            {
                "observation_id": "obs_watch_1",
                "type": "metric_change",
                "subject": {
                    "metric": "watch_time",
                    "slice": {
                        "platform": "android",
                        "app_version": "8.3.1",
                        "network_type": "4g",
                        "content_type": "short",
                    },
                },
                "payload": {"delta_pct": -14.0, "current_sessions": 280, "baseline_sessions": 285},
                "significance": {"sample_size": 280, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            }
        ]

        synthesis = pipeline.build_synthesis(
            observations,
            confidence_scorer_name="fixed",
            recommendation_policy_name="fixed",
        )

        self.assertEqual(synthesis["claims"][0]["confidence"], 0.42)
        self.assertEqual(synthesis["recommendations"][0]["rec_id"], "rec_fixed")
        self.assertTrue(any(edge["edge_type"] == "justifies" for edge in synthesis["edges"]))


if __name__ == "__main__":
    unittest.main()
