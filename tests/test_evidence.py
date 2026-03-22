from __future__ import annotations

import unittest

from tests.shared_fixtures import get_seeded_duckdb_path
from app.evidence import (
    make_observation,
    make_funnel_observation,
    make_contribution_observation,
    make_anomaly_observation,
    score_confidence,
    synthesize_claims,
)
from app.evidence_engine import EvidencePipeline


class ObservationFactoryTests(unittest.TestCase):
    """Tests for observation factory functions."""

    def test_make_observation_metric_change(self) -> None:
        row = {"platform": "android", "app_version": "8.3.1", "network_type": "4g", "content_type": "short"}
        obs = make_observation(
            "metric_change", "watch_time", row,
            {"current_value": 82, "baseline_value": 96, "delta_pct": -14.2, "current_sessions": 280, "baseline_sessions": 285},
            {"freshness_ok": True, "sample_size_ok": True},
        )
        self.assertTrue(obs["observation_id"].startswith("obs_"))
        self.assertEqual(obs["type"], "metric_change")
        self.assertEqual(obs["subject"]["metric"], "watch_time")
        self.assertEqual(obs["subject"]["slice"]["platform"], "android")
        self.assertTrue(obs["significance"]["practical_significance"])

    def test_make_funnel_observation(self) -> None:
        stages = [
            {"stage_name": "impression", "users": 1000, "drop_rate": 0.30, "delta_drop_rate": 0.02},
            {"stage_name": "click", "users": 700, "drop_rate": 0.40, "delta_drop_rate": 0.08},
            {"stage_name": "play", "users": 420, "drop_rate": 0.20, "delta_drop_rate": -0.01},
        ]
        obs = make_funnel_observation("engagement_funnel", stages, {"freshness_ok": True})
        self.assertTrue(obs["observation_id"].startswith("obs_"))
        self.assertEqual(obs["type"], "funnel_drop")
        self.assertEqual(obs["payload"]["worst_stage"], "click")
        self.assertEqual(obs["payload"]["worst_delta_drop_rate"], 0.08)
        self.assertTrue(obs["significance"]["practical_significance"])

    def test_make_funnel_observation_no_significance(self) -> None:
        stages = [
            {"stage_name": "impression", "users": 1000, "drop_rate": 0.30, "delta_drop_rate": 0.01},
            {"stage_name": "click", "users": 700, "drop_rate": 0.40, "delta_drop_rate": 0.02},
        ]
        obs = make_funnel_observation("small_funnel", stages, {"freshness_ok": True})
        self.assertFalse(obs["significance"]["practical_significance"])

    def test_make_contribution_observation(self) -> None:
        contributions = [
            {"segment_value": "android", "current_share": 0.60, "baseline_share": 0.50, "delta_share": 0.10, "current_count": 600},
            {"segment_value": "ios", "current_share": 0.30, "baseline_share": 0.35, "delta_share": -0.05, "current_count": 300},
            {"segment_value": "web", "current_share": 0.10, "baseline_share": 0.15, "delta_share": -0.05, "current_count": 100},
        ]
        obs = make_contribution_observation("watch_time", "platform", contributions, {"freshness_ok": True})
        self.assertTrue(obs["observation_id"].startswith("obs_"))
        self.assertEqual(obs["type"], "contribution_shift")
        self.assertEqual(obs["payload"]["biggest_shift_segment"], "android")
        self.assertEqual(obs["payload"]["biggest_delta_share"], 0.10)
        self.assertTrue(obs["significance"]["practical_significance"])

    def test_make_anomaly_observation(self) -> None:
        obs = make_anomaly_observation(
            "watch_time",
            {"platform": "android", "app_version": "8.3.1"},
            {"value": 60, "mean": 90, "stddev": 10, "z_score": -3.0, "is_anomaly": True, "sample_size": 500},
            {"freshness_ok": True},
        )
        self.assertTrue(obs["observation_id"].startswith("obs_"))
        self.assertEqual(obs["type"], "anomaly_detection")
        self.assertEqual(obs["payload"]["z_score"], -3.0)
        self.assertTrue(obs["significance"]["practical_significance"])

    def test_make_anomaly_observation_no_significance(self) -> None:
        obs = make_anomaly_observation(
            "watch_time",
            {"platform": "ios"},
            {"value": 88, "mean": 90, "stddev": 10, "z_score": -0.2, "is_anomaly": False, "sample_size": 500},
            {"freshness_ok": True},
        )
        self.assertFalse(obs["significance"]["practical_significance"])


