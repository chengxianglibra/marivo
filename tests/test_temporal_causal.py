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
        """G-2c: TemporalPrecedenceChecker upgrades an L1 claim to L2 when
        supporting observations carry strictly non-overlapping observed_windows.

        Exercises the checker directly with hand-crafted input so the assertion
        is independent of seeded-data randomness while still proving the
        real checker code (not a mock).
        """
        from app.evidence_engine.causal_checkers import TemporalPrecedenceChecker

        checker = TemporalPrecedenceChecker()

        # Two aggregate-query-style observations with non-overlapping windows.
        # These represent day-grouped aggregate observations as G-2 would produce
        # them (observed_window inferred from the event_date group_by column).
        obs_a = {
            "observation_id": "obs_g2c_a",
            "type": "metric_change",
            "subject": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": -4.8},
            "observed_window": {"start": "2026-02-21", "end": "2026-02-27", "granularity": "day"},
        }
        obs_b = {
            "observation_id": "obs_g2c_b",
            "type": "metric_change",
            "subject": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": -5.2},
            "observed_window": {"start": "2026-02-28", "end": "2026-03-06", "granularity": "day"},
        }

        # An L1 claim backed by both observations.
        # L1 is the pre-condition for TemporalPrecedenceChecker (L0 claims are ignored).
        claim_l1 = {
            "claim_id": "claim_g2c_test",
            "inference_level": "L1",
            "scope": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_a", "obs_g2c_b"],
            "contradicting_observations": [],
        }

        upgrades = checker.check([claim_l1], [obs_a, obs_b], [])

        # G-2c core assertion: checker must propose an L2 upgrade
        self.assertEqual(len(upgrades), 1, "Expected exactly one upgrade proposal")
        upgrade = upgrades[0]
        self.assertEqual(upgrade.claim_id, "claim_g2c_test")
        self.assertEqual(upgrade.new_level, "L2")
        self.assertTrue(
            any("temporal_precedence" in t for t in upgrade.justification_tokens),
            f"Justification tokens must reference temporal_precedence: {upgrade.justification_tokens}",
        )

        # Verify the checker correctly rejects overlapping windows (regression guard)
        obs_overlap = {
            "observation_id": "obs_g2c_overlap",
            "type": "metric_change",
            "subject": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": -3.0},
            "observed_window": {"start": "2026-02-25", "end": "2026-03-02", "granularity": "day"},
        }
        claim_overlap = {
            "claim_id": "claim_g2c_overlap",
            "inference_level": "L1",
            "scope": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_a", "obs_g2c_overlap"],
            "contradicting_observations": [],
        }
        no_upgrades = checker.check([claim_overlap], [obs_a, obs_overlap], [])
        self.assertEqual(len(no_upgrades), 0, "Overlapping windows must NOT trigger L2 upgrade")


