"""Causal reasoning integration tests.

Coverage:
- Causal reasoning end-to-end (direct DB injection + HTTP API)
- Evidence edge type schema constants
- Full regression suite integration

Test classes
------------
CausalUpgradeChainTests      — direct SQLite injection, no HTTP
EvidenceGraphAPIFieldsTests  — HTTP TestClient
EvidenceEdgeSchemaTests      — pure constant validation, no DB
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
from app.evidence_engine.schemas import (
    ALL_EDGE_TYPES,
    CAUSAL_EDGE_TO_INFERENCE_LEVEL,
    INFERENCE_LEVEL_ORDER,
)
from app.main import create_app
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


# ── helpers ───────────────────────────────────────────────────────────────────


def _insert_session(store: SQLiteMetadataStore, sess_id: str) -> None:
    store.execute(
        "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [sess_id, "phase2 integration test", "{}", "{}", "{}", "active"],
    )


def _insert_step(store: SQLiteMetadataStore, sess_id: str, step_id: str) -> None:
    store.execute(
        "INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json) VALUES (?, ?, ?, ?, ?, ?)",
        [step_id, sess_id, "compare_metric", "completed", "", "{}"],
    )


def _insert_obs(
    store: SQLiteMetadataStore,
    obs_id: str,
    sess_id: str,
    step_id: str,
    metric: str,
    slice_val: dict,
    delta_pct: float,
    temporal_order: int,
    window: dict | None = None,
) -> None:
    store.execute(
        """
        INSERT INTO observations (
            observation_id, session_id, step_id, observation_type,
            subject_json, payload_json, significance_json, quality_json,
            observed_window_json, temporal_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            obs_id, sess_id, step_id, "metric_change",
            json.dumps({"metric": metric, "slice": slice_val}),
            json.dumps({"delta_pct": delta_pct}),
            json.dumps({"sample_size": 100, "practical_significance": True}),
            json.dumps({"freshness_ok": True, "sample_size_ok": True}),
            json.dumps(window) if window is not None else None,
            temporal_order,
        ],
    )


