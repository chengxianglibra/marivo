from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TemporallyPrecedesEdgePromotionTests(unittest.TestCase):
    """Causal edge materialization: temporally_precedes edges are emitted at synthesis time.

    Proves that:
    1. IncrementalSynthesizer does not materialize temporal causal edges.
    2. _run_synthesis materializes claim-to-claim temporally_precedes edges from the final pipeline output.
    3. The promoted effect claim remains at L2 after synthesis.
    """

    def _make_service_with_synth(self, tmpdir: str):
        """Return (service, synth) wired together with real SQLite + DuckDB stores."""

        from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        meta = SQLiteMetadataStore(Path(tmpdir) / "test.meta.sqlite")
        meta.initialize()
        analytics = DuckDBAnalyticsEngine(Path(tmpdir) / "test.duckdb")
        analytics.initialize()

        svc = SemanticLayerService(meta, analytics)
        synth = IncrementalSynthesizer(meta)
        svc._incremental_synthesizer = synth
        return svc, synth, meta

    def test_causal_edge_materialized_during_synthesize_findings(self) -> None:
        """Temporal causal edges should be created only during final synthesis."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_prom01"
            step_id = "step_g2d_prom01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "G-2d promotion test", "{}", "{}", "{}", "active"],
            )

            # Two related claims in the same slice with non-overlapping windows.
            windows = [
                ("obs_g2d_01", "query_count", 12.0, "2024-01-01", "2024-01-07"),
                ("obs_g2d_02", "queued_time", 18.0, "2024-01-10", "2024-01-17"),
            ]
            for oid, metric, delta_pct, wstart, wend in windows:
                meta.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, step_id, observation_type,
                        subject_json, payload_json, significance_json, quality_json,
                        observed_window_json, temporal_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        oid,
                        sess_id,
                        step_id,
                        "metric_observation",
                        json.dumps({"metric": metric, "slice": {"user": "sys_titan"}}),
                        json.dumps({"delta_pct": delta_pct}),
                        json.dumps({"sample_size": 200, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "day"}),
                        0,
                    ],
                )

            synth.process(sess_id)

            edges_before = meta.query_rows(
                "SELECT edge_type FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                [sess_id],
            )
            self.assertEqual(
                len(edges_before), 0, "Incremental synthesis must not materialize causal edges"
            )

            # Step 2: synthesize_findings → promotion + final edge materialization
            svc._run_synthesis(sess_id)

            edges_after = meta.query_rows(
                """
                SELECT edge_type, from_node_id, from_node_type,
                       to_node_id, to_node_type, weight, explanation
                FROM evidence_edges
                WHERE session_id = ? AND edge_type = 'temporally_precedes'
                """,
                [sess_id],
            )
            self.assertEqual(
                len(edges_after),
                1,
                "temporally_precedes edge must be materialized during synthesize_findings",
            )
            edge = dict(edges_after[0])
            self.assertEqual(edge["from_node_type"], "claim")
            self.assertEqual(edge["to_node_type"], "claim")
            self.assertGreater(edge["weight"], 0, "Edge weight must be positive")
            self.assertIn("3 days", edge["explanation"], "Explanation must state the lag in days")
            self.assertNotEqual(edge["from_node_id"], edge["to_node_id"])
            claims_after = meta.query_rows(
                "SELECT claim_id, scope_json, inference_level, status FROM claims WHERE session_id = ?",
                [sess_id],
            )
            claim_by_id = {row["claim_id"]: row for row in claims_after}
            self.assertIn(edge["from_node_id"], claim_by_id)
            self.assertIn(edge["to_node_id"], claim_by_id)
            self.assertIn("3 days", edge["explanation"], "Explanation must state the lag in days")

            # Verify the effect claim is promoted to L2 and no longer tentative
            l2_claims = [r for r in claims_after if r["inference_level"] == "L2"]
            self.assertGreater(len(l2_claims), 0, "Claim must remain at L2 after promotion")
            promoted_statuses = {r["status"] for r in l2_claims}
            self.assertTrue(
                promoted_statuses <= {"confirmed", "insufficient"},
                f"Claim must be promoted (not tentative), got: {promoted_statuses}",
            )

    def test_synthesize_findings_remains_idempotent_for_causal_edges(self) -> None:
        """Repeated synthesize_findings runs must not multiply causal edges."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_idem01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "G-2d idempotency test", "{}", "{}", "{}", "active"],
            )

            for oid, metric, delta_pct, wstart, wend in [
                ("obs_id_01", "query_count", 14.0, "2024-02-01", "2024-02-07"),
                ("obs_id_02", "queued_time", 19.0, "2024-02-10", "2024-02-17"),
            ]:
                meta.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, step_id, observation_type,
                        subject_json, payload_json, significance_json, quality_json,
                        observed_window_json, temporal_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        oid,
                        sess_id,
                        "step_idem_01",
                        "metric_observation",
                        json.dumps({"metric": metric, "slice": {"user": "sys_titan"}}),
                        json.dumps({"delta_pct": delta_pct}),
                        json.dumps({"sample_size": 150, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "day"}),
                        0,
                    ],
                )

            synth.process(sess_id)

            def _count_tp_edges():
                return len(
                    meta.query_rows(
                        "SELECT 1 FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                        [sess_id],
                    )
                )

            self.assertEqual(
                _count_tp_edges(), 0, "No causal edge should exist before final synthesis"
            )

            # First synthesize_findings
            svc._run_synthesis(sess_id)
            self.assertEqual(
                _count_tp_edges(), 1, "Edge count must be exactly 1 after first synthesize_findings"
            )

            # Simulate a second promotion-path run by resetting the claim to tentative.
            meta.execute(
                "UPDATE claims SET status = 'tentative' WHERE session_id = ?",
                [sess_id],
            )

            # Second synthesize_findings on the same claim
            svc._run_synthesis(sess_id)
            self.assertEqual(
                _count_tp_edges(),
                1,
                "Repeated synthesize_findings must not multiply causal edges (expected exactly 1)",
            )

    def test_synthesize_findings_remains_idempotent_for_derived_observations(self) -> None:
        """Repeated synthesize_findings runs must replace derived synth observations, not accumulate them."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_idem_obs01"
            step_id = "step_g2d_idem_obs01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "derived observation idempotency test", "{}", "{}", "{}", "active"],
            )

            for oid, metric, current_value, delta_pct, wstart, wend in [
                ("obs_h_01", "query_count", 100.0, 5.0, "2024-01-01T01:00", "2024-01-01T02:00"),
                ("obs_h_02", "query_count", 180.0, 9.0, "2024-01-01T02:00", "2024-01-01T03:00"),
                ("obs_h_03", "query_count", 120.0, 6.0, "2024-01-01T03:00", "2024-01-01T04:00"),
                ("obs_h_04", "queued_time", 4.0, 2.0, "2024-01-01T02:00", "2024-01-01T03:00"),
                ("obs_h_05", "queued_time", 10.0, 7.0, "2024-01-01T03:00", "2024-01-01T04:00"),
                ("obs_h_06", "queued_time", 6.0, 3.0, "2024-01-01T04:00", "2024-01-01T05:00"),
                ("obs_h_07", "cpu_time", 2.0, 1.0, "2024-01-01T02:00", "2024-01-01T03:00"),
            ]:
                meta.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, step_id, observation_type,
                        subject_json, payload_json, significance_json, quality_json,
                        observed_window_json, temporal_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        oid,
                        sess_id,
                        step_id,
                        "metric_observation",
                        json.dumps(
                            {
                                "metric": metric,
                                "slice": {"user": "sys_titan", "log_hour": wstart},
                                "temporal_group_by_columns": ["log_hour"],
                            }
                        ),
                        json.dumps({"current_value": current_value, "delta_pct": delta_pct}),
                        json.dumps({"sample_size": 200, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "hour"}),
                        0,
                    ],
                )

            synth.process(sess_id)

            def _count_obs(obs_type: str) -> int:
                row = meta.query_one(
                    "SELECT COUNT(*) AS cnt FROM observations WHERE session_id = ? AND observation_type = ?",
                    [sess_id, obs_type],
                )
                return int(row["cnt"]) if row else 0

            svc._run_synthesis(sess_id)
            self.assertEqual(_count_obs("cross_metric_correlation"), 1)
            self.assertEqual(_count_obs("temporal_pattern"), 2)

            meta.execute(
                "UPDATE claims SET status = 'tentative' WHERE session_id = ?",
                [sess_id],
            )

            svc._run_synthesis(sess_id)
            self.assertEqual(_count_obs("cross_metric_correlation"), 1)
            self.assertEqual(_count_obs("temporal_pattern"), 2)

    def test_hourly_peak_decay_edge_materialized_during_synthesize_findings(self) -> None:
        """Hourly lead-lag should materialize a claim-to-claim temporally_precedes edge."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_hourly01"
            step_id = "step_g2d_hourly01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "G-2d hourly promotion test", "{}", "{}", "{}", "active"],
            )

            hourly_windows = [
                ("obs_h_01", "query_count", 100.0, 5.0, "2024-01-01T01:00", "2024-01-01T02:00"),
                ("obs_h_02", "query_count", 180.0, 9.0, "2024-01-01T02:00", "2024-01-01T03:00"),
                ("obs_h_03", "query_count", 120.0, 6.0, "2024-01-01T03:00", "2024-01-01T04:00"),
                ("obs_h_04", "queued_time", 4.0, 2.0, "2024-01-01T02:00", "2024-01-01T03:00"),
                ("obs_h_05", "queued_time", 10.0, 7.0, "2024-01-01T03:00", "2024-01-01T04:00"),
                ("obs_h_06", "queued_time", 6.0, 3.0, "2024-01-01T04:00", "2024-01-01T05:00"),
            ]
            for oid, metric, current_value, delta_pct, wstart, wend in hourly_windows:
                meta.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, step_id, observation_type,
                        subject_json, payload_json, significance_json, quality_json,
                        observed_window_json, temporal_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        oid,
                        sess_id,
                        step_id,
                        "metric_observation",
                        json.dumps(
                            {
                                "metric": metric,
                                "slice": {"user": "sys_titan", "log_hour": wstart},
                                "temporal_group_by_columns": ["log_hour"],
                            }
                        ),
                        json.dumps({"current_value": current_value, "delta_pct": delta_pct}),
                        json.dumps({"sample_size": 200, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "hour"}),
                        0,
                    ],
                )

            synth.process(sess_id)

            claims_before = meta.query_rows(
                "SELECT claim_id, scope_json, inference_level FROM claims WHERE session_id = ? ORDER BY claim_id",
                [sess_id],
            )
            self.assertEqual(
                len(claims_before), 2, f"Expected 2 folded hourly claims, got: {claims_before}"
            )
            self.assertTrue(
                all(row["inference_level"] in {"L0", "L1"} for row in claims_before),
                f"Expected pre-synthesis hourly claims to remain below L2, got: {claims_before}",
            )

            edges_before = meta.query_rows(
                "SELECT edge_type FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                [sess_id],
            )
            self.assertEqual(
                len(edges_before),
                0,
                "Incremental synthesis must not materialize hourly temporal edges",
            )

            svc._run_synthesis(sess_id)

            edges_after = meta.query_rows(
                """
                SELECT edge_type, from_node_id, to_node_id, weight, explanation
                FROM evidence_edges
                WHERE session_id = ? AND edge_type = 'temporally_precedes'
                """,
                [sess_id],
            )
            self.assertEqual(
                len(edges_after), 1, "Expected one hourly temporally_precedes edge after synthesis"
            )
            edge = dict(edges_after[0])
            self.assertGreater(edge["weight"], 0)
            self.assertIn("peaks at 2024-01-01 02:00", edge["explanation"])
            self.assertIn("1 hours later", edge["explanation"])

            claims_after = meta.query_rows(
                "SELECT claim_id, inference_level, status FROM claims WHERE session_id = ? ORDER BY claim_id",
                [sess_id],
            )
            l2_claims = [row for row in claims_after if row["inference_level"] == "L2"]
            self.assertEqual(
                len(l2_claims),
                1,
                f"Expected exactly one L2 hourly effect claim, got: {claims_after}",
            )
            self.assertIn(l2_claims[0]["status"], {"confirmed", "insufficient"})


if __name__ == "__main__":
    unittest.main()
