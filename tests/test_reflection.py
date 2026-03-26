"""Tests for M-11 Reflection Context API.

Tests cover:
  - build_reflection_context() function (unit)
  - GET /sessions/{id}/reflection-context endpoint (HTTP)
  - PlanningService.patch_plan_incremental() (unit)
  - POST /sessions/{id}/plans/{id}/patch endpoint (HTTP)
  - ReplanningService.apply_patch() (unit, delegates to planning)
  - reflection.enabled config gate
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.evidence_engine.causal_basis import (
    GAP_MISSING_OBSERVED_WINDOW,
    GAP_NORMALISE_WORKLOAD_VOLUME,
)
from app.main import create_app
from app.planning import PlanningService
from app.planner.replanning import ReplanningService
from app.reflection.context import build_reflection_context
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


# ── Shared helpers ────────────────────────────────────────────────────────────

def _insert_claim(
    store: SQLiteMetadataStore,
    session_id: str,
    *,
    inference_level: str = "L0",
    status: str = "tentative",
    confidence: float = 0.6,
) -> str:
    claim_id = f"claim_{uuid4().hex[:12]}"
    store.execute(
        """
        INSERT INTO claims (
            claim_id, session_id, claim_type, text, scope_json, confidence, status,
            supporting_observation_ids_json, contradicting_observation_ids_json,
            confidence_breakdown_json, inference_level, inference_justification_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            claim_id, session_id, "trend", "test claim", "{}", confidence, status,
            "[]", "[]", "{}", inference_level, "[]",
        ],
    )
    return claim_id


def _insert_recommendation(
    store: SQLiteMetadataStore,
    session_id: str,
    claim_id: str,
    *,
    inference_level: str = "L0",
    scope: dict | None = None,
) -> str:
    rec_id = f"rec_{uuid4().hex[:12]}"
    from app.evidence_engine.causal_basis import (  # noqa: PLC0415
        SessionSummary,
        build_causal_basis,
    )

    # Build causal_basis from a minimal claim-like dict (no observation context)
    causal_basis = build_causal_basis(
        {
            "inference_level": inference_level,
            "confidence": 0.6,
            "text": "test claim",
            "scope": scope or {},
        },
        [],
        SessionSummary(
            has_comparable_slices=False,
            has_windowed_observations=False,
            metric_names=frozenset(),
        ),
    )
    store.execute(
        """
        INSERT INTO recommendations (
            rec_id, session_id, claim_id, action_text, priority,
            expected_impact, risk, validation_metric_json, causal_basis_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            rec_id, session_id, claim_id, "take action", "P1",
            "some impact", "low", "{}", json.dumps(causal_basis),
        ],
    )
    return rec_id


def _insert_observation(
    store: SQLiteMetadataStore,
    session_id: str,
    *,
    metric: str,
    slice_dict: dict | None = None,
    observed_window: dict | None = None,
    temporal_order: int = 0,
    delta_pct: float = -5.0,
) -> str:
    obs_id = f"obs_{uuid4().hex[:12]}"
    store.execute(
        """
        INSERT INTO observations (
            observation_id, session_id, step_id, observation_type,
            subject_json, payload_json, significance_json, quality_json,
            observed_window_json, temporal_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            obs_id,
            session_id,
            "step_test",
            "metric_observation",
            json.dumps({"metric": metric, "slice": slice_dict or {}}),
            json.dumps({"delta_pct": delta_pct}),
            "{}",
            "{}",
            json.dumps(observed_window) if observed_window is not None else None,
            temporal_order,
        ],
    )
    return obs_id


# ── Fixture setup ─────────────────────────────────────────────────────────────

class ReflectionContextUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "refl.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "refl.duckdb"
        cls.store = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.store.initialize()
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.store, cls.analytics)
        cls.planning = PlanningService(cls.store)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        session = self.service.create_session("reflection test", {}, {}, {})
        return session["session_id"]

    # ── Test 1: required keys present ──────────────────────────────────────

    def test_build_reflection_context_required_keys(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        for key in ("session_id", "plan_id", "readiness_signal", "readiness_score",
                    "tentative_claims", "evidence_gaps", "entity_update_suggestions",
                    "available_step_types"):
            self.assertIn(key, ctx, f"Missing key: {key}")
        self.assertEqual(ctx["session_id"], session_id)
        self.assertIsNone(ctx["plan_id"])

    # ── Test 2: tentative_claims includes L0 claim ─────────────────────────

    def test_tentative_claims_includes_l0_claim(self) -> None:
        session_id = self._new_session()
        claim_id = _insert_claim(self.store, session_id, inference_level="L0", status="tentative")
        ctx = build_reflection_context(self.store, session_id)
        claim_ids = [c["claim_id"] for c in ctx["tentative_claims"]]
        self.assertIn(claim_id, claim_ids)

    # ── Test 3: evidence_gaps is session-level deduplicated (G-3c) ────────

    def test_evidence_gaps_session_level_structure(self) -> None:
        """evidence_gaps is now a session-level deduplicated list (breaking change G-3c)."""
        session_id = self._new_session()
        claim_id = _insert_claim(self.store, session_id, inference_level="L0")
        _insert_recommendation(self.store, session_id, claim_id, inference_level="L0")
        ctx = build_reflection_context(self.store, session_id)
        self.assertIsInstance(ctx["evidence_gaps"], list)
        self.assertTrue(len(ctx["evidence_gaps"]) > 0)
        gap = ctx["evidence_gaps"][0]
        # New session-level structure
        self.assertIn("gap_key", gap)
        self.assertIn("text", gap)
        self.assertIn("suggested_validation", gap)
        self.assertIn("affected_claims", gap)
        self.assertIsInstance(gap["affected_claims"], list)
        self.assertIn(claim_id, gap["affected_claims"])

    # ── Test 4: plan_id query param passed through ─────────────────────────

    def test_plan_id_appears_in_context(self) -> None:
        session_id = self._new_session()
        plan = self.planning.draft_plan(
            session_id,
            [{"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}}],
        )
        ctx = build_reflection_context(self.store, session_id, plan_id=plan["plan_id"])
        self.assertEqual(ctx["plan_id"], plan["plan_id"])

    # ── Test 5: readiness_score is a float in [0,1] ────────────────────────

    def test_readiness_score_is_float(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        self.assertIsInstance(ctx["readiness_score"], float)
        self.assertGreaterEqual(ctx["readiness_score"], 0.0)
        self.assertLessEqual(ctx["readiness_score"], 1.0)

    # ── Test 6: available_step_types lists all step types ─────────────────

    def test_available_step_types_all_present(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        expected = {"metric_query", "profile_table", "sample_rows", "aggregate_query", "attribute_change", "correlate_metrics", "synthesize_findings"}
        self.assertEqual(set(ctx["available_step_types"]), expected)

    # ── Test 17: scope-aware confounder — missing observed_window ──────────

    def test_scope_aware_confounder_missing_observed_window(self) -> None:
        """A claim with supporting observations but no observed_window gets missing_observed_window gap."""
        session_id = self._new_session()
        obs_id = _insert_observation(
            self.store,
            session_id,
            metric="elapsed_time",
            slice_dict={"cluster": "k8sbi-bi1"},
        )
        # Insert a claim with elapsed_time metric and the above observation
        claim_id = f"claim_{uuid4().hex[:12]}"
        self.store.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json,
                confidence_breakdown_json, inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                claim_id, session_id, "root_cause_candidate",
                "elapsed_time declined 5.0% for cluster=k8sbi-bi1 (tentative)",
                '{"metric": "elapsed_time", "slice": {"cluster": "k8sbi-bi1"}}',
                0.6, "tentative",
                f'["{obs_id}"]', "[]", "{}", "L0", "[]",
            ],
        )
        ctx = build_reflection_context(self.store, session_id)
        tc = next(c for c in ctx["tentative_claims"] if c["claim_id"] == claim_id)
        # Should get scope-aware confounders mentioning observed_window
        self.assertTrue(
            any("observed_window" in t for t in tc["unresolved_confounders"]),
            f"Expected missing_observed_window in confounders; got: {tc['unresolved_confounders']}",
        )

    # ── Test 17b: windowed observations satisfy temporal evidence path ────

    def test_observed_window_reflection_context_uses_temporal_evidence_without_redefining_readiness(self) -> None:
        """Windowed supporting observations clear temporal confounders; readiness stays support-count based."""
        session_id = self._new_session()
        obs_ids = [
            _insert_observation(
                self.store,
                session_id,
                metric="elapsed_time",
                slice_dict={"cluster": "k8sbi-bi1"},
                observed_window={"start": "2026-03-01", "end": "2026-03-02"},
                temporal_order=1,
                delta_pct=-4.0,
            ),
            _insert_observation(
                self.store,
                session_id,
                metric="elapsed_time",
                slice_dict={"cluster": "k8sbi-bi1"},
                observed_window={"start": "2026-03-03", "end": "2026-03-04"},
                temporal_order=2,
                delta_pct=-6.0,
            ),
        ]
        claim_id = f"claim_{uuid4().hex[:12]}"
        self.store.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json,
                confidence_breakdown_json, inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                claim_id, session_id, "root_cause_candidate",
                "elapsed_time declined for cluster=k8sbi-bi1 (tentative)",
                '{"metric": "elapsed_time", "slice": {"cluster": "k8sbi-bi1"}}',
                0.7, "tentative",
                json.dumps(obs_ids), "[]", "{}", "L0", "[]",
            ],
        )

        ctx = build_reflection_context(self.store, session_id)
        tc = next(c for c in ctx["tentative_claims"] if c["claim_id"] == claim_id)
        confounders = tc["unresolved_confounders"]

        self.assertFalse(
            any("observed_window" in t for t in confounders),
            f"Did not expect missing_observed_window when observations are windowed; got: {confounders}",
        )
        self.assertFalse(
            any("temporal ordering" in t for t in confounders),
            f"Did not expect missing_temporal_ordering when temporal evidence is present; got: {confounders}",
        )
        self.assertAlmostEqual(ctx["readiness_signal"]["evidence_sufficiency"], 2.0 / 3.0, places=4)

    # ── Test 18: scope-aware confounder — normalise workload volume ────────

    def test_scope_aware_confounder_normalise_workload(self) -> None:
        """Resource-slice claim with comparable slices gets normalise_workload_volume gap."""
        session_id = self._new_session()
        # Insert two observations for the same metric but different clusters
        for cluster in ("k8sbi-bi1", "k8sbi-bi2"):
            obs_id = f"obs_{uuid4().hex[:12]}"
            self.store.execute(
                """
                INSERT INTO observations (
                    observation_id, session_id, step_id, observation_type,
                    subject_json, payload_json, significance_json, quality_json,
                    observed_window_json, temporal_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                [
                    obs_id, session_id, "step_test2", "metric_observation",
                    f'{{"metric": "query_count", "slice": {{"cluster": "{cluster}"}}}}',
                    '{"delta_pct": 10.0}', '{}', '{}',
                ],
            )
        # Insert a claim scoped to one cluster
        claim_id = f"claim_{uuid4().hex[:12]}"
        self.store.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json,
                confidence_breakdown_json, inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                claim_id, session_id, "root_cause_candidate",
                "query_count increased 10% for cluster=k8sbi-bi1 (tentative)",
                '{"metric": "query_count", "slice": {"cluster": "k8sbi-bi1"}}',
                0.6, "tentative",
                "[]", "[]", "{}", "L0", "[]",
            ],
        )
        ctx = build_reflection_context(self.store, session_id)
        tc = next(c for c in ctx["tentative_claims"] if c["claim_id"] == claim_id)
        self.assertTrue(
            any("workload" in t or "normalise" in t for t in tc["unresolved_confounders"]),
            f"Expected normalise_workload_volume in confounders; got: {tc['unresolved_confounders']}",
        )

    # ── Test 18b: confounder auto-resolution via confirmed claim ────────

    def test_confounder_auto_resolved_by_confirmed_volume_claim(self) -> None:
        """normalise_workload_volume gap is filtered when a confirmed query_count claim exists."""
        session_id = self._new_session()
        # Two observations for same metric, different clusters → triggers normalise_workload_volume
        obs_ids = []
        for cluster in ("k8sbi-bi1", "k8sbi-bi2"):
            obs_id = f"obs_{uuid4().hex[:12]}"
            obs_ids.append(obs_id)
            self.store.execute(
                """
                INSERT INTO observations (
                    observation_id, session_id, step_id, observation_type,
                    subject_json, payload_json, significance_json, quality_json,
                    observed_window_json, temporal_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                [
                    obs_id, session_id, "step_test_ar", "metric_observation",
                    f'{{"metric": "query_count", "slice": {{"cluster": "{cluster}"}}}}',
                    '{"delta_pct": 10.0}', '{}', '{}',
                ],
            )
        # Tentative claim scoped to one cluster — would normally get normalise_workload_volume
        tentative_claim_id = f"claim_{uuid4().hex[:12]}"
        self.store.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json,
                confidence_breakdown_json, inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                tentative_claim_id, session_id, "root_cause_candidate",
                "queued_time increased 58% for cluster=k8sbi-bi1 (tentative)",
                '{"metric": "queued_time", "slice": {"cluster": "k8sbi-bi1"}}',
                0.6, "tentative",
                f'["{obs_ids[0]}"]', "[]", "{}", "L0", "[]",
            ],
        )
        # Confirmed claim about query_count — this resolves the workload volume confounder
        confirmed_claim_id = f"claim_{uuid4().hex[:12]}"
        self.store.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json,
                confidence_breakdown_json, inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                confirmed_claim_id, session_id, "root_cause_candidate",
                "query_count increased 30% for cluster=k8sbi-bi1",
                '{"metric": "query_count", "slice": {"cluster": "k8sbi-bi1"}}',
                0.91, "confirmed",
                f'["{obs_ids[0]}"]', "[]", "{}", "L0", "[]",
            ],
        )
        ctx = build_reflection_context(self.store, session_id)
        tc = next(c for c in ctx["tentative_claims"] if c["claim_id"] == tentative_claim_id)
        # The normalise_workload_volume gap should be resolved and filtered out
        self.assertFalse(
            any("workload" in t or "normalise" in t for t in tc["unresolved_confounders"]),
            f"Expected normalise_workload_volume to be auto-resolved; got: {tc['unresolved_confounders']}",
        )

    # ── Test 19: evidence_gaps deduplication across two claims ────────────

    def test_evidence_gaps_deduplicated_across_claims(self) -> None:
        """Two recommendations with the same gap_key appear as one session-level gap."""
        session_id = self._new_session()
        claim_id_a = _insert_claim(self.store, session_id, inference_level="L0")
        claim_id_b = _insert_claim(self.store, session_id, inference_level="L0")
        _insert_recommendation(self.store, session_id, claim_id_a)
        _insert_recommendation(self.store, session_id, claim_id_b)
        ctx = build_reflection_context(self.store, session_id)
        # Both claims share the same fallback gap(s); they should be deduplicated
        gap_keys = [g["gap_key"] for g in ctx["evidence_gaps"]]
        # No duplicate gap_keys
        self.assertEqual(len(gap_keys), len(set(gap_keys)))
        # Both claim IDs appear somewhere in affected_claims
        all_affected = {cid for g in ctx["evidence_gaps"] for cid in g["affected_claims"]}
        self.assertIn(claim_id_a, all_affected)
        self.assertIn(claim_id_b, all_affected)


