"""M-06 tests: ScopeClusterer, SignalAligner, ClaimFormulator, ThreeStagePipeline."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from uuid import uuid4

from app.evidence_engine.scoring import score_confidence
from app.evidence_engine.synthesizers.claim_formulator import ClaimFormulator
from app.evidence_engine.synthesizers.scope_clusterer import ScopeClusterer
from app.evidence_engine.synthesizers.signal_aligner import SignalAligner
from app.evidence_engine.synthesizers.three_stage_pipeline import ThreeStagePipeline

# ── helpers ───────────────────────────────────────────────────────────────────


def _metric_obs(
    metric: str = "watch_time",
    platform: str = "android",
    delta_pct: float = -14.2,
    sample_size: int = 120,
    practical: bool = True,
    sample_size_ok: bool = True,
) -> dict:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "metric_observation",
        "subject": {"metric": metric, "slice": {"platform": platform}},
        "payload": {"delta_pct": delta_pct, "absolute_change": -1000},
        "significance": {"sample_size": sample_size, "practical_significance": practical},
        "quality": {"sample_size_ok": sample_size_ok, "freshness_ok": True},
    }


def _current_window_metric_obs(
    metric: str = "watch_time",
    platform: str = "android",
    current_value: float = 82.0,
    sample_size: int = 120,
) -> dict:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "metric_observation",
        "subject": {"metric": metric, "slice": {"platform": platform}},
        "payload": {"current_value": current_value, "current_sessions": sample_size},
        "significance": {"sample_size": sample_size, "practical_significance": True},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


def _funnel_obs(
    worst_stage: str = "click",
    sample_size: int = 80,
    practical: bool = True,
    metric: str = "",
    platform: str = "",
) -> dict:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "funnel_drop",
        "subject": {"metric": metric, "slice": {"platform": platform}},
        "payload": {"worst_stage": worst_stage, "drop_rate": 0.35},
        "significance": {"sample_size": sample_size, "practical_significance": practical},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


def _anomaly_obs(
    z_score: float = 3.5,
    sample_size: int = 100,
    practical: bool = True,
    metric: str = "watch_time",
    platform: str = "android",
) -> dict:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "anomaly_detection",
        "subject": {"metric": metric, "slice": {"platform": platform}},
        "payload": {"z_score": z_score},
        "significance": {"sample_size": sample_size, "practical_significance": practical},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


def _contribution_obs(
    segment: str = "new_users",
    sample_size: int = 90,
    practical: bool = True,
    metric: str = "",
    platform: str = "",
) -> dict:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "contribution_shift",
        "subject": {"metric": metric, "slice": {"platform": platform}},
        "payload": {"biggest_shift_segment": segment, "shift_magnitude": 0.12},
        "significance": {"sample_size": sample_size, "practical_significance": practical},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


# ── ScopeClusterer ────────────────────────────────────────────────────────────


class TestScopeClusterer(unittest.TestCase):
    def setUp(self):
        self.clusterer = ScopeClusterer()

    def test_single_metric_observation_obs_produces_one_cluster(self):
        obs = _metric_obs()
        clusters = self.clusterer.cluster([obs])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].cluster_reason, "exact_scope_match")
        self.assertEqual(clusters[0].total_observation_count, 1)

    def test_different_slices_produce_separate_clusters(self):
        obs1 = _metric_obs(platform="android")
        obs2 = _metric_obs(platform="ios")
        clusters = self.clusterer.cluster([obs1, obs2])
        self.assertEqual(len(clusters), 2)
        keys = {c.scope_key for c in clusters}
        self.assertEqual(len(keys), 2)  # distinct scope keys

    def test_funnel_obs_matching_scope_attached_to_cluster(self):
        m_obs = _metric_obs(metric="watch_time", platform="android")
        f_obs = _funnel_obs(metric="watch_time", platform="android")
        clusters = self.clusterer.cluster([m_obs, f_obs])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].funnel_drop_obs), 1)
        self.assertEqual(clusters[0].total_observation_count, 2)

    def test_non_metric_only_with_stable_scope_produces_cluster(self):
        obs = _anomaly_obs()
        clusters = self.clusterer.cluster([obs])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].cluster_reason, "exact_scope_match")
        self.assertEqual(len(clusters[0].anomaly_detection_obs), 1)

    def test_empty_observations_returns_empty_list(self):
        clusters = self.clusterer.cluster([])
        self.assertEqual(clusters, [])

    def test_scope_key_is_deterministic(self):
        obs1 = {
            "observation_id": "obs_a",
            "type": "metric_observation",
            "subject": {"metric": "revenue", "slice": {"b": "2", "a": "1"}},
            "payload": {"delta_pct": -5.0},
            "significance": {"sample_size": 50, "practical_significance": True},
            "quality": {"sample_size_ok": True, "freshness_ok": True},
        }
        obs2 = {
            "observation_id": "obs_b",
            "type": "metric_observation",
            "subject": {"metric": "revenue", "slice": {"a": "1", "b": "2"}},
            "payload": {"delta_pct": -3.0},
            "significance": {"sample_size": 50, "practical_significance": True},
            "quality": {"sample_size_ok": True, "freshness_ok": True},
        }
        clusters = self.clusterer.cluster([obs1, obs2])
        # Both obs have same scope — should produce 1 cluster
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].scope_key, "revenue/a=1,b=2")

    def test_observation_without_stable_scope_is_dropped(self):
        obs = {
            "observation_id": "obs_bad_scope",
            "type": "metric_observation",
            "subject": {"metric": "", "slice": None},
            "payload": {"delta_pct": -3.0},
            "significance": {"sample_size": 50, "practical_significance": True},
            "quality": {"sample_size_ok": True, "freshness_ok": True},
        }
        clusters = self.clusterer.cluster([obs])
        self.assertEqual(clusters, [])

    def test_metric_observation_without_delta_pct_is_clustered_as_other_observation(self):
        obs = _current_window_metric_obs()
        clusters = self.clusterer.cluster([obs])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].metric_observation_obs, [])
        self.assertEqual(len(clusters[0].other_obs), 1)


# ── SignalAligner ─────────────────────────────────────────────────────────────


class TestSignalAligner(unittest.TestCase):
    def setUp(self):
        self.clusterer = ScopeClusterer()
        self.aligner = SignalAligner()

    def _single_metric_cluster(self, **kwargs):
        obs = _metric_obs(**kwargs)
        return self.clusterer.cluster([obs])[0]

    def test_primary_selected_by_delta_pct_times_log_sample_size(self):
        # obs_a: smaller delta but much larger sample → should win by scoring
        obs_a = _metric_obs(delta_pct=-5.0, sample_size=500, platform="android")
        obs_b = _metric_obs(delta_pct=-10.0, sample_size=10, platform="android")
        clusters = self.clusterer.cluster([obs_a, obs_b])
        signal = self.aligner.align(clusters[0])
        # score(obs_a) = 5.0 * log1p(500) ≈ 31.2; score(obs_b) = 10.0 * log1p(10) ≈ 23.9
        import math

        score_a = abs(-5.0) * math.log1p(500)
        score_b = abs(-10.0) * math.log1p(10)
        expected_primary_id = (
            obs_a["observation_id"] if score_a >= score_b else obs_b["observation_id"]
        )
        self.assertEqual(signal.primary_obs["observation_id"], expected_primary_id)

    def test_funnel_obs_with_practical_significance_added_to_supporting(self):
        m_obs = _metric_obs(metric="watch_time", platform="android")
        f_obs = _funnel_obs(metric="watch_time", platform="android", practical=True)
        clusters = self.clusterer.cluster([m_obs, f_obs])
        signal = self.aligner.align(clusters[0])
        self.assertIn(f_obs["observation_id"], signal.supporting_obs_ids)

    def test_contradiction_detected_with_opposite_delta(self):
        obs_a = _metric_obs(delta_pct=-14.0, platform="android")
        obs_b = _metric_obs(delta_pct=+8.0, platform="android")  # opposite direction
        clusters = self.clusterer.cluster([obs_a, obs_b])
        signal = self.aligner.align(clusters[0])
        self.assertGreater(signal.contradiction_penalty, 0.0)
        self.assertEqual(len(signal.contradicting_obs_ids), 1)

    def test_effect_strength_capped_at_one(self):
        cluster = self._single_metric_cluster(delta_pct=-100.0)
        signal = self.aligner.align(cluster)
        self.assertEqual(signal.effect_strength, 1.0)

    def test_non_metric_cluster_primary_by_sample_size(self):
        small_obs = _anomaly_obs(sample_size=20)
        large_obs = _anomaly_obs(sample_size=200)
        clusters = self.clusterer.cluster([small_obs, large_obs])
        signal = self.aligner.align(clusters[0])
        self.assertEqual(signal.primary_obs["observation_id"], large_obs["observation_id"])
        self.assertEqual(signal.primary_selection_reason, "max sample_size (non-metric cluster)")

    def test_current_window_metric_observation_uses_non_metric_alignment(self):
        obs = _current_window_metric_obs(current_value=90.0)
        clusters = self.clusterer.cluster([obs])
        signal = self.aligner.align(clusters[0])
        self.assertEqual(signal.primary_obs["observation_id"], obs["observation_id"])
        self.assertEqual(signal.primary_selection_reason, "max sample_size (non-metric cluster)")

    def test_audit_fields_populated_when_supporting_obs_present(self):
        m_obs = _metric_obs(metric="watch_time", platform="android")
        f_obs = _funnel_obs(metric="watch_time", platform="android", practical=True)
        clusters = self.clusterer.cluster([m_obs, f_obs])
        signal = self.aligner.align(clusters[0])
        self.assertTrue(len(signal.consistency_factors) >= 1)
        self.assertTrue(len(signal.support_reasons) >= 1)
        self.assertTrue(len(signal.alignment_notes) >= 1)


# ── ClaimFormulator ───────────────────────────────────────────────────────────


class TestClaimFormulator(unittest.TestCase):
    def setUp(self):
        self.clusterer = ScopeClusterer()
        self.aligner = SignalAligner()
        self.formulator = ClaimFormulator()

    def _signal_for(self, *obs_list):
        clusters = self.clusterer.cluster(list(obs_list))
        return self.aligner.align(clusters[0])

    def test_metric_observation_produces_root_cause_candidate(self):
        signal = self._signal_for(_metric_obs())
        formulation = self.formulator.formulate(signal)
        self.assertEqual(formulation.claim["type"], "root_cause_candidate")
        self.assertEqual(formulation.claim_type_decision, "root_cause_candidate")

    def test_non_metric_produces_finding(self):
        signal = self._signal_for(_anomaly_obs())
        formulation = self.formulator.formulate(signal)
        self.assertEqual(formulation.claim["type"], "finding")
        self.assertEqual(formulation.claim_type_decision, "finding")

    def test_current_window_metric_observation_produces_finding_without_direction_metadata(self):
        signal = self._signal_for(_current_window_metric_obs(current_value=91.5))
        formulation = self.formulator.formulate(signal)
        self.assertEqual(formulation.claim["type"], "finding")
        self.assertEqual(formulation.text_template, "metric_current_window_observation")
        self.assertNotIn("primary_delta_pct", formulation.claim["confidence_breakdown"])

    def test_final_confidence_equals_score_confidence_call(self):
        signal = self._signal_for(_metric_obs())
        formulation = self.formulator.formulate(signal)
        expected = score_confidence(**formulation.confidence_inputs)
        self.assertEqual(formulation.final_confidence, expected)

    def test_formulate_overall_trend_returns_none_for_single_metric(self):
        obs = _metric_obs(metric="watch_time")
        clusters = self.clusterer.cluster([obs])
        signals = self.aligner.align_all(clusters)
        result = self.formulator.formulate_overall_trend(signals, [obs])
        self.assertIsNone(result)

    def test_formulate_overall_trend_returns_claim_for_two_metrics(self):
        obs1 = _metric_obs(metric="watch_time", platform="android")
        obs2 = _metric_obs(metric="revenue", platform="android", delta_pct=-8.0)
        clusters = self.clusterer.cluster([obs1, obs2])
        signals = self.aligner.align_all(clusters)
        result = self.formulator.formulate_overall_trend(signals, [obs1, obs2])
        self.assertIsNotNone(result)
        self.assertEqual(result.claim["type"], "overall_trend")
        self.assertEqual(result.text_template, "multi_metric_trend")

    def test_formulate_overall_trend_ignores_current_window_only_observations(self):
        obs1 = _metric_obs(metric="watch_time", platform="android")
        obs2 = _current_window_metric_obs(metric="revenue", platform="android", current_value=50.0)
        clusters = self.clusterer.cluster([obs1, obs2])
        signals = self.aligner.align_all(clusters)
        result = self.formulator.formulate_overall_trend(signals, [obs1, obs2])
        self.assertIsNone(result)

    def test_audit_fields_populated(self):
        signal = self._signal_for(_metric_obs())
        formulation = self.formulator.formulate(signal)
        self.assertTrue(len(formulation.claim_type_reason) > 0)
        self.assertTrue(len(formulation.text_template) > 0)


# ── ThreeStagePipeline integration ────────────────────────────────────────────


class TestThreeStagePipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = ThreeStagePipeline()

    def test_run_returns_four_tuple(self):
        result = self.pipeline.run([_metric_obs()])
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 4)

    def test_single_metric_obs_produces_one_claim(self):
        obs = _metric_obs()
        claims, recs, edges, audit = self.pipeline.run([obs])
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "root_cause_candidate")
        self.assertEqual(audit.claims_produced, 1)
        self.assertFalse(audit.overall_trend_generated)

    def test_two_distinct_metrics_generate_overall_trend(self):
        obs1 = _metric_obs(metric="watch_time")
        obs2 = _metric_obs(metric="revenue", delta_pct=-8.0)
        claims, _, _, audit = self.pipeline.run([obs1, obs2])
        types = {c["type"] for c in claims}
        self.assertIn("overall_trend", types)
        self.assertTrue(audit.overall_trend_generated)

    def test_non_metric_only_produces_finding(self):
        obs = _anomaly_obs()
        claims, _, _, audit = self.pipeline.run([obs])
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")

    def test_current_window_metric_observation_produces_finding_without_pipeline_error(self):
        obs = _current_window_metric_obs(current_value=88.0)
        claims, _, _, audit = self.pipeline.run([obs])
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["type"], "finding")
        self.assertEqual(audit.error, None)
        self.assertFalse(audit.overall_trend_generated)

    def test_mixed_metric_compare_and_current_window_keeps_overall_trend_based_on_delta_obs(self):
        obs1 = _metric_obs(metric="watch_time")
        obs2 = _metric_obs(metric="revenue", delta_pct=-8.0)
        obs3 = _current_window_metric_obs(metric="sessions", current_value=1000.0)
        claims, _, _, audit = self.pipeline.run([obs1, obs2, obs3])
        trend_claims = [claim for claim in claims if claim["type"] == "overall_trend"]
        self.assertEqual(len(trend_claims), 1)
        self.assertTrue(audit.overall_trend_generated)

    def test_empty_observations_returns_empty_claims(self):
        claims, recs, edges, audit = self.pipeline.run([])
        self.assertEqual(claims, [])
        self.assertEqual(recs, [])
        self.assertEqual(edges, [])
        self.assertEqual(audit.claims_produced, 0)

    def test_pipeline_output_structurally_equivalent_to_legacy(self):
        """Pipeline and synthesize_claims() should produce equivalent structure."""
        from app.evidence import synthesize_claims

        obs = [
            _metric_obs(metric="watch_time", platform="android", delta_pct=-14.0),
        ]
        legacy_claims, _, _ = synthesize_claims(obs)
        pipeline_claims, _, _, _ = self.pipeline.run(obs)

        self.assertEqual(len(legacy_claims), len(pipeline_claims))
        self.assertEqual(legacy_claims[0]["type"], pipeline_claims[0]["type"])
        self.assertEqual(
            legacy_claims[0]["scope"]["slice"],
            pipeline_claims[0]["scope"]["slice"],
        )
        # Confidence may differ by at most 0.05 due to rounding
        legacy_confidence = score_confidence(**legacy_claims[0]["confidence_breakdown"])
        pipeline_confidence = score_confidence(**pipeline_claims[0]["confidence_breakdown"])
        self.assertAlmostEqual(legacy_confidence, pipeline_confidence, delta=0.05)

    def test_audit_log_is_json_serialisable(self):
        obs = [_metric_obs()]
        _, _, _, audit = self.pipeline.run(obs)
        # Should not raise
        serialised = json.dumps(dataclasses.asdict(audit))
        self.assertIsInstance(serialised, str)


# ── Audit log persistence integration ─────────────────────────────────────────


class TestAuditLogPersistence(unittest.TestCase):
    """Integration tests verifying audit logs land in the artifacts table."""

    def _make_service(self, tmp_dir: str):
        import os

        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        meta_path = os.path.join(tmp_dir, "test.meta.sqlite")
        duck_path = os.path.join(tmp_dir, "test.duckdb")

        metadata = SQLiteMetadataStore(meta_path)
        metadata.initialize()
        analytics = DuckDBAnalyticsEngine(duck_path)
        analytics.initialize()

        return SemanticLayerService(metadata, analytics), metadata

    def test_synthesis_without_tentative_claims_persists_empty_promotion_audit(self):
        """synthesize_findings always records promotion-mode audit metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            svc, metadata = self._make_service(tmp)

            session = svc.create_session("test_mode_b", {}, {}, {})
            session_id = session["session_id"]

            # Insert a metric_observation observation using service helpers
            obs = _metric_obs()
            obs["temporal_order"] = 0
            step_id = f"step_{uuid4().hex[:12]}"
            svc._insert_step(step_id, session_id, "metric_query", "test step", {})
            svc._insert_observation(session_id, step_id, obs)

            # Run synthesize_findings with no tentative claims.
            svc._run_synthesis(session_id)

            rows = metadata.query_rows(
                "SELECT artifact_type, content_json FROM artifacts WHERE session_id = ?",
                [session_id],
            )
            synthesis_artifacts = [r for r in rows if r["artifact_type"] == "synthesis_audit"]
            self.assertGreater(len(synthesis_artifacts), 0, "No synthesis_audit artifact found")
            content = json.loads(synthesis_artifacts[0]["content_json"])
            self.assertEqual(content["stage"], "promotion")
            self.assertEqual(content["claims_promoted"], [])

    def test_three_stage_audit_counts_dropped_observations(self):
        pipeline = ThreeStagePipeline()
        bad_obs = {
            "observation_id": "obs_bad_scope",
            "type": "metric_observation",
            "subject": {"metric": "", "slice": None},
            "payload": {"delta_pct": -3.0},
            "significance": {"sample_size": 50, "practical_significance": True},
            "quality": {"sample_size_ok": True, "freshness_ok": True},
        }
        claims, recs, edges, audit = pipeline.run([_metric_obs(), bad_obs])
        self.assertGreaterEqual(len(claims), 1)
        self.assertEqual(recs, [])
        self.assertEqual(edges, [])
        self.assertEqual(audit.dropped_observation_count, 1)

    def test_mode_a_promotion_audit_log_persisted(self):
        """synthesize_findings with tentative claims persists a promotion audit."""
        with tempfile.TemporaryDirectory() as tmp:
            svc, metadata = self._make_service(tmp)

            session = svc.create_session("test_mode_a", {}, {}, {})
            session_id = session["session_id"]

            # Insert a metric_observation observation using service helpers
            obs = _metric_obs()
            obs["temporal_order"] = 0
            step_id_obs = f"step_{uuid4().hex[:12]}"
            svc._insert_step(step_id_obs, session_id, "metric_query", "test step", {})
            svc._insert_observation(session_id, step_id_obs, obs)

            # Insert a tentative claim
            claim_id = f"claim_{uuid4().hex[:12]}"
            svc.metadata.execute(
                """INSERT INTO claims
                   (claim_id, session_id, claim_type, text, scope_json, confidence, status,
                    supporting_observation_ids_json, contradicting_observation_ids_json,
                    confidence_breakdown_json, inference_level, inference_justification_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    claim_id,
                    session_id,
                    "root_cause_candidate",
                    "tentative claim",
                    json.dumps({"slice": {}}),
                    0.72,
                    "tentative",
                    json.dumps([obs["observation_id"]]),
                    json.dumps([]),
                    json.dumps(
                        {
                            "effect_strength": 0.7,
                            "consistency": 0.95,
                            "sample_score": 0.8,
                            "data_quality_score": 0.95,
                            "contradiction_penalty": 0.0,
                        }
                    ),
                    "L0",
                    json.dumps([]),
                ],
            )

            # Run synthesize_findings (Mode A — tentative claim exists)
            svc._run_synthesis(session_id)

            rows = metadata.query_rows(
                "SELECT artifact_type, content_json FROM artifacts WHERE session_id = ?",
                [session_id],
            )
            synthesis_artifacts = [r for r in rows if r["artifact_type"] == "synthesis_audit"]
            self.assertGreater(len(synthesis_artifacts), 0, "No synthesis_audit artifact found")
            content = json.loads(synthesis_artifacts[0]["content_json"])
            self.assertEqual(content["stage"], "promotion")
            self.assertIn("confirmed_count", content)
            self.assertIn("insufficient_count", content)


if __name__ == "__main__":
    unittest.main()
