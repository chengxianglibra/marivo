from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class TemporalWindowInferenceTests(unittest.TestCase):
    """Temporal window inference: date-grouped aggregate_query observations carry observed_window.

    This test proves that:
    1. aggregate_query with a temporal group_by column (e.g., event_date) populates observed_window
    2. The evidence pipeline can upgrade claims from L1 to L2 via TemporalPrecedenceChecker
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "g2_temporal.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Register a metric used by test_l1_to_l2_upgrade_via_temporal_precedence.
        r = cls.client.post("/semantic/entities", json={
            "name": "g2_watch_session", "display_name": "G2 Watch Session",
            "keys": ["session_id"],
        })
        ent_id = r.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{ent_id}/publish")
        r = cls.client.post("/semantic/metrics", json={
            "name": "g2_event_count", "display_name": "G2 Event Count",
            "definition_sql": "COUNT(*)",
            "dimensions": ["platform"],
            "entity_id": ent_id,
        })
        met_id = r.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{met_id}/publish")
        cls.g2_metric_name = "g2_event_count"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_aggregate_query_infers_observed_window_from_event_date(self) -> None:
        """aggregate_query with event_date in group_by should infer observed_window."""
        session_id = self.client.post(
            "/sessions", json={"goal": "G-2 temporal window inference test."},
        ).json()["session_id"]

        # Run aggregate grouped by a temporal column (event_date)
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["event_date", "platform", "count(*) as cnt"],
                "group_by": ["event_date", "platform"],
                "order_by": "event_date",
                "limit": 5,
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()

        # Verify observations were extracted
        self.assertIn("observations", result)
        observations = result["observations"]
        self.assertGreater(len(observations), 0)

        # G-2: Verify observed_window is populated on each observation
        for obs in observations:
            self.assertIn("observed_window", obs, "G-2: observed_window should be inferred from event_date")
            window = obs["observed_window"]
            self.assertIn("start", window)
            self.assertIn("end", window)
            self.assertIn("granularity", window)
            # Day granularity for date column
            self.assertEqual(window["granularity"], "day")

    def test_aggregate_query_explicit_observed_window_column(self) -> None:
        """aggregate_query with explicit observed_window_column param should use it."""
        session_id = self.client.post(
            "/sessions", json={"goal": "G-2 explicit column test."},
        ).json()["session_id"]

        # Use explicit observed_window_column
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["event_date", "count(*) as cnt"],
                "group_by": ["event_date"],
                "observed_window_column": "event_date",  # explicit override
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()

        observations = result.get("observations", [])
        self.assertGreater(len(observations), 0)
        for obs in observations:
            self.assertIn("observed_window", obs)

    def test_aggregate_query_yyyymmdd_format(self) -> None:
        """aggregate_query should parse YYYYMMDD format temporal values."""
        session_id = self.client.post(
            "/sessions", json={"goal": "G-2 YYYYMMDD format test."},
        ).json()["session_id"]

        # Unit test the parser directly
        from app.evidence_engine.extractors.aggregate import _parse_temporal_value
        from datetime import date

        parsed_date, granularity = _parse_temporal_value("20240115")
        self.assertEqual(parsed_date, date(2024, 1, 15))
        self.assertEqual(granularity, "day")

    def test_l1_to_l2_upgrade_via_temporal_precedence(self) -> None:
        """G-2c: TemporalPrecedenceChecker upgrades the later related claim to L2 when
        related claims carry strictly non-overlapping observed_windows.

        Exercises the checker directly with hand-crafted input so the assertion
        is independent of seeded-data randomness while still proving the
        real checker code (not a mock).
        """
        from app.evidence_engine.causal_checkers import TemporalPrecedenceChecker

        checker = TemporalPrecedenceChecker()

        # Two aggregate-query-style observations with non-overlapping windows.
        # These represent claim-level signals connected by an existing relation.
        obs_a = {
            "observation_id": "obs_g2c_a",
            "type": "metric_change",
            "subject": {"metric": "query_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": 12.0},
            "observed_window": {"start": "2026-02-21", "end": "2026-02-27", "granularity": "day"},
        }
        obs_b = {
            "observation_id": "obs_g2c_b",
            "type": "metric_change",
            "subject": {"metric": "queued_time", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": 18.0},
            "observed_window": {"start": "2026-02-28", "end": "2026-03-06", "granularity": "day"},
        }

        cause_claim = {
            "claim_id": "claim_g2c_cause",
            "inference_level": "L1",
            "status": "confirmed",
            "scope": {"metric": "query_count", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_a"],
            "contradicting_observations": [],
        }
        effect_claim = {
            "claim_id": "claim_g2c_effect",
            "inference_level": "L1",
            "status": "confirmed",
            "scope": {"metric": "queued_time", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_b"],
            "contradicting_observations": [],
        }
        relation = {
            "from_claim_id": "claim_g2c_cause",
            "to_claim_id": "claim_g2c_effect",
            "relation_type": "correlates_with",
            "weight": 0.92,
            "match_basis": {"category": "exact_match", "direction": "up"},
            "score_components": {},
            "supporting_observation_ids": ["obs_g2c_a", "obs_g2c_b"],
            "explanation": "test relation",
        }

        upgrades = checker.check(
            [cause_claim, effect_claim],
            [obs_a, obs_b],
            [],
            relations=[relation],
        )

        # G-2c core assertion: checker must propose an L2 upgrade
        self.assertEqual(len(upgrades), 1, "Expected exactly one upgrade proposal")
        upgrade = upgrades[0]
        self.assertEqual(upgrade.claim_id, "claim_g2c_effect")
        self.assertEqual(upgrade.new_level, "L2")
        self.assertTrue(
            any("temporal_precedence" in t for t in upgrade.justification_tokens),
            f"Justification tokens must reference temporal_precedence: {upgrade.justification_tokens}",
        )

        # Verify the checker correctly rejects overlapping windows (regression guard)
        obs_overlap = {
            "observation_id": "obs_g2c_overlap",
            "type": "metric_change",
            "subject": {"metric": "queued_time", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": 15.0},
            "observed_window": {"start": "2026-02-25", "end": "2026-03-02", "granularity": "day"},
        }
        overlap_effect = {
            "claim_id": "claim_g2c_overlap",
            "inference_level": "L1",
            "status": "confirmed",
            "scope": {"metric": "queued_time", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_overlap"],
            "contradicting_observations": [],
        }
        no_upgrades = checker.check(
            [cause_claim, overlap_effect],
            [obs_a, obs_overlap],
            [],
            relations=[{
                **relation,
                "to_claim_id": "claim_g2c_overlap",
                "supporting_observation_ids": ["obs_g2c_a", "obs_g2c_overlap"],
            }],
        )
        self.assertEqual(len(no_upgrades), 0, "Overlapping windows must NOT trigger L2 upgrade")

    def test_aggregate_query_temporal_scope_folding_enables_l2_upgrade(self) -> None:
        from app.evidence_engine.causal_checkers import TemporalPrecedenceChecker

        session_id = self.client.post(
            "/sessions", json={"goal": "P1 temporal scope folding test."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["event_date", "platform", "count(*) as cnt"],
                "group_by": ["event_date", "platform"],
                "order_by": "event_date, platform",
                "metric": self.g2_metric_name,
                "temporal_group_by_columns": ["event_date"],
                "limit": 50,
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        observations = result.get("observations", [])
        self.assertGreater(len(observations), 0)

        checker = TemporalPrecedenceChecker()
        ios_observations = sorted(
            [
                observation
                for observation in observations
                if observation.get("subject", {}).get("slice", {}).get("platform") == "ios"
                and observation.get("observed_window") is not None
            ],
            key=lambda observation: observation["observed_window"]["start"],
        )
        self.assertGreaterEqual(len(ios_observations), 2, f"Need at least two ios observations, got: {observations}")
        first_obs, second_obs = ios_observations[0], ios_observations[-1]
        first_claim = {
            "claim_id": "claim_fold_a",
            "inference_level": "L1",
            "status": "confirmed",
            "scope": {"metric": "query_count", "slice": {"platform": "ios"}},
            "supporting_observations": [first_obs["observation_id"]],
            "contradicting_observations": [],
        }
        second_claim = {
            "claim_id": "claim_fold_b",
            "inference_level": "L1",
            "status": "confirmed",
            "scope": {"metric": "queued_time", "slice": {"platform": "ios"}},
            "supporting_observations": [second_obs["observation_id"]],
            "contradicting_observations": [],
        }
        upgrades = checker.check(
            [first_claim, second_claim],
            observations,
            [],
            relations=[{
                "from_claim_id": first_claim["claim_id"],
                "to_claim_id": second_claim["claim_id"],
                "relation_type": "correlates_with",
                "weight": 0.92,
                "match_basis": {"category": "exact_match", "direction": "up"},
                "score_components": {},
                "supporting_observation_ids": (
                    first_claim["supporting_observations"] + second_claim["supporting_observations"]
                ),
                "explanation": "test relation",
            }],
        )
        self.assertEqual(len(upgrades), 1, f"Expected L2 upgrade from related temporal claims, got: {upgrades}")
        self.assertEqual(upgrades[0].new_level, "L2")
        self.assertEqual(upgrades[0].claim_id, second_claim["claim_id"])
        self.assertTrue(
            any("temporal_precedence" in token for token in upgrades[0].justification_tokens),
            f"Unexpected justification tokens: {upgrades[0].justification_tokens}",
        )


class TemporallyPrecedesEdgePromotionTests(unittest.TestCase):
    """Causal edge materialization: temporally_precedes edges are emitted at synthesis time.

    Proves that:
    1. IncrementalSynthesizer does not materialize temporal causal edges.
    2. _run_synthesis materializes claim-to-claim temporally_precedes edges from the final pipeline output.
    3. The promoted effect claim remains at L2 after synthesis.
    """

    def _make_service_with_synth(self, tmpdir: str):
        """Return (service, synth) wired together with real SQLite + DuckDB stores."""
        import json
        from pathlib import Path

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
        import tempfile

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
                        oid, sess_id, step_id, "metric_change",
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
            self.assertEqual(len(edges_before), 0, "Incremental synthesis must not materialize causal edges")

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
                len(edges_after), 1,
                "temporally_precedes edge must be materialized during synthesize_findings",
            )
            edge = dict(edges_after[0])
            self.assertEqual(edge["from_node_type"], "claim")
            self.assertEqual(edge["to_node_type"], "claim")
            self.assertGreater(edge["weight"], 0, "Edge weight must be positive")
            self.assertIn("3 days", edge["explanation"],
                          "Explanation must state the lag in days")
            self.assertNotEqual(edge["from_node_id"], edge["to_node_id"])
            claims_after = meta.query_rows(
                "SELECT claim_id, scope_json, inference_level, status FROM claims WHERE session_id = ?",
                [sess_id],
            )
            claim_by_id = {row["claim_id"]: row for row in claims_after}
            self.assertIn(edge["from_node_id"], claim_by_id)
            self.assertIn(edge["to_node_id"], claim_by_id)
            self.assertIn("3 days", edge["explanation"],
                          "Explanation must state the lag in days")

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
        import tempfile

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
                        oid, sess_id, "step_idem_01", "metric_change",
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
                return len(meta.query_rows(
                    "SELECT 1 FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                    [sess_id],
                ))

            self.assertEqual(_count_tp_edges(), 0, "No causal edge should exist before final synthesis")

            # First synthesize_findings
            svc._run_synthesis(sess_id)
            self.assertEqual(_count_tp_edges(), 1,
                             "Edge count must be exactly 1 after first synthesize_findings")

            # Simulate a second promotion-path run by resetting the claim to tentative.
            meta.execute(
                "UPDATE claims SET status = 'tentative' WHERE session_id = ?",
                [sess_id],
            )

            # Second synthesize_findings on the same claim
            svc._run_synthesis(sess_id)
            self.assertEqual(
                _count_tp_edges(), 1,
                "Repeated synthesize_findings must not multiply causal edges (expected exactly 1)",
            )


if __name__ == "__main__":
    unittest.main()