class TemporallyPrecedesEdgePromotionTests(unittest.TestCase):
    """Causal edge promotion: temporally_precedes edges survive synthesize_findings promotion.

    Proves that:
    1. IncrementalSynthesizer writes a temporally_precedes edge during incremental synthesis.
    2. _run_synthesis (synthesize_findings) preserves the edge via the causal-edge replay path.
    3. The claim remains at L2 after promotion.
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

    def test_causal_edge_survives_synthesize_findings(self) -> None:
        """Causal edge written during incremental synthesis must survive promotion."""
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

            # Two windowed observations with the same (metric, slice) scope and
            # strictly non-overlapping windows so TemporalPrecedenceChecker fires.
            windows = [
                ("obs_g2d_01", "2024-01-01", "2024-01-07"),
                ("obs_g2d_02", "2024-01-10", "2024-01-17"),
            ]
            for oid, wstart, wend in windows:
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
                        json.dumps({"metric": "m_g2d", "slice": {}}),
                        json.dumps({"delta_pct": 6.0}),
                        json.dumps({"sample_size": 200, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "day"}),
                        0,
                    ],
                )

            # Step 1: Incremental synthesis → CrossSlice (L0→L1) then Temporal (L1→L2+edge).
            # Two calls are required: call 1 upgrades to L1, call 2 sees L1 and fires Temporal.
            synth.process(sess_id)   # CrossSlice: L0 → L1
            synth.process(sess_id)   # TemporalPrecedence: L1 → L2 + causal edge

            edges_before = meta.query_rows(
                "SELECT edge_type FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                [sess_id],
            )
            self.assertEqual(len(edges_before), 1, "Edge must be present before synthesize_findings")

            claims_before = meta.query_rows(
                "SELECT inference_level FROM claims WHERE session_id = ? AND status = 'tentative'",
                [sess_id],
            )
            self.assertTrue(
                any(r["inference_level"] == "L2" for r in claims_before),
                "Claim must be at L2 before synthesize_findings",
            )

            # Step 2: synthesize_findings → promotion + edge replay
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
                "temporally_precedes edge must survive synthesize_findings promotion",
            )
            edge = dict(edges_after[0])
            self.assertEqual(edge["from_node_id"], "obs_g2d_01",
                             "Edge must originate from the earliest observation")
            self.assertEqual(edge["from_node_type"], "observation")
            self.assertEqual(edge["to_node_type"], "claim")
            self.assertGreater(edge["weight"], 0, "Edge weight must be positive")
            self.assertIn("obs_g2d_02", edge["explanation"],
                          "Explanation must reference the paired (later) observation")
            self.assertIn("3 days", edge["explanation"],
                          "Explanation must state the lag in days")

            # Verify claim is promoted to L2 and no longer tentative
            claims_after = meta.query_rows(
                "SELECT inference_level, status FROM claims WHERE session_id = ?",
                [sess_id],
            )
            l2_claims = [r for r in claims_after if r["inference_level"] == "L2"]
            self.assertGreater(len(l2_claims), 0, "Claim must remain at L2 after promotion")
            promoted_statuses = {r["status"] for r in l2_claims}
            self.assertTrue(
                promoted_statuses <= {"confirmed", "insufficient"},
                f"Claim must be promoted (not tentative), got: {promoted_statuses}",
            )

    def test_synthesize_findings_replay_idempotent(self) -> None:
        """Promotion replay must not multiply causal edges across repeated calls.

        We simulate two successive PROMOTION-path synthesize_findings runs by
        manually resetting claim status back to 'tentative' between calls.
        This directly verifies that the save → clear → replay pattern produces
        exactly the same number of edges each time, not N*k.
        """
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_idem01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "G-2d idempotency test", "{}", "{}", "{}", "active"],
            )

            for oid, wstart, wend in [
                ("obs_id_01", "2024-02-01", "2024-02-07"),
                ("obs_id_02", "2024-02-10", "2024-02-17"),
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
                        json.dumps({"metric": "m_idem", "slice": {}}),
                        json.dumps({"delta_pct": 7.0}),
                        json.dumps({"sample_size": 150, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "day"}),
                        0,
                    ],
                )

            synth.process(sess_id)   # CrossSlice: L0 → L1
            synth.process(sess_id)   # TemporalPrecedence: L1 → L2 + causal edge

            def _count_tp_edges():
                return len(meta.query_rows(
                    "SELECT 1 FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                    [sess_id],
                ))

            self.assertEqual(_count_tp_edges(), 1, "One edge before first synthesis")

            # First synthesize_findings (PROMOTION path)
            svc._run_synthesis(sess_id)
            self.assertEqual(_count_tp_edges(), 1,
                             "Edge count must be exactly 1 after first synthesize_findings")

            # Simulate a second PROMOTION-path run by resetting the claim to 'tentative'.
            # This is intentionally artificial to isolate the replay path: we want to
            # prove that save→clear→replay is idempotent and never produces N*k edges.
            meta.execute(
                "UPDATE claims SET status = 'tentative' WHERE session_id = ?",
                [sess_id],
            )

            # Second synthesize_findings (PROMOTION path again on the same claim)
            svc._run_synthesis(sess_id)
            self.assertEqual(
                _count_tp_edges(), 1,
                "Repeated synthesize_findings must not multiply causal edges (expected exactly 1)",
            )


if __name__ == "__main__":
    unittest.main()
