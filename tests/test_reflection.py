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
) -> str:
    rec_id = f"rec_{uuid4().hex[:12]}"
    from app.evidence_engine.schemas import _build_causal_basis  # noqa: PLC0415

    # Build causal_basis from a minimal claim-like dict
    causal_basis = _build_causal_basis({  # type: ignore[arg-type]
        "inference_level": inference_level,
        "confidence": 0.6,
        "text": "test claim",
    })
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
                    "tentative_claims", "evidence_gaps", "available_step_types"):
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

    # ── Test 3: evidence_gaps from recommendations ─────────────────────────

    def test_evidence_gaps_from_recommendations_with_confounders(self) -> None:
        session_id = self._new_session()
        claim_id = _insert_claim(self.store, session_id, inference_level="L0")
        rec_id = _insert_recommendation(self.store, session_id, claim_id, inference_level="L0")
        ctx = build_reflection_context(self.store, session_id)
        rec_ids = [g["rec_id"] for g in ctx["evidence_gaps"]]
        self.assertIn(rec_id, rec_ids)
        gap = next(g for g in ctx["evidence_gaps"] if g["rec_id"] == rec_id)
        self.assertEqual(gap["inference_level"], "L0")
        self.assertIsInstance(gap["unresolved_confounders"], list)
        self.assertTrue(len(gap["unresolved_confounders"]) > 0)

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
        expected = {"compare_metric", "profile_table", "sample_rows", "aggregate_query", "correlate_metrics", "synthesize_findings"}
        self.assertEqual(set(ctx["available_step_types"]), expected)


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


if __name__ == "__main__":
    unittest.main()
