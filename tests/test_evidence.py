from __future__ import annotations

import unittest

from app.evidence import (
    make_anomaly_observation,
    make_contribution_observation,
    make_funnel_observation,
    make_observation,
    score_confidence,
    synthesize_claims,
)
from app.evidence_engine import EvidencePipeline
from tests.shared_fixtures import get_seeded_duckdb_path


def _metric_query_payload(metric: str) -> dict[str, object]:
    return {
        "table": "analytics.watch_events",
        "metric": metric,
        "time_scope": {
            "mode": "compare",
            "grain": "day",
            "current": {"start": "2026-02-28", "end": "2026-03-06"},
            "baseline": {"start": "2026-02-22", "end": "2026-02-28"},
        },
    }


class ObservationFactoryTests(unittest.TestCase):
    """Tests for observation factory functions."""

    def test_make_observation_metric_observation(self) -> None:
        row = {
            "platform": "android",
            "app_version": "8.3.1",
            "network_type": "4g",
            "content_type": "short",
        }
        obs = make_observation(
            "metric_observation",
            "watch_time",
            row,
            {
                "current_value": 82,
                "baseline_value": 96,
                "delta_pct": -14.2,
                "current_sessions": 280,
                "baseline_sessions": 285,
            },
            {"freshness_ok": True, "sample_size_ok": True},
        )
        self.assertTrue(obs["observation_id"].startswith("obs_"))
        self.assertEqual(obs["type"], "metric_observation")
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
            {
                "segment_value": "android",
                "current_share": 0.60,
                "baseline_share": 0.50,
                "delta_share": 0.10,
                "current_count": 600,
            },
            {
                "segment_value": "ios",
                "current_share": 0.30,
                "baseline_share": 0.35,
                "delta_share": -0.05,
                "current_count": 300,
            },
            {
                "segment_value": "web",
                "current_share": 0.10,
                "baseline_share": 0.15,
                "delta_share": -0.05,
                "current_count": 100,
            },
        ]
        obs = make_contribution_observation(
            "watch_time", "platform", contributions, {"freshness_ok": True}
        )
        self.assertTrue(obs["observation_id"].startswith("obs_"))
        self.assertEqual(obs["type"], "contribution_shift")
        self.assertEqual(obs["payload"]["biggest_shift_segment"], "android")
        self.assertEqual(obs["payload"]["biggest_delta_share"], 0.10)
        self.assertTrue(obs["significance"]["practical_significance"])

    def test_make_anomaly_observation(self) -> None:
        obs = make_anomaly_observation(
            "watch_time",
            {"platform": "android", "app_version": "8.3.1"},
            {
                "value": 60,
                "mean": 90,
                "stddev": 10,
                "z_score": -3.0,
                "is_anomaly": True,
                "sample_size": 500,
            },
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
            {
                "value": 88,
                "mean": 90,
                "stddev": 10,
                "z_score": -0.2,
                "is_anomaly": False,
                "sample_size": 500,
            },
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
                "type": "metric_observation",
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
            },
            {
                "observation_id": "obs_qoe_1",
                "type": "qoe_regression",
                "subject": {
                    "metric": "first_frame_time",
                    "slice": {
                        "platform": "android",
                        "app_version": "8.3.1",
                        "network_type": "4g",
                        "content_type": "short",
                    },
                },
                "payload": {"delta_pct": 18.0, "current_sessions": 280, "baseline_sessions": 285},
                "significance": {"sample_size": 280, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
        ]

    def test_funnel_observation_added_to_supports(self) -> None:
        obs = self._base_observations()
        obs.append(
            {
                "observation_id": "obs_funnel_1",
                "type": "funnel_drop",
                "subject": {
                    "metric": "engagement_funnel",
                    "slice": {"funnel": "engagement_funnel", "worst_stage": "click"},
                },
                "payload": {"worst_stage": "click", "worst_delta_drop_rate": 0.08, "stages": []},
                "significance": {"sample_size": 500, "practical_significance": True},
                "quality": {"freshness_ok": True},
            }
        )
        claims, _, _ = synthesize_claims(obs)
        self.assertGreaterEqual(len(claims), 1)
        self.assertIn("obs_funnel_1", claims[0]["supporting_observations"])

    def test_contribution_observation_added_to_supports(self) -> None:
        obs = self._base_observations()
        obs.append(
            {
                "observation_id": "obs_contrib_1",
                "type": "contribution_shift",
                "subject": {
                    "metric": "watch_time",
                    "slice": {"segment": "platform", "biggest_shift": "android"},
                },
                "payload": {
                    "biggest_shift_segment": "android",
                    "biggest_delta_share": 0.10,
                    "segment_name": "platform",
                    "contributions": [],
                },
                "significance": {"sample_size": 1000, "practical_significance": True},
                "quality": {"freshness_ok": True},
            }
        )
        claims, _, _ = synthesize_claims(obs)
        self.assertIn("obs_contrib_1", claims[0]["supporting_observations"])

    def test_anomaly_observation_added_to_supports(self) -> None:
        obs = self._base_observations()
        obs.append(
            {
                "observation_id": "obs_anomaly_1",
                "type": "anomaly_detection",
                "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
                "payload": {"z_score": -3.0, "is_anomaly": True, "sample_size": 500},
                "significance": {"sample_size": 500, "practical_significance": True},
                "quality": {"freshness_ok": True},
            }
        )
        claims, _, _ = synthesize_claims(obs)
        self.assertIn("obs_anomaly_1", claims[0]["supporting_observations"])

    def test_insignificant_new_types_not_added(self) -> None:
        obs = self._base_observations()
        obs.append(
            {
                "observation_id": "obs_funnel_weak",
                "type": "funnel_drop",
                "subject": {"metric": "f", "slice": {"funnel": "f", "worst_stage": "s"}},
                "payload": {"worst_stage": "s", "worst_delta_drop_rate": 0.01, "stages": []},
                "significance": {"sample_size": 500, "practical_significance": False},
                "quality": {"freshness_ok": True},
            }
        )
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
                "type": "metric_observation",
                "subject": {"metric": "watch_time", "slice": {"platform": "web"}},
                "payload": {"delta_pct": -10.0, "current_sessions": 20, "baseline_sessions": 25},
                "significance": {"sample_size": 20, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": False},
            },
            {
                "observation_id": "obs_large",
                "type": "metric_observation",
                "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
                "payload": {
                    "delta_pct": -10.0,
                    "current_sessions": 5000,
                    "baseline_sessions": 5200,
                },
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
                "type": "metric_observation",
                "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
                "payload": {"delta_pct": -12.0, "current_sessions": 300, "baseline_sessions": 310},
                "significance": {"sample_size": 300, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            },
            {
                "observation_id": "obs_vv",
                "type": "metric_observation",
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

    def test_synthesize_claims_current_window_metric_observation_produces_finding(self) -> None:
        obs = [
            {
                "observation_id": "obs_wt",
                "type": "metric_observation",
                "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
                "payload": {"current_value": 82.0, "current_sessions": 300},
                "significance": {"sample_size": 300, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            }
        ]
        claims, _, _ = synthesize_claims(obs)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")
        self.assertIn("Current window observation", claims[0]["text"])


class SynthesizeClaimsNonMetricTests(unittest.TestCase):
    """Tests for synthesize_claims when only non-metric_observation observations exist."""

    def test_synthesize_claims_funnel_only(self) -> None:
        observations = [
            {
                "observation_id": "obs_funnel_1",
                "type": "funnel_drop",
                "subject": {
                    "metric": "engagement_funnel",
                    "slice": {"funnel": "engagement_funnel", "worst_stage": "click"},
                },
                "payload": {"worst_stage": "click", "worst_delta_drop_rate": 0.08, "stages": []},
                "significance": {"sample_size": 500, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            }
        ]
        claims, _, _ = synthesize_claims(observations)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")
        self.assertIn("Funnel drop", claims[0]["text"])
        self.assertIn("obs_funnel_1", claims[0]["supporting_observations"])

    def test_synthesize_claims_anomaly_only(self) -> None:
        observations = [
            {
                "observation_id": "obs_anomaly_1",
                "type": "anomaly_detection",
                "subject": {"metric": "latency", "slice": {"host": "h1"}},
                "payload": {"z_score": -3.5, "is_anomaly": True, "sample_size": 200},
                "significance": {"sample_size": 200, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            }
        ]
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
        observations = [
            {
                "observation_id": "obs_contrib_1",
                "type": "contribution_shift",
                "subject": {
                    "metric": "watch_time",
                    "slice": {"segment": "platform", "biggest_shift": "android"},
                },
                "payload": {
                    "biggest_shift_segment": "android",
                    "biggest_delta_share": 0.10,
                    "segment_name": "platform",
                    "contributions": [],
                },
                "significance": {"sample_size": 1000, "practical_significance": True},
                "quality": {"freshness_ok": True, "sample_size_ok": True},
            }
        ]
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
                "type": "metric_observation",
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
            },
            {
                "observation_id": "obs_qoe_1",
                "type": "qoe_regression",
                "subject": {
                    "metric": "first_frame_time",
                    "slice": {
                        "platform": "android",
                        "app_version": "8.3.1",
                        "network_type": "4g",
                        "content_type": "short",
                    },
                },
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
        # Seed a published metric so metric_query works
        from app.semantic import SemanticService

        semantic = SemanticService(self.service.metadata)
        entity = semantic.create_entity("session_pipeline", "Session", ["session_id"])
        semantic.publish_entity(entity["entity_id"])
        metric = semantic.create_metric(
            "watch_time_pipeline",
            "Watch Time",
            "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])
        self.service.run_step(
            session_id,
            "metric_query",
            _metric_query_payload("watch_time_pipeline"),
        )
        captured: dict[str, int] = {}

        class StubPipeline:
            def build_synthesis(self, observations: list[dict], **kwargs) -> dict:
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
        # In promotion mode (incremental claims exist), tentative claims are promoted
        # in-place; the stub pipeline's claims are not additionally inserted.
        self.assertGreaterEqual(len(claims), 1)

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
            session_id,
            "profile_table",
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
            session_id,
            "profile_table",
            {"table_name": "analytics.watch_events"},
        )
        self.service.run_step(
            session_id,
            "sample_rows",
            {"table_name": "analytics.watch_events", "limit": 5},
        )
        graph = self.service.get_evidence_graph(session_id)
        self.assertIn("steps", graph)
        self.assertGreaterEqual(len(graph["steps"]), 2)
        for step in graph["steps"]:
            self.assertIn("provenance", step)
            self.assertIsInstance(step["provenance"], dict)

    def test_relation_edge_provenance_in_evidence_graph(self) -> None:
        import json

        session_id = self.session["session_id"]
        self.service.metadata.execute(
            """
            INSERT INTO evidence_edges (
                edge_id, session_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type,
                weight, explanation, match_basis_json, score_components_json, supporting_observation_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "edge_rel_test",
                session_id,
                "claim_a",
                "claim",
                "claim_b",
                "claim",
                "correlates_with",
                0.81,
                "relation test",
                json.dumps({"category": "exact_match", "direction": "up"}),
                json.dumps({"scope_match": 0.92, "direction_match": 1.0}),
                json.dumps(["obs_1", "obs_2"]),
            ],
        )

        graph = self.service.get_evidence_graph(session_id)
        relation_edges = [edge for edge in graph["edges"] if edge["edge_id"] == "edge_rel_test"]
        self.assertEqual(len(relation_edges), 1)
        edge = relation_edges[0]
        self.assertEqual(edge["match_basis"]["category"], "exact_match")
        self.assertEqual(edge["score_components"]["direction_match"], 1.0)
        self.assertEqual(edge["supporting_observation_ids"], ["obs_1", "obs_2"])


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
            "type": "metric_observation",
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
                "type": "metric_observation",
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
            "il_watch_time",
            "Watch Time IL",
            "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])

        cls.session_id = cls.service.create_session("IL test", {}, {}, {})["session_id"]
        cls.service.run_step(
            cls.session_id,
            "metric_query",
            _metric_query_payload("il_watch_time"),
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


class IncrementalSynthesizerTests(unittest.TestCase):
    """M-03: IncrementalSynthesizer unit tests — scope matching, tentative claim creation, contradiction detection."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path

        from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta = SQLiteMetadataStore(Path(cls.temp_dir.name) / "inc.meta.sqlite")
        meta.initialize()
        cls.meta = meta
        cls.IncrementalSynthesizer = IncrementalSynthesizer

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_synth(self):
        return self.IncrementalSynthesizer(self.meta)

    def _insert_obs(
        self,
        session_id: str,
        obs_id: str,
        metric: str,
        slice_dict: dict,
        delta_pct: float,
        sample_size: int = 300,
    ) -> None:
        import json

        self.meta.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                obs_id,
                session_id,
                "step_test",
                "metric_observation",
                json.dumps({"metric": metric, "slice": slice_dict}),
                json.dumps({"delta_pct": delta_pct}),
                json.dumps({"sample_size": sample_size, "practical_significance": True}),
                json.dumps({"freshness_ok": True, "sample_size_ok": True}),
            ],
        )

    def _insert_current_window_obs(
        self,
        session_id: str,
        obs_id: str,
        metric: str,
        slice_dict: dict,
        current_value: float,
        *,
        current_sessions: int = 300,
        sample_size: int = 300,
    ) -> None:
        import json

        self.meta.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                obs_id,
                session_id,
                "step_test",
                "metric_observation",
                json.dumps({"metric": metric, "slice": slice_dict}),
                json.dumps(
                    {
                        "current_value": current_value,
                        "current_sessions": current_sessions,
                    }
                ),
                json.dumps({"sample_size": sample_size, "practical_significance": True}),
                json.dumps({"freshness_ok": True, "sample_size_ok": True}),
            ],
        )

    def _insert_temporal_folded_obs(
        self,
        session_id: str,
        obs_id: str,
        metric: str,
        slice_dict: dict,
        delta_pct: float,
        *,
        temporal_group_by_columns: list[str] | None = None,
        observed_window: dict[str, str] | None = None,
        sample_size: int = 300,
    ) -> None:
        import json

        subject = {"metric": metric, "slice": slice_dict}
        if temporal_group_by_columns:
            subject["temporal_group_by_columns"] = temporal_group_by_columns

        self.meta.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json,
                observed_window_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                obs_id,
                session_id,
                "step_test",
                "metric_observation",
                json.dumps(subject),
                json.dumps({"delta_pct": delta_pct}),
                json.dumps({"sample_size": sample_size, "practical_significance": True}),
                json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                json.dumps(observed_window) if observed_window is not None else None,
            ],
        )

    def _make_session(self) -> str:
        from uuid import uuid4

        session_id = f"sess_{uuid4().hex[:12]}"
        self.meta.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            [session_id, "test", "{}", "{}", "{}", "active"],
        )
        return session_id

    def test_creates_tentative_claim_from_metric_observation(self) -> None:
        session_id = self._make_session()
        self._insert_obs(session_id, "obs_a1", "watch_time", {"platform": "ios"}, -14.2)
        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 1)
        self.assertEqual(result["claims_updated"], 0)
        rows = self.meta.query_rows(
            "SELECT status, claim_type FROM claims WHERE session_id = ?", [session_id]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "tentative")
        self.assertEqual(rows[0]["claim_type"], "root_cause_candidate")

    def test_creates_tentative_finding_from_current_window_metric_observation(self) -> None:
        session_id = self._make_session()
        self._insert_current_window_obs(
            session_id,
            "obs_sw1",
            "watch_time",
            {"platform": "ios"},
            82.0,
        )
        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 1)
        self.assertEqual(result["claims_updated"], 0)
        rows = self.meta.query_rows(
            "SELECT status, claim_type, text FROM claims WHERE session_id = ?",
            [session_id],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "tentative")
        self.assertEqual(rows[0]["claim_type"], "finding")
        self.assertIn("current window observation", rows[0]["text"])

    def test_scope_matching_updates_existing_tentative_claim(self) -> None:
        session_id = self._make_session()
        self._insert_obs(session_id, "obs_b1", "watch_time", {"platform": "android"}, -10.0)
        synth = self._make_synth()
        synth.process(session_id)
        # Second observation with same scope
        self._insert_obs(session_id, "obs_b2", "watch_time", {"platform": "android"}, -12.0)
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 0)
        self.assertEqual(result["claims_updated"], 1)
        import json

        rows = self.meta.query_rows(
            "SELECT supporting_observation_ids_json FROM claims WHERE session_id = ?", [session_id]
        )
        self.assertEqual(len(rows), 1)
        supporting = json.loads(rows[0]["supporting_observation_ids_json"])
        self.assertIn("obs_b1", supporting)
        self.assertIn("obs_b2", supporting)

    def test_contradiction_detected_when_delta_pcts_opposing(self) -> None:
        session_id = self._make_session()
        self._insert_obs(session_id, "obs_c1", "ctr", {"device": "tablet"}, -8.0)
        synth = self._make_synth()
        synth.process(session_id)
        # Opposite direction
        self._insert_obs(session_id, "obs_c2", "ctr", {"device": "tablet"}, +5.0)
        result = synth.process(session_id)
        self.assertEqual(result["contradictions_found"], 1)
        import json

        rows = self.meta.query_rows(
            "SELECT contradicting_observation_ids_json FROM claims WHERE session_id = ?",
            [session_id],
        )
        contradicting = json.loads(rows[0]["contradicting_observation_ids_json"])
        self.assertIn("obs_c2", contradicting)

    def test_different_slice_creates_separate_tentative_claims(self) -> None:
        session_id = self._make_session()
        self._insert_obs(session_id, "obs_d1", "watch_time", {"platform": "ios"}, -10.0)
        self._insert_obs(session_id, "obs_d2", "watch_time", {"platform": "android"}, -8.0)
        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 2)
        rows = self.meta.query_rows(
            "SELECT claim_id FROM claims WHERE session_id = ?", [session_id]
        )
        self.assertEqual(len(rows), 2)

    def test_temporal_group_by_columns_fold_claim_scope(self) -> None:
        session_id = self._make_session()
        self._insert_temporal_folded_obs(
            session_id,
            "obs_fold_1",
            "queued_time",
            {"log_date": "2026-03-16", "resource_group": "rg_a"},
            10.0,
            temporal_group_by_columns=["log_date"],
            observed_window={"start": "2026-03-16", "end": "2026-03-17", "granularity": "day"},
        )
        self._insert_temporal_folded_obs(
            session_id,
            "obs_fold_2",
            "queued_time",
            {"log_date": "2026-03-23", "resource_group": "rg_a"},
            12.0,
            temporal_group_by_columns=["log_date"],
            observed_window={"start": "2026-03-23", "end": "2026-03-24", "granularity": "day"},
        )

        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 1)
        self.assertEqual(result["claims_updated"], 1)

        import json

        row = self.meta.query_one(
            "SELECT scope_json, supporting_observation_ids_json FROM claims WHERE session_id = ?",
            [session_id],
        )
        assert row is not None
        scope = json.loads(row["scope_json"])
        supporting = json.loads(row["supporting_observation_ids_json"])
        self.assertEqual(scope, {"metric": "queued_time", "slice": {"resource_group": "rg_a"}})
        self.assertEqual(set(supporting), {"obs_fold_1", "obs_fold_2"})

    def test_without_temporal_group_by_columns_time_series_rows_remain_separate_claims(
        self,
    ) -> None:
        session_id = self._make_session()
        self._insert_temporal_folded_obs(
            session_id,
            "obs_sep_1",
            "queued_time",
            {"log_date": "2026-03-16", "resource_group": "rg_a"},
            10.0,
        )
        self._insert_temporal_folded_obs(
            session_id,
            "obs_sep_2",
            "queued_time",
            {"log_date": "2026-03-23", "resource_group": "rg_a"},
            12.0,
        )

        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 2)

        rows = self.meta.query_rows(
            "SELECT claim_id FROM claims WHERE session_id = ?",
            [session_id],
        )
        self.assertEqual(len(rows), 2)

    def test_temporal_group_by_columns_missing_column_is_ignored(self) -> None:
        session_id = self._make_session()
        self._insert_temporal_folded_obs(
            session_id,
            "obs_miss_1",
            "queued_time",
            {"log_date": "2026-03-16", "resource_group": "rg_a"},
            10.0,
            temporal_group_by_columns=["missing_col"],
            observed_window={"start": "2026-03-16", "end": "2026-03-17", "granularity": "day"},
        )
        self._insert_temporal_folded_obs(
            session_id,
            "obs_miss_2",
            "queued_time",
            {"log_date": "2026-03-23", "resource_group": "rg_a"},
            12.0,
            temporal_group_by_columns=["missing_col"],
            observed_window={"start": "2026-03-23", "end": "2026-03-24", "granularity": "day"},
        )
        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 2)

        rows = self.meta.query_rows(
            "SELECT scope_json FROM claims WHERE session_id = ? ORDER BY created_at",
            [session_id],
        )
        import json

        self.assertEqual(len(rows), 2)
        scopes = [json.loads(row["scope_json"]) for row in rows]
        self.assertEqual(scopes[0]["slice"]["log_date"], "2026-03-16")
        self.assertEqual(scopes[1]["slice"]["log_date"], "2026-03-23")

    def test_process_idempotent_for_already_processed_observations(self) -> None:
        session_id = self._make_session()
        self._insert_obs(session_id, "obs_e1", "views", {"region": "us"}, -6.0)
        synth = self._make_synth()
        synth.process(session_id)
        result2 = synth.process(session_id)  # Second call: nothing new
        self.assertEqual(result2["claims_created"], 0)
        self.assertEqual(result2["claims_updated"], 0)
        rows = self.meta.query_rows(
            "SELECT claim_id FROM claims WHERE session_id = ?", [session_id]
        )
        self.assertEqual(len(rows), 1)  # No duplicate claims

    def _insert_agg_obs(
        self, session_id: str, obs_id: str, metric: str, slice_dict: dict, payload: dict
    ) -> None:
        import json

        self.meta.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                obs_id,
                session_id,
                "step_test",
                "aggregate_snapshot",
                json.dumps({"metric": metric, "slice": slice_dict}),
                json.dumps(payload),
                json.dumps({"sample_size": 1000, "practical_significance": True}),
                json.dumps({"freshness_ok": True, "sample_size_ok": True}),
            ],
        )

    def _insert_anomaly_obs(
        self,
        session_id: str,
        obs_id: str,
        metric: str,
        slice_dict: dict,
        *,
        z_score: float,
        outlier_factor: float | None = None,
        sample_size: int = 10,
    ) -> None:
        import json

        payload = {
            "value": 1000.0 if z_score >= 0 else 10.0,
            "mean": 100.0,
            "std": 50.0,
            "z_score": z_score,
            "outlier_factor": outlier_factor,
            "method": "z_score",
            "stratum": {},
            "sample_size": sample_size,
        }
        self.meta.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                obs_id,
                session_id,
                "step_test",
                "anomaly_detection",
                json.dumps({"metric": metric, "slice": slice_dict}),
                json.dumps(payload),
                json.dumps({"sample_size": sample_size, "practical_significance": True}),
                json.dumps({"freshness_ok": True, "sample_size_ok": True}),
            ],
        )

    def test_aggregate_observation_generates_payload_based_claim_text(self) -> None:
        session_id = self._make_session()
        self._insert_agg_obs(
            session_id,
            "obs_f1",
            "aggregate",
            {"user": "ai_bi"},
            {"query_count": 1234, "total_scan_gb": 567.89},
        )
        synth = self._make_synth()
        synth.process(session_id)
        rows = self.meta.query_rows("SELECT text FROM claims WHERE session_id = ?", [session_id])
        self.assertEqual(len(rows), 1)
        text = rows[0]["text"]
        self.assertNotIn("Signal detected", text)
        self.assertIn("user=ai_bi", text)
        self.assertIn("query_count", text)

    def test_aggregate_observation_fallback_when_no_numeric_payload(self) -> None:
        session_id = self._make_session()
        self._insert_agg_obs(
            session_id,
            "obs_g1",
            "aggregate",
            {"region": "us"},
            {"label": "foo", "category": "bar"},  # only strings
        )
        synth = self._make_synth()
        synth.process(session_id)
        rows = self.meta.query_rows("SELECT text FROM claims WHERE session_id = ?", [session_id])
        self.assertEqual(len(rows), 1)
        text = rows[0]["text"]
        self.assertIn("aggregate snapshot", text)
        self.assertIn("region=us", text)

    def test_anomaly_observation_creates_tentative_claim(self) -> None:
        session_id = self._make_session()
        self._insert_anomaly_obs(
            session_id,
            "obs_h1",
            "query_count",
            {"log_hour": "02"},
            z_score=3.5,
            outlier_factor=10.0,
        )
        synth = self._make_synth()
        result = synth.process(session_id)
        self.assertEqual(result["claims_created"], 1)
        row = self.meta.query_one(
            "SELECT text, confidence FROM claims WHERE session_id = ?",
            [session_id],
        )
        self.assertIn("anomalous spike", row["text"])
        self.assertIn("10.0x normal", row["text"])
        self.assertGreater(row["confidence"], 0.0)

    def test_anomaly_observations_do_not_create_contradictions_without_delta_pct(self) -> None:
        session_id = self._make_session()
        self._insert_anomaly_obs(
            session_id,
            "obs_i1",
            "query_count",
            {"resource_group": "rg_a"},
            z_score=3.0,
            outlier_factor=8.0,
        )
        synth = self._make_synth()
        synth.process(session_id)
        self._insert_anomaly_obs(
            session_id,
            "obs_i2",
            "query_count",
            {"resource_group": "rg_a"},
            z_score=-2.8,
            outlier_factor=0.1,
        )
        result = synth.process(session_id)
        self.assertEqual(result["contradictions_found"], 0)
        row = self.meta.query_one(
            "SELECT supporting_observation_ids_json, contradicting_observation_ids_json "
            "FROM claims WHERE session_id = ?",
            [session_id],
        )
        import json

        self.assertEqual(json.loads(row["contradicting_observation_ids_json"]), [])
        self.assertEqual(len(json.loads(row["supporting_observation_ids_json"])), 2)

    def test_anomaly_claim_uses_z_score_when_outlier_factor_missing(self) -> None:
        session_id = self._make_session()
        self._insert_anomaly_obs(
            session_id,
            "obs_j1",
            "queue_time",
            {"resource_group": "others"},
            z_score=4.0,
            outlier_factor=None,
        )
        synth = self._make_synth()
        synth.process(session_id)
        row = self.meta.query_one(
            "SELECT text, confidence_breakdown_json FROM claims WHERE session_id = ?",
            [session_id],
        )
        import json

        self.assertIn("z=4.0", row["text"])
        breakdown = json.loads(row["confidence_breakdown_json"])
        self.assertGreater(breakdown["effect_strength"], 0.0)