class SynthesizeClaimsWithNewTypesTests(unittest.TestCase):
    """Tests that new observation types are incorporated in claim synthesis."""

    def _base_observations(self) -> list[dict]:
        """Minimal set of observations to trigger claim synthesis."""
        return [
            {
                "observation_id": "obs_watch_1",
                "type": "metric_change",
                "subject": {"metric": "watch_time", "slice": {"platform": "android", "app_version": "8.3.1", "network_type": "4g", "content_type": "short"}},
                "payload": {"delta_pct": -14.0, "current_sessions": 280, "baseline_sessions": 285},
                "significance": {"sample_size": 280, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
            {
                "observation_id": "obs_qoe_1",
                "type": "qoe_regression",
                "subject": {"metric": "first_frame_time", "slice": {"platform": "android", "app_version": "8.3.1", "network_type": "4g", "content_type": "short"}},
                "payload": {"delta_pct": 18.0, "current_sessions": 280, "baseline_sessions": 285},
                "significance": {"sample_size": 280, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]

    def test_funnel_observation_added_to_supports(self) -> None:
        obs = self._base_observations()
        obs.append({
            "observation_id": "obs_funnel_1",
            "type": "funnel_drop",
            "subject": {"metric": "engagement_funnel", "slice": {"funnel": "engagement_funnel", "worst_stage": "click"}},
            "payload": {"worst_stage": "click", "worst_delta_drop_rate": 0.08, "stages": []},
            "significance": {"sample_size": 500, "practical_significance": True},
            "quality": {"freshness_ok": True},
        })
        claims, _, _ = synthesize_claims(obs)
        self.assertGreaterEqual(len(claims), 1)
        self.assertIn("obs_funnel_1", claims[0]["supporting_observations"])

    def test_contribution_observation_added_to_supports(self) -> None:
        obs = self._base_observations()
        obs.append({
            "observation_id": "obs_contrib_1",
            "type": "contribution_shift",
            "subject": {"metric": "watch_time", "slice": {"segment": "platform", "biggest_shift": "android"}},
            "payload": {"biggest_shift_segment": "android", "biggest_delta_share": 0.10, "segment_name": "platform", "contributions": []},
            "significance": {"sample_size": 1000, "practical_significance": True},
            "quality": {"freshness_ok": True},
        })
        claims, _, _ = synthesize_claims(obs)
        self.assertIn("obs_contrib_1", claims[0]["supporting_observations"])

    def test_anomaly_observation_added_to_supports(self) -> None:
        obs = self._base_observations()
        obs.append({
            "observation_id": "obs_anomaly_1",
            "type": "anomaly_detection",
            "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
            "payload": {"z_score": -3.0, "is_anomaly": True, "sample_size": 500},
            "significance": {"sample_size": 500, "practical_significance": True},
            "quality": {"freshness_ok": True},
        })
        claims, _, _ = synthesize_claims(obs)
        self.assertIn("obs_anomaly_1", claims[0]["supporting_observations"])

    def test_insignificant_new_types_not_added(self) -> None:
        obs = self._base_observations()
        obs.append({
            "observation_id": "obs_funnel_weak",
            "type": "funnel_drop",
            "subject": {"metric": "f", "slice": {"funnel": "f", "worst_stage": "s"}},
            "payload": {"worst_stage": "s", "worst_delta_drop_rate": 0.01, "stages": []},
            "significance": {"sample_size": 500, "practical_significance": False},
            "quality": {"freshness_ok": True},
        })
        claims, _, _ = synthesize_claims(obs)
        self.assertNotIn("obs_funnel_weak", claims[0]["supporting_observations"])


class WeightedPrimarySelectionTests(unittest.TestCase):
    """Fix 4: synthesize_claims should prefer observations with high effect AND high sample size."""

    def test_synthesize_prefers_high_sample_observation(self) -> None:
        """Two observations with same delta_pct but different sample sizes;
        the higher-sample one should be chosen as primary."""
        obs = [
            {
                "observation_id": "obs_small",
                "type": "metric_change",
                "subject": {"metric": "watch_time", "slice": {"platform": "web"}},
                "payload": {"delta_pct": -10.0, "current_sessions": 20, "baseline_sessions": 25},
                "significance": {"sample_size": 20, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": False},
            },
            {
                "observation_id": "obs_large",
                "type": "metric_change",
                "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
                "payload": {"delta_pct": -10.0, "current_sessions": 5000, "baseline_sessions": 5200},
                "significance": {"sample_size": 5000, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]
        claims, _, _ = synthesize_claims(obs)
        self.assertGreaterEqual(len(claims), 1)
        # Primary claim should be driven by obs_large (higher sample size)
        self.assertIn("obs_large", claims[0]["supporting_observations"])
        self.assertEqual(claims[0]["scope"]["slice"]["platform"], "android")

    def test_overall_trend_claim_with_multiple_metrics(self) -> None:
        """When multiple distinct metrics are observed, an overall_trend claim should be generated."""
        obs = [
            {
                "observation_id": "obs_wt",
                "type": "metric_change",
                "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
                "payload": {"delta_pct": -12.0, "current_sessions": 300, "baseline_sessions": 310},
                "significance": {"sample_size": 300, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
            {
                "observation_id": "obs_vv",
                "type": "metric_change",
                "subject": {"metric": "video_views", "slice": {"platform": "android"}},
                "payload": {"delta_pct": -5.0, "current_sessions": 400, "baseline_sessions": 420},
                "significance": {"sample_size": 400, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]
        claims, _, _ = synthesize_claims(obs)
        trend_claims = [c for c in claims if c["type"] == "overall_trend"]
        self.assertEqual(len(trend_claims), 1)
        self.assertIn("2 metrics", trend_claims[0]["text"])


class SynthesizeClaimsNonMetricTests(unittest.TestCase):
    """Tests for synthesize_claims when only non-metric_change observations exist."""

    def test_synthesize_claims_funnel_only(self) -> None:
        observations = [{
            "observation_id": "obs_funnel_1",
            "type": "funnel_drop",
            "subject": {"metric": "engagement_funnel", "slice": {"funnel": "engagement_funnel", "worst_stage": "click"}},
            "payload": {"worst_stage": "click", "worst_delta_drop_rate": 0.08, "stages": []},
            "significance": {"sample_size": 500, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }]
        claims, _, _ = synthesize_claims(observations)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")
        self.assertIn("Funnel drop", claims[0]["text"])
        self.assertIn("obs_funnel_1", claims[0]["supporting_observations"])

    def test_synthesize_claims_anomaly_only(self) -> None:
        observations = [{
            "observation_id": "obs_anomaly_1",
            "type": "anomaly_detection",
            "subject": {"metric": "latency", "slice": {"host": "h1"}},
            "payload": {"z_score": -3.5, "is_anomaly": True, "sample_size": 200},
            "significance": {"sample_size": 200, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }]
        claims, _, _ = synthesize_claims(observations)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")
        self.assertIn("anomaly", claims[0]["text"].lower())

    def test_synthesize_claims_empty_still_returns_empty(self) -> None:
        claims, recs, edges = synthesize_claims([])
        self.assertEqual(claims, [])
        self.assertEqual(recs, [])
        self.assertEqual(edges, [])

    def test_synthesize_claims_contribution_only(self) -> None:
        observations = [{
            "observation_id": "obs_contrib_1",
            "type": "contribution_shift",
            "subject": {"metric": "watch_time", "slice": {"segment": "platform", "biggest_shift": "android"}},
            "payload": {"biggest_shift_segment": "android", "biggest_delta_share": 0.10, "segment_name": "platform", "contributions": []},
            "significance": {"sample_size": 1000, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }]
        claims, _, _ = synthesize_claims(observations)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")
        self.assertIn("Contribution shift", claims[0]["text"])

    def test_synthesize_claims_mixed_non_metric(self) -> None:
        """Multiple non-metric observations should produce a claim with multiple supports."""
        observations = [
            {
                "observation_id": "obs_funnel_1",
                "type": "funnel_drop",
                "subject": {"metric": "f", "slice": {"funnel": "f", "worst_stage": "s"}},
                "payload": {"worst_stage": "s", "worst_delta_drop_rate": 0.08, "stages": []},
                "significance": {"sample_size": 300, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
            {
                "observation_id": "obs_anomaly_1",
                "type": "anomaly_detection",
                "subject": {"metric": "latency", "slice": {"host": "h1"}},
                "payload": {"z_score": -3.5, "is_anomaly": True, "sample_size": 500},
                "significance": {"sample_size": 500, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]
        claims, _, _ = synthesize_claims(observations)
        self.assertEqual(len(claims), 1)
        # Primary is the one with higher sample_size (anomaly)
        self.assertIn("obs_anomaly_1", claims[0]["supporting_observations"])
        self.assertIn("obs_funnel_1", claims[0]["supporting_observations"])


class EvidencePipelineTests(unittest.TestCase):
    def test_build_synthesis_adds_support_and_justification_edges(self) -> None:
        observations = [
            {
                "observation_id": "obs_watch_1",
                "type": "metric_change",
                "subject": {"metric": "watch_time", "slice": {"platform": "android", "app_version": "8.3.1", "network_type": "4g", "content_type": "short"}},
                "payload": {"delta_pct": -14.0, "current_sessions": 280, "baseline_sessions": 285},
                "significance": {"sample_size": 280, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
            {
                "observation_id": "obs_qoe_1",
                "type": "qoe_regression",
                "subject": {"metric": "first_frame_time", "slice": {"platform": "android", "app_version": "8.3.1", "network_type": "4g", "content_type": "short"}},
                "payload": {"delta_pct": 18.0, "current_sessions": 280, "baseline_sessions": 285},
                "significance": {"sample_size": 280, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]

        result = EvidencePipeline(synthesize_claims).build_synthesis(observations)

        self.assertGreaterEqual(len(result["claims"]), 1)
        self.assertGreaterEqual(len(result["recommendations"]), 1)
        self.assertTrue(any(edge["edge_type"] == "supports" for edge in result["edges"]))
        self.assertTrue(any(edge["edge_type"] == "justifies" for edge in result["edges"]))
        self.assertEqual(result["summary"], result["claims"][0]["text"])


class EvidencePipelineServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta = SQLiteMetadataStore(Path(cls.temp_dir.name) / "pipeline.meta.sqlite")
        duck_path = Path(cls.temp_dir.name) / "pipeline.duckdb"
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        meta.initialize()
        analytics.initialize()
        cls.service = SemanticLayerService(meta, analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_synthesize_step_uses_configured_pipeline(self) -> None:
        session_id = self.service.create_session("Pipeline test", {}, {}, {})["session_id"]
        # Seed a published metric so compare_metric works
        from app.semantic import SemanticService
        semantic = SemanticService(self.service.metadata)
        entity = semantic.create_entity("session_pipeline", "Session", ["session_id"])
        semantic.publish_entity(entity["entity_id"])
        metric = semantic.create_metric(
            "watch_time_pipeline", "Watch Time", "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])
        self.service.run_step(
            session_id, "compare_metric",
            {"metric_name": "watch_time_pipeline", "table_name": "analytics.watch_events"},
        )
        captured: dict[str, int] = {}

        class StubPipeline:
            def build_synthesis(self, observations: list[dict]) -> dict:
                captured["count"] = len(observations)
                claim_id = "claim_stub"
                rec_id = "rec_stub"
                observation_id = observations[0]["observation_id"]
                return {
                    "claims": [
                        {
                            "claim_id": claim_id,
                            "type": "root_cause_candidate",
                            "text": "Stub claim text",
                            "scope": {"slice": observations[0]["subject"]["slice"]},
                            "confidence": 0.77,
                            "status": "supported",
                            "supporting_observations": [observation_id],
                            "contradicting_observations": [],
                            "confidence_breakdown": {"effect_strength": 0.8},
                        }
                    ],
                    "recommendations": [
                        {
                            "rec_id": rec_id,
                            "claim_id": claim_id,
                            "action_text": "Stub recommendation",
                            "priority": "P1",
                            "expected_impact": "Validate pipeline delegation.",
                            "risk": "Low",
                            "validation_metric": {"primary_metric": "watch_time"},
                        }
                    ],
                    "edges": [
                        {
                            "from_node_id": observation_id,
                            "from_node_type": "observation",
                            "to_node_id": claim_id,
                            "to_node_type": "claim",
                            "edge_type": "supports",
                            "weight": 0.77,
                            "explanation": "Stub support edge.",
                        },
                        {
                            "from_node_id": claim_id,
                            "from_node_type": "claim",
                            "to_node_id": rec_id,
                            "to_node_type": "recommendation",
                            "edge_type": "justifies",
                            "weight": 0.9,
                            "explanation": "Stub justification edge.",
                        },
                    ],
                    "summary": "Stub summary",
                }

        self.service.evidence_pipeline = StubPipeline()

        result = self.service.run_step(session_id, "synthesize_findings")

        self.assertEqual(result["summary"], "Stub summary")
        self.assertGreater(captured["count"], 0)

        claims = self.service.metadata.query_rows(
            "SELECT claim_id, text FROM claims WHERE session_id = ?",
            [session_id],
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["claim_id"], "claim_stub")
        self.assertEqual(claims[0]["text"], "Stub claim text")

        recommendations = self.service.metadata.query_rows(
            "SELECT rec_id, action_text FROM recommendations WHERE session_id = ?",
            [session_id],
        )
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["rec_id"], "rec_stub")

        edges = self.service.metadata.query_rows(
            "SELECT edge_type FROM evidence_edges WHERE session_id = ? ORDER BY edge_type",
            [session_id],
        )
        self.assertEqual([edge["edge_type"] for edge in edges], ["justifies", "supports"])


class ProvenanceTests(unittest.TestCase):
    """Tests for provenance token generation and persistence."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta = SQLiteMetadataStore(Path(cls.temp_dir.name) / "prov.meta.sqlite")
        duck_path = Path(cls.temp_dir.name) / "prov.duckdb"
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        meta.initialize()
        analytics.initialize()
        cls.service = SemanticLayerService(meta, analytics)
        cls.session = cls.service.create_session("Provenance test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_provenance_token_has_expected_fields(self) -> None:
        prov = self.service._make_provenance("SELECT 1", engine_type="duckdb")
        self.assertIn("query_hash", prov)
        self.assertIn("engine", prov)
        self.assertIn("timestamp", prov)
        self.assertIn("param_count", prov)
        self.assertEqual(prov["engine"], "duckdb")
        self.assertEqual(len(prov["query_hash"]), 16)

    def test_provenance_persisted_in_step(self) -> None:
        session_id = self.session["session_id"]
        self.service.run_step(
            session_id, "profile_table",
            {"table_name": "analytics.watch_events"},
        )

        steps = self.service.metadata.query_rows(
            "SELECT provenance_json FROM steps WHERE session_id = ? AND step_type = 'profile_table'",
            [session_id],
        )
        self.assertGreaterEqual(len(steps), 1)
        import json
        prov = json.loads(steps[0]["provenance_json"])
        self.assertIn("query_hash", prov)
        self.assertIn("engine", prov)

    def test_provenance_in_evidence_graph(self) -> None:
        session_id = self.session["session_id"]
        self.service.run_step(
            session_id, "profile_table",
            {"table_name": "analytics.watch_events"},
        )
        self.service.run_step(
            session_id, "sample_rows",
            {"table_name": "analytics.watch_events", "limit": 5},
        )
        graph = self.service.get_evidence_graph(session_id)
        self.assertIn("steps", graph)
        self.assertGreaterEqual(len(graph["steps"]), 2)
        for step in graph["steps"]:
            self.assertIn("provenance", step)
            self.assertIsInstance(step["provenance"], dict)


class ConfidenceScoringTests(unittest.TestCase):
    """Tests for score_confidence."""

    def test_max_confidence(self) -> None:
        score = score_confidence(1.0, 1.0, 1.0, 1.0, 0.0)
        self.assertLessEqual(score, 0.99)

    def test_zero_inputs(self) -> None:
        score = score_confidence(0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(score, 0.0)

    def test_contradiction_penalty(self) -> None:
        without_penalty = score_confidence(0.8, 0.8, 0.8, 0.8, 0.0)
        with_penalty = score_confidence(0.8, 0.8, 0.8, 0.8, 0.2)
        self.assertGreater(without_penalty, with_penalty)

    def test_clamped_to_range(self) -> None:
        # Large penalty should not go below 0
        score = score_confidence(0.1, 0.1, 0.1, 0.1, 1.0)
        self.assertGreaterEqual(score, 0.0)


class InferenceLevelUnitTests(unittest.TestCase):
    """M-02: inference_level field on synthesized claims."""

    def _metric_obs(self, obs_id: str = "obs_m1", delta_pct: float = -12.0) -> dict:
        return {
            "observation_id": obs_id,
            "type": "metric_change",
            "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
            "payload": {"delta_pct": delta_pct, "current_sessions": 300, "baseline_sessions": 310},
            "significance": {"sample_size": 300, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }

    def _funnel_obs(self, obs_id: str = "obs_f1") -> dict:
        return {
            "observation_id": obs_id,
            "type": "funnel_drop",
            "subject": {"metric": "eng", "slice": {"funnel": "eng", "worst_stage": "click"}},
            "payload": {"worst_stage": "click", "worst_delta_drop_rate": 0.08, "stages": []},
            "significance": {"sample_size": 400, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }

    def test_synthesized_metric_claim_has_inference_level_L0(self) -> None:
        claims, _, _ = synthesize_claims([self._metric_obs()])
        self.assertGreaterEqual(len(claims), 1)
        for claim in claims:
            self.assertEqual(claim["inference_level"], "L0")
            self.assertEqual(claim["inference_justification"], [])

    def test_non_metric_claim_has_inference_level_L0(self) -> None:
        claims, _, _ = synthesize_claims([self._funnel_obs()])
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["inference_level"], "L0")
        self.assertEqual(claims[0]["inference_justification"], [])

    def test_overall_trend_claim_has_inference_level_L0(self) -> None:
        obs = [
            self._metric_obs("obs_m1", -12.0),
            {
                "observation_id": "obs_m2",
                "type": "metric_change",
                "subject": {"metric": "video_views", "slice": {"platform": "android"}},
                "payload": {"delta_pct": -5.0, "current_sessions": 400, "baseline_sessions": 420},
                "significance": {"sample_size": 400, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]
        claims, _, _ = synthesize_claims(obs)
        overall_trends = [c for c in claims if c["type"] == "overall_trend"]
        self.assertGreaterEqual(len(overall_trends), 1)
        for claim in overall_trends:
            self.assertEqual(claim["inference_level"], "L0")
            self.assertEqual(claim["inference_justification"], [])


class InferenceLevelIntegrationTests(unittest.TestCase):
    """M-02: inference_level persisted to DB and returned via get_evidence_graph."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta = SQLiteMetadataStore(Path(cls.temp_dir.name) / "il.meta.sqlite")
        duck_path = Path(cls.temp_dir.name) / "il.duckdb"
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        meta.initialize()
        analytics.initialize()
        cls.service = SemanticLayerService(meta, analytics)

        # Seed metric
        from app.semantic import SemanticService
        semantic = SemanticService(cls.service.metadata)
        entity = semantic.create_entity("il_entity", "IL Entity", ["session_id"])
        semantic.publish_entity(entity["entity_id"])
        metric = semantic.create_metric(
            "il_watch_time", "Watch Time IL", "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])

        cls.session_id = cls.service.create_session("IL test", {}, {}, {})["session_id"]
        cls.service.run_step(
            cls.session_id, "compare_metric",
            {"metric_name": "il_watch_time", "table_name": "analytics.watch_events"},
        )
        cls.service.run_step(cls.session_id, "synthesize_findings")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_inference_level_persisted_and_read_back(self) -> None:
        graph = self.service.get_evidence_graph(self.session_id)
        claims = graph["claims"]
        self.assertGreaterEqual(len(claims), 1)
        for claim in claims:
            self.assertIn("inference_level", claim)
            self.assertIn("inference_justification", claim)
            self.assertEqual(claim["inference_level"], "L0")
            self.assertIsInstance(claim["inference_justification"], list)

    def test_inference_level_column_in_schema(self) -> None:
        rows = self.service.metadata.query_rows("PRAGMA table_info(claims)", [])
        col_names = {row["name"] for row in rows}
        self.assertIn("inference_level", col_names)
        self.assertIn("inference_justification_json", col_names)


if __name__ == "__main__":
    unittest.main()
