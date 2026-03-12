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
        self.service.run_step(session_id, "compare_watch_time")

        steps = self.service.metadata.query_rows(
            "SELECT provenance_json FROM steps WHERE session_id = ? AND step_type = 'compare_watch_time'",
            [session_id],
        )
        self.assertGreaterEqual(len(steps), 1)
        import json
        prov = json.loads(steps[0]["provenance_json"])
        self.assertIn("query_hash", prov)
        self.assertIn("engine", prov)

    def test_provenance_in_evidence_graph(self) -> None:
        session_id = self.session["session_id"]
        self.service.run_watch_time_drop_workflow(session_id)
        graph = self.service.get_evidence_graph(session_id)
        self.assertIn("steps", graph)
        self.assertGreaterEqual(len(graph["steps"]), 5)
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


if __name__ == "__main__":
    unittest.main()