class DefaultRecommendationPolicyTests(unittest.TestCase):
    """Tests for DefaultRecommendationPolicy template-driven action text."""

    def setUp(self) -> None:
        from app.evidence_engine.recommendation_policy import DefaultRecommendationPolicy

        self.policy = DefaultRecommendationPolicy()

    def _make_obs(self, obs_id: str, payload: dict, slice_dict: dict | None = None) -> dict:
        return {
            "observation_id": obs_id,
            "type": "aggregate_snapshot",
            "subject": {"metric": "aggregate", "slice": slice_dict or {}},
            "payload": payload,
            "significance": {"sample_size": 1000, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }

    def _make_claim(
        self, claim_id: str, obs_id: str, status: str, slice_dict: dict | None = None
    ) -> dict:
        return {
            "claim_id": claim_id,
            "type": "root_cause_candidate",
            "status": status,
            "confidence": 0.7,
            "text": "some claim",
            "scope": {"metric": "aggregate", "slice": slice_dict or {}},
            "supporting_observations": [obs_id],
            "contradicting_observations": [],
            "confidence_breakdown": {"current_value": 0.85},
            "inference_level": "L0",
            "inference_justification": [],
        }

    def test_insufficient_claims_do_not_produce_recommendations(self) -> None:
        obs = self._make_obs("obs_h1", {"query_count": 1234, "avg_cpu": 0.85}, {"user": "ai_bi"})
        claim = self._make_claim("claim_h1", "obs_h1", "insufficient", {"user": "ai_bi"})
        recs = self.policy.derive([obs], [claim], [])
        self.assertEqual(recs, [])

    def test_confirmed_claim_includes_scope_and_payload_hint(self) -> None:
        obs = self._make_obs("obs_i1", {"avg_cpu": 0.85}, {"cluster": "k8sbi-bi1"})
        claim = self._make_claim("claim_i1", "obs_i1", "confirmed", {"cluster": "k8sbi-bi1"})
        claim["text"] = "aggregate changed for cluster"
        recs = self.policy.derive([obs], [claim], [])
        self.assertEqual(len(recs), 1)
        action = recs[0]["action_text"]
        self.assertIn("cluster=k8sbi-bi1", action)
        self.assertIn("aggregate changed for cluster", action)
        self.assertEqual(recs[0]["template_id"], "single_claim_action_v1")

    def test_non_confirmed_claim_without_payload_numeric_produces_no_recommendation(self) -> None:
        obs = self._make_obs("obs_j1", {"label": "foo"}, {"region": "us"})
        claim = self._make_claim("claim_j1", "obs_j1", "insufficient", {"region": "us"})
        recs = self.policy.derive([obs], [claim], [])
        self.assertEqual(recs, [])


class PromotionIntegrationTests(unittest.TestCase):
    """M-03: synthesize_findings promotion path (tentative → confirmed/insufficient)."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path

        from app.semantic import SemanticService
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta = SQLiteMetadataStore(Path(cls.temp_dir.name) / "prom.meta.sqlite")
        duck_path = Path(cls.temp_dir.name) / "prom.duckdb"
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        meta.initialize()
        analytics.initialize()
        cls.service = SemanticLayerService(meta, analytics)

        semantic = SemanticService(meta)
        entity = semantic.create_entity("prom_entity", "Prom Entity", ["session_id"])
        semantic.publish_entity(entity["entity_id"])
        metric = semantic.create_metric(
            "prom_watch_time",
            "Watch Time Prom",
            "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        return self.service.create_session("promotion test", {}, {}, {})["session_id"]

    def test_tentative_promoted_to_confirmed_after_synthesize_findings(self) -> None:
        session_id = self._new_session()
        self.service.run_step(
            session_id,
            "metric_query",
            _metric_query_payload("prom_watch_time"),
        )
        # After primitive step, tentative claims should exist
        tentative = self.service.metadata.query_rows(
            "SELECT claim_id FROM claims WHERE session_id = ? AND status = 'tentative'",
            [session_id],
        )
        self.assertGreater(len(tentative), 0)

        self.service.run_step(session_id, "synthesize_findings")

        # After promotion, no tentative claims remain
        still_tentative = self.service.metadata.query_rows(
            "SELECT claim_id FROM claims WHERE session_id = ? AND status = 'tentative'",
            [session_id],
        )
        self.assertEqual(len(still_tentative), 0)

        # Some claims should be confirmed or insufficient
        promoted = self.service.metadata.query_rows(
            "SELECT status FROM claims WHERE session_id = ?", [session_id]
        )
        self.assertGreater(len(promoted), 0)
        statuses = {row["status"] for row in promoted}
        self.assertTrue(statuses.issubset({"confirmed", "insufficient"}))

    def test_incremental_claims_in_evidence_graph(self) -> None:
        """Full flow: primitive step → tentative claim → synthesize → confirmed visible in evidence graph."""
        session_id = self._new_session()
        self.service.run_step(
            session_id,
            "metric_query",
            _metric_query_payload("prom_watch_time"),
        )
        self.service.run_step(session_id, "synthesize_findings")

        graph = self.service.get_evidence_graph(session_id)
        self.assertIn("claims", graph)
        self.assertGreater(len(graph["claims"]), 0)
        for claim in graph["claims"]:
            self.assertIn("inference_level", claim)
            self.assertEqual(claim["inference_level"], "L0")
            self.assertIn(claim["status"], {"confirmed", "insufficient"})

    def test_recommendations_generated_in_promotion_mode(self) -> None:
        """Confirmed claims should still produce recommendations in promotion mode."""
        session_id = self._new_session()
        self.service.run_step(
            session_id,
            "metric_query",
            _metric_query_payload("prom_watch_time"),
        )
        result = self.service.run_step(session_id, "synthesize_findings")
        self.assertIn("recommendations", result)
        # At least 1 recommendation should be generated for the confirmed claims
        graph = self.service.get_evidence_graph(session_id)
        self.assertGreaterEqual(len(graph.get("recommendations", [])), 1)
        for rec in graph.get("recommendations", []):
            self.assertIn("action", rec)
            self.assertEqual(rec["action"], rec["action_text"])

    def test_single_window_metric_observation_promotes_as_finding_without_recommendations(
        self,
    ) -> None:
        session_id = self._new_session()
        self.service.run_step(
            session_id,
            "metric_query",
            {
                **_metric_query_payload("prom_watch_time"),
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-02-28", "end": "2026-03-06"},
                },
            },
        )

        tentative = self.service.metadata.query_rows(
            "SELECT claim_type, status FROM claims WHERE session_id = ? AND status = 'tentative'",
            [session_id],
        )
        self.assertGreater(len(tentative), 0)
        self.assertTrue(all(row["claim_type"] == "finding" for row in tentative))

        self.service.run_step(session_id, "synthesize_findings")

        promoted = self.service.metadata.query_rows(
            "SELECT claim_type, status FROM claims WHERE session_id = ?",
            [session_id],
        )
        self.assertGreater(len(promoted), 0)
        self.assertTrue(all(row["claim_type"] == "finding" for row in promoted))
        self.assertTrue(all(row["status"] in {"confirmed", "insufficient"} for row in promoted))

        recommendations = self.service.metadata.query_rows(
            "SELECT rec_id FROM recommendations WHERE session_id = ?",
            [session_id],
        )
        self.assertEqual(recommendations, [])


class CausalEvidenceEdgeTypesTests(unittest.TestCase):
    """Causal evidence edge types — backward compat + causal type behavior."""

    def _make_obs(self, obs_id: str = "obs_1") -> dict:
        return {
            "observation_id": obs_id,
            "type": "metric_observation",
            "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
            "payload": {"delta_pct": -14.0, "current_sessions": 300, "baseline_sessions": 310},
            "significance": {"sample_size": 300, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }

    def _make_claim(self, claim_id: str = "claim_test", obs_id: str = "obs_1") -> dict:
        return {
            "claim_id": claim_id,
            "type": "root_cause_candidate",
            "text": "Test claim",
            "scope": {"slice": {}},
            "confidence": 0.70,
            "status": "supported",
            "supporting_observations": [obs_id],
            "contradicting_observations": [],
            "confidence_breakdown": {
                "effect_strength": 0.7,
                "consistency": 0.8,
                "sample_score": 0.6,
                "data_quality_score": 0.9,
                "contradiction_penalty": 0.0,
            },
            "inference_level": "L0",
            "inference_justification": [],
        }

    def _pipeline_with_causal_edge(self, edge_type: str, obs_id: str = "obs_1") -> EvidencePipeline:
        """Build a pipeline whose synthesizer injects one causal edge."""
        claim = self._make_claim(obs_id=obs_id)
        causal_edge = {
            "from_node_id": obs_id,
            "from_node_type": "observation",
            "to_node_id": claim["claim_id"],
            "to_node_type": "claim",
            "edge_type": edge_type,
            "weight": 0.85,
            "explanation": f"Causal edge of type {edge_type}.",
        }

        def _synthesize(observations):
            return [claim], [], [causal_edge]

        return EvidencePipeline(_synthesize)

    # ── M-07.3a: Basic edge types unchanged ─────────────────────────────────

    def test_basic_supports_edge_created(self) -> None:
        result = EvidencePipeline(synthesize_claims).build_synthesis([self._make_obs()])
        support_edges = [e for e in result["edges"] if e["edge_type"] == "supports"]
        self.assertGreater(len(support_edges), 0)

    def test_basic_contradicts_edge_weight_fixed(self) -> None:
        claim = self._make_claim()
        claim["supporting_observations"] = []
        claim["contradicting_observations"] = ["obs_1"]

        def _synth(observations):
            return [claim], [], []

        result = EvidencePipeline(_synth).build_synthesis([self._make_obs()])
        contradicts_edges = [e for e in result["edges"] if e["edge_type"] == "contradicts"]
        self.assertEqual(len(contradicts_edges), 1)
        self.assertAlmostEqual(contradicts_edges[0]["weight"], 0.35)

    def test_basic_justifies_edge_created(self) -> None:
        result = EvidencePipeline(synthesize_claims).build_synthesis([self._make_obs()])
        justifies_edges = [e for e in result["edges"] if e["edge_type"] == "justifies"]
        self.assertGreater(len(justifies_edges), 0)

    def test_basic_edges_do_not_change_inference_level(self) -> None:
        result = EvidencePipeline(synthesize_claims).build_synthesis([self._make_obs()])
        for claim in result["claims"]:
            self.assertEqual(
                claim["inference_level"],
                "L0",
                f"Claim {claim['claim_id']} should remain L0 with only basic edges",
            )
            self.assertEqual(claim["inference_justification"], [])

    # ── M-07.3b: New causal edge types accepted ──────────────────────────────

    def test_correlates_with_edge_accepted(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_CORRELATES_WITH

        result = self._pipeline_with_causal_edge(EDGE_TYPE_CORRELATES_WITH).build_synthesis(
            [self._make_obs()]
        )
        self.assertTrue(any(e["edge_type"] == EDGE_TYPE_CORRELATES_WITH for e in result["edges"]))

    def test_temporally_precedes_edge_accepted(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_TEMPORALLY_PRECEDES

        result = self._pipeline_with_causal_edge(EDGE_TYPE_TEMPORALLY_PRECEDES).build_synthesis(
            [self._make_obs()]
        )
        self.assertTrue(
            any(e["edge_type"] == EDGE_TYPE_TEMPORALLY_PRECEDES for e in result["edges"])
        )

    def test_mechanistically_explains_edge_accepted(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_MECHANISTICALLY_EXPLAINS

        result = self._pipeline_with_causal_edge(
            EDGE_TYPE_MECHANISTICALLY_EXPLAINS
        ).build_synthesis([self._make_obs()])
        self.assertTrue(
            any(e["edge_type"] == EDGE_TYPE_MECHANISTICALLY_EXPLAINS for e in result["edges"])
        )

    def test_eliminates_alternative_edge_accepted(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_ELIMINATES_ALTERNATIVE

        result = self._pipeline_with_causal_edge(EDGE_TYPE_ELIMINATES_ALTERNATIVE).build_synthesis(
            [self._make_obs()]
        )
        self.assertTrue(
            any(e["edge_type"] == EDGE_TYPE_ELIMINATES_ALTERNATIVE for e in result["edges"])
        )

    def test_experimentally_confirms_edge_accepted(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_EXPERIMENTALLY_CONFIRMS

        result = self._pipeline_with_causal_edge(EDGE_TYPE_EXPERIMENTALLY_CONFIRMS).build_synthesis(
            [self._make_obs()]
        )
        self.assertTrue(
            any(e["edge_type"] == EDGE_TYPE_EXPERIMENTALLY_CONFIRMS for e in result["edges"])
        )

    # ── M-07.3c: inference_level auto-update ─────────────────────────────────

    def test_correlates_with_upgrades_claim_to_L1(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_CORRELATES_WITH

        result = self._pipeline_with_causal_edge(EDGE_TYPE_CORRELATES_WITH).build_synthesis(
            [self._make_obs()]
        )
        claim = result["claims"][0]
        self.assertEqual(claim["inference_level"], "L1")
        self.assertIn(f"{EDGE_TYPE_CORRELATES_WITH}→L1", claim["inference_justification"])

    def test_temporally_precedes_upgrades_claim_to_L2(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_TEMPORALLY_PRECEDES

        result = self._pipeline_with_causal_edge(EDGE_TYPE_TEMPORALLY_PRECEDES).build_synthesis(
            [self._make_obs()]
        )
        self.assertEqual(result["claims"][0]["inference_level"], "L2")

    def test_mechanistically_explains_upgrades_claim_to_L3(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_MECHANISTICALLY_EXPLAINS

        result = self._pipeline_with_causal_edge(
            EDGE_TYPE_MECHANISTICALLY_EXPLAINS
        ).build_synthesis([self._make_obs()])
        self.assertEqual(result["claims"][0]["inference_level"], "L3")

    def test_eliminates_alternative_upgrades_claim_to_L4(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_ELIMINATES_ALTERNATIVE

        result = self._pipeline_with_causal_edge(EDGE_TYPE_ELIMINATES_ALTERNATIVE).build_synthesis(
            [self._make_obs()]
        )
        self.assertEqual(result["claims"][0]["inference_level"], "L4")

    def test_experimentally_confirms_upgrades_claim_to_L5(self) -> None:
        from app.evidence_engine.schemas import EDGE_TYPE_EXPERIMENTALLY_CONFIRMS

        result = self._pipeline_with_causal_edge(EDGE_TYPE_EXPERIMENTALLY_CONFIRMS).build_synthesis(
            [self._make_obs()]
        )
        self.assertEqual(result["claims"][0]["inference_level"], "L5")

    def test_highest_level_wins_with_multiple_causal_edges(self) -> None:
        from app.evidence_engine.schemas import (
            EDGE_TYPE_CORRELATES_WITH,
            EDGE_TYPE_TEMPORALLY_PRECEDES,
        )

        obs_id = "obs_multi"
        claim = self._make_claim(claim_id="claim_multi", obs_id=obs_id)

        def _synth(observations):
            edges = [
                {
                    "from_node_id": obs_id,
                    "from_node_type": "observation",
                    "to_node_id": "claim_multi",
                    "to_node_type": "claim",
                    "edge_type": EDGE_TYPE_CORRELATES_WITH,
                    "weight": 0.7,
                    "explanation": "correlation",
                },
                {
                    "from_node_id": obs_id,
                    "from_node_type": "observation",
                    "to_node_id": "claim_multi",
                    "to_node_type": "claim",
                    "edge_type": EDGE_TYPE_TEMPORALLY_PRECEDES,
                    "weight": 0.8,
                    "explanation": "temporal",
                },
            ]
            return [claim], [], edges

        result = EvidencePipeline(_synth).build_synthesis([self._make_obs(obs_id)])
        updated = result["claims"][0]
        self.assertEqual(updated["inference_level"], "L2")
        self.assertIn(f"{EDGE_TYPE_TEMPORALLY_PRECEDES}→L2", updated["inference_justification"])
        self.assertIn(f"{EDGE_TYPE_CORRELATES_WITH}→L1", updated["inference_justification"])

    def test_multiple_causal_edge_types_boost_confidence(self) -> None:
        from app.evidence_engine.schemas import (
            EDGE_TYPE_CORRELATES_WITH,
            EDGE_TYPE_TEMPORALLY_PRECEDES,
        )

        obs_id = "obs_boost"

        # Single causal edge → baseline confidence
        single_result = self._pipeline_with_causal_edge(
            EDGE_TYPE_CORRELATES_WITH, obs_id
        ).build_synthesis([self._make_obs(obs_id)])
        single_confidence = single_result["claims"][0]["confidence"]

        claim = self._make_claim(claim_id="claim_boost", obs_id=obs_id)

        def _synth_two(observations):
            edges = [
                {
                    "from_node_id": obs_id,
                    "from_node_type": "observation",
                    "to_node_id": "claim_boost",
                    "to_node_type": "claim",
                    "edge_type": EDGE_TYPE_CORRELATES_WITH,
                    "weight": 0.7,
                    "explanation": "corr",
                },
                {
                    "from_node_id": obs_id,
                    "from_node_type": "observation",
                    "to_node_id": "claim_boost",
                    "to_node_type": "claim",
                    "edge_type": EDGE_TYPE_TEMPORALLY_PRECEDES,
                    "weight": 0.8,
                    "explanation": "temp",
                },
            ]
            return [claim], [], edges

        two_result = EvidencePipeline(_synth_two).build_synthesis([self._make_obs(obs_id)])
        self.assertGreater(two_result["claims"][0]["confidence"], single_confidence)

    # ── M-07.3d: Schema constants ─────────────────────────────────────────────

    def test_all_edge_types_is_union_of_basic_and_causal(self) -> None:
        from app.evidence_engine.schemas import ALL_EDGE_TYPES, BASIC_EDGE_TYPES, CAUSAL_EDGE_TYPES

        self.assertEqual(ALL_EDGE_TYPES, BASIC_EDGE_TYPES | CAUSAL_EDGE_TYPES)

    def test_causal_edge_to_inference_level_mapping_complete(self) -> None:
        from app.evidence_engine.schemas import (
            CAUSAL_EDGE_TO_INFERENCE_LEVEL,
            CAUSAL_EDGE_TYPES,
            INFERENCE_LEVEL_ORDER,
        )

        for et in CAUSAL_EDGE_TYPES:
            self.assertIn(
                et, CAUSAL_EDGE_TO_INFERENCE_LEVEL, f"Missing mapping for causal edge type: {et}"
            )
            level = CAUSAL_EDGE_TO_INFERENCE_LEVEL[et]
            self.assertIn(
                level,
                INFERENCE_LEVEL_ORDER,
                f"Level {level} for edge type {et} not in INFERENCE_LEVEL_ORDER",
            )


class CausalBasisTests(unittest.TestCase):
    """M-10: causal_basis metadata attached to Recommendations."""

    # ── helpers ──────────────────────────────────────────────────────────────

    def _make_claim(
        self, level: str = "L0", text: str = "metric dropped", confidence: float = 0.7
    ) -> dict:
        return {
            "claim_id": "claim_cb",
            "type": "root_cause_candidate",
            "text": text,
            "scope": {"metric": "watch_time", "slice": {}},
            "confidence": confidence,
            "status": "supported",
            "supporting_observations": ["obs_cb"],
            "contradicting_observations": [],
            "confidence_breakdown": {
                "effect_strength": 0.7,
                "consistency": 0.8,
                "sample_score": 0.6,
                "data_quality_score": 0.9,
                "contradiction_penalty": 0.0,
            },
            "inference_level": level,
            "inference_justification": [],
        }

    def _make_obs(self, obs_id: str = "obs_cb") -> dict:
        return {
            "observation_id": obs_id,
            "type": "metric_observation",
            "subject": {"metric": "watch_time", "slice": {"platform": "android"}},
            "payload": {"delta_pct": -14.0, "current_sessions": 300, "baseline_sessions": 310},
            "significance": {"sample_size": 300, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }

    # ── pipeline-level tests ─────────────────────────────────────────────────

    def test_pipeline_attaches_causal_basis_to_recommendation(self) -> None:
        claim = self._make_claim()

        def _synth(observations):
            return [claim], [], []

        result = EvidencePipeline(_synth).build_synthesis([self._make_obs()])
        recs = result["recommendations"]
        self.assertGreaterEqual(len(recs), 1)
        for rec in recs:
            self.assertIn("causal_basis", rec)
            cb = rec["causal_basis"]
            self.assertIsNotNone(cb)
            self.assertIn("inference_level", cb)
            self.assertIn("unresolved_confounders", cb)
            self.assertIn("suggested_validation", cb)
            self.assertIn("strongest_evidence_summary", cb)

    def test_pipeline_causal_basis_uses_upgraded_level(self) -> None:
        """causal_basis.inference_level must reflect post-M-07 upgrade, not pre-upgrade L0."""
        from app.evidence_engine.schemas import EDGE_TYPE_TEMPORALLY_PRECEDES

        obs_id = "obs_cb_upgrade"
        claim = self._make_claim()
        causal_edge = {
            "from_node_id": obs_id,
            "from_node_type": "observation",
            "to_node_id": claim["claim_id"],
            "to_node_type": "claim",
            "edge_type": EDGE_TYPE_TEMPORALLY_PRECEDES,
            "weight": 0.85,
            "explanation": "temporal precedence",
        }

        def _synth(observations):
            return [claim], [], [causal_edge]

        result = EvidencePipeline(_synth).build_synthesis([self._make_obs(obs_id)])
        self.assertEqual(result["claims"][0]["inference_level"], "L2")
        recs = result["recommendations"]
        self.assertGreaterEqual(len(recs), 1)
        self.assertEqual(recs[0]["causal_basis"]["inference_level"], "L2")

    # ── integration tests (DB persistence) ───────────────────────────────────

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from pathlib import Path

        from app.semantic import SemanticService
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta = SQLiteMetadataStore(Path(cls.temp_dir.name) / "cb.meta.sqlite")
        duck_path = Path(cls.temp_dir.name) / "cb.duckdb"
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        meta.initialize()
        analytics.initialize()
        cls.service = SemanticLayerService(meta, analytics)

        semantic = SemanticService(cls.service.metadata)
        entity = semantic.create_entity("cb_entity", "CB Entity", ["session_id"])
        semantic.publish_entity(entity["entity_id"])
        metric = semantic.create_metric(
            "cb_watch_time",
            "Watch Time CB",
            "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])

        cls.session_id = cls.service.create_session("CB test", {}, {}, {})["session_id"]
        cls.service.run_step(
            cls.session_id,
            "metric_query",
            _metric_query_payload("cb_watch_time"),
        )
        cls.service.run_step(cls.session_id, "synthesize_findings")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_causal_basis_persisted_and_read_back(self) -> None:
        graph = self.service.get_evidence_graph(self.session_id)
        recs = graph.get("recommendations", [])
        self.assertGreaterEqual(len(recs), 1)
        # All recs must have causal_basis key; those from synthesize_findings have non-null value.
        # (Other recs may have causal_basis=None if they were inserted without the column — see backward compat test.)
        for rec in recs:
            self.assertIn("causal_basis", rec)
        # At least one recommendation from synthesize_findings should have a fully-populated causal_basis.
        synthesized_recs = [r for r in recs if r.get("causal_basis") is not None]
        self.assertGreaterEqual(len(synthesized_recs), 1)
        for rec in synthesized_recs:
            cb = rec["causal_basis"]
            self.assertIn("inference_level", cb)
            self.assertIn("unresolved_confounders", cb)
            self.assertIn("suggested_validation", cb)
            self.assertIn("strongest_evidence_summary", cb)

    def test_backward_compat_null_causal_basis(self) -> None:
        """Rows inserted without causal_basis_json must return causal_basis=None without error."""
        import json
        from uuid import uuid4

        rec_id = f"rec_{uuid4().hex[:12]}"
        self.service.metadata.execute(
            """
            INSERT INTO recommendations
                (rec_id, session_id, claim_id, action_text, priority, expected_impact, risk, validation_metric_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rec_id,
                self.session_id,
                "claim_old",
                "do something",
                "P1",
                "some impact",
                "low",
                json.dumps({"primary_metric": "x"}),
            ],
        )
        graph = self.service.get_evidence_graph(self.session_id)
        old_recs = [r for r in graph["recommendations"] if r["rec_id"] == rec_id]
        self.assertEqual(len(old_recs), 1)
        self.assertIsNone(old_recs[0]["causal_basis"])

    def test_causal_basis_json_column_in_schema(self) -> None:
        rows = self.service.metadata.query_rows("PRAGMA table_info(recommendations)", [])
        col_names = {row["name"] for row in rows}
        self.assertIn("causal_basis_json", col_names)
        self.assertIn("template_id", col_names)

    def test_evidence_edges_relation_provenance_columns_in_schema(self) -> None:
        rows = self.service.metadata.query_rows("PRAGMA table_info(evidence_edges)", [])
        col_names = {row["name"] for row in rows}
        self.assertIn("match_basis_json", col_names)
        self.assertIn("score_components_json", col_names)
        self.assertIn("supporting_observation_ids_json", col_names)


if __name__ == "__main__":
    unittest.main()