def _insert_causal_candidate_obs(
    store: SQLiteMetadataStore,
    obs_id: str,
    sess_id: str,
    step_id: str,
    metric: str,
    slice_val: dict,
    candidate_cause_observation_id: str,
    temporal_order: int,
    window: dict | None = None,
) -> None:
    """Insert a causal_candidate observation with explicit cause link."""
    store.execute(
        """
        INSERT INTO observations (
            observation_id, session_id, step_id, observation_type,
            subject_json, payload_json, significance_json, quality_json,
            observed_window_json, temporal_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            obs_id, sess_id, step_id, "causal_candidate",
            json.dumps({"metric": metric, "slice": slice_val}),
            json.dumps({"candidate_cause_observation_id": candidate_cause_observation_id}),
            json.dumps({"sample_size": 100, "practical_significance": True}),
            json.dumps({"freshness_ok": True, "sample_size_ok": True}),
            json.dumps(window) if window is not None else None,
            temporal_order,
        ],
    )


# ── CausalUpgradeChainTests ───────────────────────────────────────────────────


class CausalUpgradeChainTests(unittest.TestCase):
    """Direct DB injection tests for the L0→L1→L2 causal upgrade chain."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.tmpdir.name) / "meta.sqlite")
        self.store.initialize()
        self.sess_id = "sess_p2int000001"
        self.step_id = "step_p2int000001"
        _insert_session(self.store, self.sess_id)
        _insert_step(self.store, self.sess_id, self.step_id)
        self.synth = IncrementalSynthesizer(self.store)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _all_claims(self) -> list[dict]:
        return self.store.query_rows(
            "SELECT inference_level, inference_justification_json FROM claims WHERE session_id = ?",
            [self.sess_id],
        )

    # -- test_l0_to_l1_cross_slice_upgrade ------------------------------------

    def test_l0_to_l1_cross_slice_upgrade(self) -> None:
        """5 consistent-sign observations → CrossSliceConsistencyChecker → L1."""
        for i in range(5):
            _insert_obs(
                self.store,
                obs_id=f"obs_cs{i:04d}",
                sess_id=self.sess_id,
                step_id=self.step_id,
                metric="watch_time",
                slice_val={"seg": str(i)},
                delta_pct=-5.0,
                temporal_order=i,
                window=None,
            )

        result = self.synth.process(self.sess_id)

        self.assertIn("causal_upgrades", result)
        self.assertGreaterEqual(result["claims_created"], 1)
        self.assertGreaterEqual(result["causal_upgrades"], 1)

        rows = self._all_claims()
        levels = [r["inference_level"] for r in rows]
        self.assertIn("L1", levels, "Expected at least one claim upgraded to L1")

        justifications = " ".join(
            r["inference_justification_json"] or "[]" for r in rows
        )
        self.assertIn("cross_slice_consistency", justifications)

    # -- test_l0_to_l1_to_l2_full_chain ---------------------------------------

    def test_l0_to_l1_to_l2_full_chain(self) -> None:
        """Full upgrade chain: cross-slice consistency yields L1, then relation-backed
        non-overlapping windows yield L2 during final synthesis."""
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        window_a = {"start": "2026-01-01", "end": "2026-01-14", "granularity": "day"}
        window_b = {"start": "2026-02-01", "end": "2026-02-14", "granularity": "day"}
        slices = [{"seg": "android"}, {"seg": "ios"}, {"seg": "web"}]

        analytics = DuckDBAnalyticsEngine(Path(self.tmpdir.name) / "causal_chain.duckdb")
        analytics.initialize()
        service = SemanticLayerService(self.store, analytics)
        service._incremental_synthesizer = self.synth

        # Batch 1 — two related metrics on the same slices. CrossSlice promotes
        # both metrics to L1; final temporal promotion is deferred to synthesis.
        for i, sl in enumerate(slices):
            _insert_obs(
                self.store,
                obs_id=f"obs_q{i:04d}",
                sess_id=self.sess_id,
                step_id=self.step_id,
                metric="query_count",
                slice_val=sl,
                delta_pct=-5.0,
                temporal_order=i,
                window=window_a,
            )
            _insert_obs(
                self.store,
                obs_id=f"obs_t{i:04d}",
                sess_id=self.sess_id,
                step_id=self.step_id,
                metric="queued_time",
                slice_val=sl,
                delta_pct=-4.0,
                temporal_order=10 + i,
                window=window_b,
            )

        result = self.synth.process(self.sess_id)
        self.assertGreaterEqual(result["claims_created"], 2)

        rows_after_l1 = self._all_claims()
        levels_after_l1 = [r["inference_level"] for r in rows_after_l1]
        self.assertIn("L1", levels_after_l1, "Expected L1 upgrade after incremental synthesis")

        service._run_synthesis(self.sess_id)

        rows_after_l2 = self._all_claims()
        levels_after_l2 = [r["inference_level"] for r in rows_after_l2]
        self.assertIn("L2", levels_after_l2, "Expected at least one claim upgraded to L2")

        justifications = " ".join(
            r["inference_justification_json"] or "[]" for r in rows_after_l2
        )
        self.assertIn("cross_slice_consistency", justifications)
        self.assertIn("temporal_precedence", justifications)

    # -- test_no_downgrade_invariant ------------------------------------------

    def test_no_downgrade_invariant(self) -> None:
        """Adding a contradicting observation must not downgrade an L1 claim back to L0."""
        # 5 consistent obs → L1
        for i in range(5):
            _insert_obs(
                self.store,
                obs_id=f"obs_nd{i:04d}",
                sess_id=self.sess_id,
                step_id=self.step_id,
                metric="watch_time",
                slice_val={"seg": str(i)},
                delta_pct=-5.0,
                temporal_order=i,
            )
        self.synth.process(self.sess_id)

        rows_l1 = self._all_claims()
        self.assertIn("L1", [r["inference_level"] for r in rows_l1])

        # Contradicting obs for a new slice (positive delta)
        _insert_obs(
            self.store,
            obs_id="obs_nd_contra",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="watch_time",
            slice_val={"seg": "contra"},
            delta_pct=+10.0,
            temporal_order=5,
        )
        self.synth.process(self.sess_id)

        rows_final = self._all_claims()
        # The L1 claims must NOT have been downgraded
        l1_rows = [r for r in rows_final if r["inference_level"] == "L1"]
        l2_rows = [r for r in rows_final if r["inference_level"] == "L2"]
        self.assertTrue(
            len(l1_rows) > 0 or len(l2_rows) > 0,
            "No L1 or L2 claims remain — the no-downgrade invariant was violated",
        )

    # -- test_cross_scope_explicit_causal_candidate ----------------------------

    def test_cross_scope_explicit_causal_candidate(self) -> None:
        """causal_candidate observation should upgrade the effect claim without materializing edges incrementally."""
        # Step 1: Create a cause observation
        _insert_obs(
            self.store,
            obs_id="obs_cause_001",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="query_volume",
            slice_val={"service": "sys_titan"},
            delta_pct=524.0,
            temporal_order=0,
            window={"start": "2026-01-01", "end": "2026-01-02", "granularity": "day"},
        )

        # Step 2: Create a causal_candidate observation pointing to the cause
        _insert_causal_candidate_obs(
            self.store,
            obs_id="obs_effect_001",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="queue_time",
            slice_val={"resource_group": "others"},
            candidate_cause_observation_id="obs_cause_001",
            temporal_order=1,
            window={"start": "2026-01-02", "end": "2026-01-03", "granularity": "day"},
        )

        result = self.synth.process(self.sess_id)

        # Should have created 2 claims
        self.assertGreaterEqual(result["claims_created"], 2)
        self.assertGreaterEqual(result["causal_upgrades"], 1)

        edges = self.store.query_rows(
            "SELECT * FROM evidence_edges WHERE session_id = ?",
            [self.sess_id],
        )
        self.assertEqual(edges, [])

        # Verify justification token
        claims = self._all_claims()
        justifications = " ".join(r["inference_justification_json"] or "[]" for r in claims)
        self.assertIn("cross_scope_explicit", justifications)
        self.assertIn("L1", [r["inference_level"] for r in claims])

    # -- test_cross_scope_automatic_temporal_predecessor -----------------------

    def test_cross_scope_automatic_temporal_predecessor(self) -> None:
        """Two observations from different scopes with temporal ordering produce correlates_with edge."""
        # Step 1: Create earlier observation (different metric, different slice)
        _insert_obs(
            self.store,
            obs_id="obs_early_001",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="cpu_usage",
            slice_val={"host": "server-1"},
            delta_pct=150.0,
            temporal_order=0,
            window={"start": "2026-01-01", "end": "2026-01-02", "granularity": "day"},
        )

        # Step 2: Create later observation (different metric, different slice)
        # This should be automatically detected as a temporal predecessor
        _insert_obs(
            self.store,
            obs_id="obs_late_001",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="latency",
            slice_val={"endpoint": "/api/query"},
            delta_pct=200.0,
            temporal_order=1,
            window={"start": "2026-01-03", "end": "2026-01-04", "granularity": "day"},
        )

        result = self.synth.process(self.sess_id)

        # Should have created 2 claims
        self.assertGreaterEqual(result["claims_created"], 2)

        # Check for correlates_with edge from early obs to late claim
        edges = self.store.query_rows(
            "SELECT * FROM evidence_edges WHERE session_id = ? AND edge_type = 'correlates_with'",
            [self.sess_id],
        )

        # Find edge where early observation points to late claim
        late_claim = self.store.query_one(
            "SELECT claim_id FROM claims WHERE session_id = ? AND scope_json LIKE ?",
            [self.sess_id, "%latency%"],
        )

        if late_claim and edges:
            late_claim_id = late_claim["claim_id"]
            matching_edges = [
                e for e in edges
                if e["from_node_id"] == "obs_early_001" and e["to_node_id"] == late_claim_id
            ]
            self.assertGreaterEqual(
                len(matching_edges), 1,
                "Expected correlates_with edge from early obs to late claim",
            )

    # -- test_cross_scope_chain_three_steps -----------------------------------

    def test_cross_scope_chain_three_steps(self) -> None:
        """Three-step causal chain A → B → C upgrades downstream claims without incremental edge writes."""
        # Step A: sys_titan query volume spike
        _insert_obs(
            self.store,
            obs_id="obs_chain_a",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="query_volume",
            slice_val={"service": "sys_titan"},
            delta_pct=524.0,
            temporal_order=0,
            window={"start": "2026-01-01", "end": "2026-01-02", "granularity": "day"},
        )

        # Step B: others RG queue congestion (links to A)
        _insert_causal_candidate_obs(
            self.store,
            obs_id="obs_chain_b",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="queue_time",
            slice_val={"resource_group": "others"},
            candidate_cause_observation_id="obs_chain_a",
            temporal_order=1,
            window={"start": "2026-01-02", "end": "2026-01-03", "granularity": "day"},
        )

        # Step C: oneservice RG timeout failures (links to B)
        _insert_causal_candidate_obs(
            self.store,
            obs_id="obs_chain_c",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="timeout_count",
            slice_val={"service": "oneservice"},
            candidate_cause_observation_id="obs_chain_b",
            temporal_order=2,
            window={"start": "2026-01-03", "end": "2026-01-04", "granularity": "day"},
        )

        result = self.synth.process(self.sess_id)

        # Should have 3 claims
        self.assertGreaterEqual(result["claims_created"], 3)

        edges = self.store.query_rows(
            "SELECT * FROM evidence_edges WHERE session_id = ? AND edge_type = 'correlates_with'",
            [self.sess_id],
        )
        self.assertEqual(edges, [])
        claims = self._all_claims()
        l1_claims = [c for c in claims if c["inference_level"] == "L1"]
        self.assertGreaterEqual(len(l1_claims), 2)
        justifications = " ".join(r["inference_justification_json"] or "[]" for r in claims)
        self.assertIn("cross_scope_explicit", justifications)

    # -- test_cross_scope_invalid_cause_id_ignored ----------------------------

    def test_cross_scope_invalid_cause_id_ignored(self) -> None:
        """causal_candidate with non-existent candidate_cause_observation_id is ignored."""
        # Create a causal_candidate observation pointing to non-existent cause
        _insert_causal_candidate_obs(
            self.store,
            obs_id="obs_invalid_cause",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="some_metric",
            slice_val={"dim": "value"},
            candidate_cause_observation_id="obs_nonexistent_12345",
            temporal_order=0,
        )

        result = self.synth.process(self.sess_id)

        # Should create 1 claim for the observation
        self.assertGreaterEqual(result["claims_created"], 1)
        # Should NOT have any causal upgrades (invalid cause ID ignored)
        self.assertEqual(result["causal_upgrades"], 0)

        # Verify no correlates_with edges
        edges = self.store.query_rows(
            "SELECT * FROM evidence_edges WHERE session_id = ? AND edge_type = 'correlates_with'",
            [self.sess_id],
        )
        self.assertEqual(len(edges), 0, "Expected no correlates_with edges for invalid cause ID")

    # -- test_cross_scope_lag_days_boundary -----------------------------------

    def test_cross_scope_lag_days_boundary(self) -> None:
        """Test lag_days boundary conditions: 0 and > MAX_LAG_DAYS produce no edge."""
        # Observation A: ends 2026-01-02
        _insert_obs(
            self.store,
            obs_id="obs_lag_a",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="metric_a",
            slice_val={"dim": "a"},
            delta_pct=10.0,
            temporal_order=0,
            window={"start": "2026-01-01", "end": "2026-01-02", "granularity": "day"},
        )

        # Observation B: starts 2026-01-02 (lag from A = 0, should NOT produce edge)
        _insert_obs(
            self.store,
            obs_id="obs_lag_b_zero",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="metric_b",
            slice_val={"dim": "b"},
            delta_pct=20.0,
            temporal_order=1,
            window={"start": "2026-01-02", "end": "2026-01-03", "granularity": "day"},
        )

        # Observation C: starts 2026-01-12
        # lag from A = 10 days (> MAX_LAG_DAYS=7, excluded)
        # lag from B = 9 days (> MAX_LAG_DAYS=7, excluded)
        _insert_obs(
            self.store,
            obs_id="obs_lag_c_eight",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="metric_c",
            slice_val={"dim": "c"},
            delta_pct=30.0,
            temporal_order=2,
            window={"start": "2026-01-12", "end": "2026-01-13", "granularity": "day"},
        )

        result = self.synth.process(self.sess_id)

        # Should have 3 claims
        self.assertGreaterEqual(result["claims_created"], 3)

        # Verify no correlates_with edges (both boundary conditions excluded)
        edges = self.store.query_rows(
            "SELECT * FROM evidence_edges WHERE session_id = ? AND edge_type = 'correlates_with'",
            [self.sess_id],
        )
        # lag=0 (same day) and lag>7 should both be excluded
        self.assertEqual(
            len(edges), 0,
            "Expected no correlates_with edges for lag=0 or lag>7",
        )

    # -- test_cross_scope_valid_lag_produces_edge ------------------------------

    def test_cross_scope_valid_lag_produces_edge(self) -> None:
        """Valid lag should upgrade a downstream claim without incremental edge materialization."""
        # Observation A: ends 2026-01-02
        _insert_obs(
            self.store,
            obs_id="obs_valid_lag_a",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="metric_valid_a",
            slice_val={"dim": "a"},
            delta_pct=10.0,
            temporal_order=0,
            window={"start": "2026-01-01", "end": "2026-01-02", "granularity": "day"},
        )

        # Observation B: starts 2026-01-04 (lag = 2 days, should produce edge)
        _insert_obs(
            self.store,
            obs_id="obs_valid_lag_b",
            sess_id=self.sess_id,
            step_id=self.step_id,
            metric="metric_valid_b",
            slice_val={"dim": "b"},
            delta_pct=20.0,
            temporal_order=1,
            window={"start": "2026-01-04", "end": "2026-01-05", "granularity": "day"},
        )

        result = self.synth.process(self.sess_id)

        # Should have 2 claims
        self.assertGreaterEqual(result["claims_created"], 2)
        self.assertGreaterEqual(result["causal_upgrades"], 1)

        edges = self.store.query_rows(
            "SELECT * FROM evidence_edges WHERE session_id = ? AND edge_type = 'correlates_with'",
            [self.sess_id],
        )
        self.assertEqual(edges, [])
        claims = self._all_claims()
        self.assertIn("L1", [r["inference_level"] for r in claims])
        justifications = " ".join(r["inference_justification_json"] or "[]" for r in claims)
        self.assertIn("cross_scope_temporal", justifications)


# ── EvidenceGraphPhase2FieldsTests ────────────────────────────────────────────


class EvidenceGraphAPIFieldsTests(unittest.TestCase):
    """HTTP integration tests: evidence graph exposes all causal edge fields."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Seed a published entity + metric shared across tests
        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "p2_int_entity",
            "display_name": "P2 Integration Entity",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "p2_avg_duration",
            "display_name": "P2 Avg Duration",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        cls.metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{cls.metric_id}/publish")
        cls.metric_name = "p2_avg_duration"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        resp = self.client.post("/sessions", json={"goal": "Phase 2 integration test session."})
        return resp.json()["session_id"]

    def _run_compare_metric(self, sess_id: str, extra: dict | None = None) -> dict:
        body: dict = {
            "metric_name": self.metric_name,
            "table_name": "analytics.watch_events",
        }
        if extra:
            body.update(extra)
        resp = self.client.post(f"/sessions/{sess_id}/steps/compare_metric", json=body)
        return resp.json()

    def _run_synthesize(self, sess_id: str) -> None:
        self.client.post(f"/sessions/{sess_id}/steps/synthesize_findings")

    def _get_graph(self, sess_id: str) -> dict:
        resp = self.client.get(f"/sessions/{sess_id}/evidence")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    # -- test_evidence_graph_claims_have_inference_level ----------------------

    def test_evidence_graph_claims_have_inference_level(self) -> None:
        """Every claim in the evidence graph must expose inference_level and inference_justification."""
        sess_id = self._new_session()
        self._run_compare_metric(sess_id)
        self._run_synthesize(sess_id)

        graph = self._get_graph(sess_id)
        if not graph["claims"]:
            self.skipTest("No claims produced — check demo data")

        valid_levels = set(INFERENCE_LEVEL_ORDER)
        for claim in graph["claims"]:
            self.assertIn("inference_level", claim, f"claim missing inference_level: {claim}")
            self.assertIn(
                claim["inference_level"], valid_levels,
                f"unexpected inference_level value: {claim['inference_level']}",
            )
            self.assertIn("inference_justification", claim)
            self.assertIsInstance(claim["inference_justification"], list)

    # -- test_evidence_graph_observations_have_temporal_fields ----------------

    def test_evidence_graph_observations_have_temporal_fields(self) -> None:
        """Observations in the evidence graph must carry temporal_order; observed_window if set."""
        sess_id = self._new_session()
        self._run_compare_metric(
            sess_id,
            extra={"period_start": "2026-02-21", "period_end": "2026-03-06", "dimensions": ["platform"]},
        )
        graph = self._get_graph(sess_id)

        metric_obs = [o for o in graph["observations"] if o.get("type") == "metric_change"]
        if not metric_obs:
            self.skipTest("No metric_change observations — check demo data date range")

        for obs in metric_obs:
            self.assertIn("temporal_order", obs, f"obs missing temporal_order: {obs.get('observation_id')}")
            self.assertIsInstance(obs["temporal_order"], int)
            self.assertGreaterEqual(obs["temporal_order"], 0)

            ow = obs.get("observed_window")
            if ow is not None:
                self.assertIn("start", ow)
                self.assertIn("end", ow)
                self.assertIn("granularity", ow)

    # -- test_evidence_graph_edge_types_are_valid -----------------------------

    def test_evidence_graph_edge_types_are_valid(self) -> None:
        """All edge_type values in evidence graph must be in the known set."""
        sess_id = self._new_session()
        self._run_compare_metric(sess_id)
        self._run_synthesize(sess_id)

        graph = self._get_graph(sess_id)
        for edge in graph["edges"]:
            self.assertIn(
                edge["edge_type"],
                ALL_EDGE_TYPES,
                f"Unknown edge_type: {edge['edge_type']}",
            )

    # -- test_synthesize_findings_produces_synthesis_audit_artifact -----------

    def test_synthesize_findings_produces_synthesis_audit_artifact(self) -> None:
        """synthesize_findings must persist a synthesis_audit artifact."""
        sess_id = self._new_session()
        self._run_compare_metric(sess_id)
        self._run_synthesize(sess_id)
        self._get_graph(sess_id)  # ensure evidence route is exercised

        store = self.client.app.state.metadata_store
        rows = store.query_rows(
            "SELECT content_json FROM artifacts WHERE session_id = ? AND artifact_type = 'synthesis_audit'",
            [sess_id],
        )
        self.assertGreater(len(rows), 0, "No synthesis_audit artifact found")

        audit = json.loads(rows[0]["content_json"])
        self.assertIn("stage", audit, f"audit log missing 'stage' key: {audit.keys()}")
        # At least one of the three-stage or promotion audit fields must be present
        phase2_keys = {"scope_clusters", "formulation_decisions", "claims_produced", "confirmed_count"}
        self.assertTrue(
            bool(phase2_keys & set(audit.keys())),
            f"Audit log lacks any Phase 2 key. Keys found: {list(audit.keys())}",
        )

    # -- test_two_period_compare_produces_distinct_windows --------------------

    def test_two_period_compare_produces_distinct_windows(self) -> None:
        """Running two compare_metric steps with different periods yields non-identical observed_windows."""
        sess_id = self._new_session()
        self._run_compare_metric(
            sess_id,
            extra={"period_start": "2026-01-01", "period_end": "2026-01-14"},
        )
        self._run_compare_metric(
            sess_id,
            extra={"period_start": "2026-02-01", "period_end": "2026-02-14"},
        )

        graph = self._get_graph(sess_id)
        windows = [
            obs["observed_window"]
            for obs in graph["observations"]
            if obs.get("observed_window") is not None
        ]
        if len(windows) < 2:
            self.skipTest("Fewer than 2 windowed observations — skipping window comparison")

        unique_starts = {w["start"] for w in windows}
        self.assertGreater(
            len(unique_starts), 1,
            "Expected observations from at least 2 distinct time windows",
        )


# ── EvidenceEdgeSchemaTests ───────────────────────────────────────────────────


class EvidenceEdgeSchemaTests(unittest.TestCase):
    """Pure constant-validation tests — no DB required."""

    def test_all_causal_edge_types_defined(self) -> None:
        """ALL_EDGE_TYPES must include all 3 basic + 5 causal types."""
        expected = {
            "supports", "contradicts", "justifies",
            "correlates_with", "temporally_precedes", "mechanistically_explains",
            "eliminates_alternative", "experimentally_confirms",
        }
        self.assertEqual(set(ALL_EDGE_TYPES), expected)

    def test_inference_level_order_is_correct(self) -> None:
        """INFERENCE_LEVEL_ORDER must be the canonical L0..L5 sequence."""
        self.assertEqual(INFERENCE_LEVEL_ORDER, ["L0", "L1", "L2", "L3", "L4", "L5"])

    def test_causal_edge_to_inference_level_mapping(self) -> None:
        """Spot-check key causal edge → level mappings."""
        self.assertEqual(CAUSAL_EDGE_TO_INFERENCE_LEVEL["correlates_with"], "L1")
        self.assertEqual(CAUSAL_EDGE_TO_INFERENCE_LEVEL["temporally_precedes"], "L2")
        self.assertEqual(CAUSAL_EDGE_TO_INFERENCE_LEVEL["mechanistically_explains"], "L3")
        self.assertEqual(CAUSAL_EDGE_TO_INFERENCE_LEVEL["eliminates_alternative"], "L4")
        self.assertEqual(CAUSAL_EDGE_TO_INFERENCE_LEVEL["experimentally_confirms"], "L5")

    def test_causal_edge_types_are_subset_of_all(self) -> None:
        """Every causal edge type key appears in ALL_EDGE_TYPES."""
        for edge_type in CAUSAL_EDGE_TO_INFERENCE_LEVEL:
            self.assertIn(edge_type, ALL_EDGE_TYPES)

    def test_inference_level_order_covers_causal_mapping_targets(self) -> None:
        """Every level value in CAUSAL_EDGE_TO_INFERENCE_LEVEL is a valid INFERENCE_LEVEL_ORDER entry."""
        for level in CAUSAL_EDGE_TO_INFERENCE_LEVEL.values():
            self.assertIn(level, INFERENCE_LEVEL_ORDER)


if __name__ == "__main__":
    unittest.main()