# ── patch_plan_incremental unit tests ─────────────────────────────────────────

class PlanPatchIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "patch.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "patch.duckdb"
        cls.store = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.store.initialize()
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.store, cls.analytics)
        cls.planning = PlanningService(cls.store)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_plan(self) -> tuple[str, str]:
        session = self.service.create_session("patch test", {}, {}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [{"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}}],
        )
        return session["session_id"], plan["plan_id"]

    # ── Test 7: add_steps appends step ────────────────────────────────────

    def test_add_steps_appends_step(self) -> None:
        _, plan_id = self._make_plan()
        result = self.planning.patch_plan_incremental(
            plan_id,
            add_steps=[{"step_type": "profile_table", "params": {"table_name": "analytics.ad_events"}}],
        )
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][1]["step_type"], "profile_table")
        self.assertEqual(result["steps"][1]["params"]["table_name"], "analytics.ad_events")
        self.assertIn("validation", result)

    # ── Test 8: modify_steps updates params ───────────────────────────────

    def test_modify_steps_updates_params(self) -> None:
        _, plan_id = self._make_plan()
        result = self.planning.patch_plan_incremental(
            plan_id,
            modify_steps=[{"index": 0, "params": {"limit": 100}}],
        )
        self.assertEqual(result["steps"][0]["params"]["limit"], 100)
        # Original param still present
        self.assertEqual(result["steps"][0]["params"]["table_name"], "analytics.watch_events")

    # ── Test 9: skip_steps marks step skipped ─────────────────────────────

    def test_skip_steps_marks_step_skipped(self) -> None:
        _, plan_id = self._make_plan()
        result = self.planning.patch_plan_incremental(
            plan_id,
            skip_steps=[0],
        )
        self.assertEqual(result["steps"][0]["status"], "skipped")

    # ── Test 10: invalid step_type raises ValueError ───────────────────────

    def test_invalid_step_type_raises_value_error(self) -> None:
        _, plan_id = self._make_plan()
        with self.assertRaises(ValueError):
            self.planning.patch_plan_incremental(
                plan_id,
                add_steps=[{"step_type": "nonexistent_step"}],
            )

    # ── Test 11: executing plan raises ValueError ──────────────────────────

    def test_executing_plan_raises_value_error(self) -> None:
        _, plan_id = self._make_plan()
        # Directly set status to executing (bypassing state machine)
        self.store.execute(
            "UPDATE plans SET status = 'executing' WHERE plan_id = ?",
            [plan_id],
        )
        with self.assertRaises(ValueError):
            self.planning.patch_plan_incremental(
                plan_id,
                add_steps=[{"step_type": "profile_table"}],
            )


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

class ReflectionContextHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "http_refl.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        resp = cls.client.post(
            "/sessions",
            json={"goal": "HTTP reflection test", "constraints": {}, "budget": {}, "policy": {}},
        )
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    # ── Test 12: GET reflection-context returns 200 ────────────────────────

    def test_get_reflection_context_returns_200(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/reflection-context")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ("session_id", "readiness_signal", "readiness_score",
                    "tentative_claims", "evidence_gaps", "available_step_types"):
            self.assertIn(key, data)

    # ── Test 13: plan_id query param round-trips ───────────────────────────

    def test_reflection_context_with_plan_id_query(self) -> None:
        plan_resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={"steps": [{"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}}]},
        )
        plan_id = plan_resp.json()["plan_id"]
        resp = self.client.get(
            f"/sessions/{self.session_id}/reflection-context",
            params={"plan_id": plan_id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["plan_id"], plan_id)

    # ── Test 14: unknown session returns 404 ──────────────────────────────

    def test_unknown_session_returns_404(self) -> None:
        resp = self.client.get("/sessions/sess_doesnotexist/reflection-context")
        self.assertEqual(resp.status_code, 404)

    # ── Test 15: POST /plans/{id}/patch via HTTP ───────────────────────────

    def test_plan_patch_http_add_step(self) -> None:
        plan_resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={"steps": [{"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}}]},
        )
        plan_id = plan_resp.json()["plan_id"]
        patch_resp = self.client.post(
            f"/sessions/{self.session_id}/plans/{plan_id}/patch",
            json={"add_steps": [{"step_type": "profile_table", "params": {"table_name": "analytics.ad_events"}}]},
        )
        self.assertEqual(patch_resp.status_code, 200)
        data = patch_resp.json()
        self.assertEqual(len(data["steps"]), 2)
        self.assertIn("validation", data)

    # ── Test 16: reflection disabled returns 404 ───────────────────────────

    def test_reflection_disabled_returns_404(self) -> None:
        config_dir = tempfile.TemporaryDirectory()
        config_file = Path(config_dir.name) / "factum.yaml"
        config_file.write_text("reflection:\n  enabled: false\n")
        db_path = Path(config_dir.name) / "disabled.duckdb"
        get_seeded_duckdb_path(db_path)
        app = create_app(db_path, config_path=str(config_file))
        client = TestClient(app)
        # Create a session first
        sess_resp = client.post(
            "/sessions",
            json={"goal": "test", "constraints": {}, "budget": {}, "policy": {}},
        )
        session_id = sess_resp.json()["session_id"]
        resp = client.get(f"/sessions/{session_id}/reflection-context")
        self.assertEqual(resp.status_code, 404)
        config_dir.cleanup()


# ── G-5c: entity_update_suggestions in reflection context ──────────────────

class EntityUpdateSuggestionsTests(unittest.TestCase):
    """G-5c: reflection context exposes entity_update_suggestions from recommendations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(self.temp_dir.name) / "g5c.meta.sqlite"
        duck_path = Path(self.temp_dir.name) / "g5c.duckdb"
        self.store = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _new_session(self) -> str:
        session_id = f"sess_{uuid4().hex[:12]}"
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [session_id, "test", "{}", "{}", "{}", "open"],
        )
        return session_id

    def _insert_rec_with_patch(self, session_id: str, claim_id: str, entity_patch: dict) -> str:
        rec_id = f"rec_{uuid4().hex[:12]}"
        self.store.execute(
            """
            INSERT INTO recommendations (
                rec_id, session_id, claim_id, action_text, priority,
                expected_impact, risk, validation_metric_json, entity_patch_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [rec_id, session_id, claim_id, "action", "P1", "impact", "low",
             "{}", json.dumps(entity_patch)],
        )
        return rec_id

    def test_entity_update_suggestions_empty_when_no_recs(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        self.assertIn("entity_update_suggestions", ctx)
        self.assertEqual(ctx["entity_update_suggestions"], [])

    def test_entity_update_suggestions_populated_from_patch(self) -> None:
        session_id = self._new_session()
        claim_id = _insert_claim(self.store, session_id, status="confirmed")
        patch = {
            "entity_id": "ent_abc",
            "entity_name": "video",
            "column_name": "elapsed_time",
            "field": "fields.elapsed_time.unit",
            "current_value": None,
            "suggested_value": "milliseconds",
            "confidence": 0.75,
            "source": "heuristic",
            "metric_name": "elapsed_time",
        }
        rec_id = self._insert_rec_with_patch(session_id, claim_id, patch)
        ctx = build_reflection_context(self.store, session_id)
        suggestions = ctx["entity_update_suggestions"]
        self.assertEqual(len(suggestions), 1)
        s = suggestions[0]
        self.assertEqual(s["entity_id"], "ent_abc")
        self.assertEqual(s["column_name"], "elapsed_time")
        self.assertEqual(s["field"], "fields.elapsed_time.unit")
        self.assertEqual(s["suggested_value"], "milliseconds")
        self.assertEqual(s["confidence"], 0.75)
        self.assertEqual(s["rec_id"], rec_id)

    def test_entity_update_suggestions_deduplicates(self) -> None:
        """Two recs with identical (entity_id, field, suggested_value) → one suggestion."""
        session_id = self._new_session()
        claim_id = _insert_claim(self.store, session_id, status="confirmed")
        patch = {
            "entity_id": "ent_abc", "entity_name": "video",
            "column_name": "elapsed_time", "field": "fields.elapsed_time.unit",
            "current_value": None, "suggested_value": "milliseconds",
            "confidence": 0.75, "source": "heuristic", "metric_name": "elapsed_time",
        }
        self._insert_rec_with_patch(session_id, claim_id, patch)
        self._insert_rec_with_patch(session_id, claim_id, patch)
        ctx = build_reflection_context(self.store, session_id)
        self.assertEqual(len(ctx["entity_update_suggestions"]), 1)

    def test_entity_update_suggestions_different_fields_not_deduplicated(self) -> None:
        session_id = self._new_session()
        claim_id = _insert_claim(self.store, session_id, status="confirmed")
        for unit in ("milliseconds", "seconds"):
            patch = {
                "entity_id": "ent_abc", "entity_name": "video",
                "column_name": "elapsed_time", "field": "fields.elapsed_time.unit",
                "current_value": None, "suggested_value": unit,
                "confidence": 0.7, "source": "heuristic", "metric_name": "elapsed_time",
            }
            self._insert_rec_with_patch(session_id, claim_id, patch)
        ctx = build_reflection_context(self.store, session_id)
        self.assertEqual(len(ctx["entity_update_suggestions"]), 2)


if __name__ == "__main__":
    unittest.main()
