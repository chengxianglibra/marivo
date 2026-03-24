"""Incremental synthesis end-to-end integration tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path

_STEP_PARAMS = {
    "table_name": "analytics.watch_events",
    "select": ["platform", "count(*) as cnt"],
    "group_by": ["platform"],
}

_VALID_SUGGESTED_ACTIONS = {
    "continue_exploring",
    "resolve_contradiction",
    "synthesize",
    "stop",
}


class IncrementalSynthesisE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_session(self, goal: str = "Integration test session") -> str:
        resp = self.client.post(
            "/sessions",
            json={"goal": goal, "budget": {"max_steps": 10}},
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["session_id"]

    def _run_aggregate_step(self, session_id: str) -> dict:
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json=_STEP_PARAMS,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_e2e_incremental_synthesis_flow(self) -> None:
        """Core flow: 3 aggregate steps + synthesize, verify live_claims/readiness accumulate."""
        session_id = self._create_session("E2E incremental synthesis")

        # Step 1: verify readiness + live_claims present
        r1 = self._run_aggregate_step(session_id)
        self.assertIn("live_claims", r1, "Step 1 response must have live_claims")
        self.assertIn("readiness", r1, "Step 1 response must have readiness")
        self.assertIsInstance(r1["live_claims"], list)
        self.assertIsInstance(r1["readiness"], dict)
        self.assertEqual(len(r1["readiness"]), 6)

        budget_after_1 = r1["readiness"]["budget_remaining"]

        # Step 2: budget_remaining should decrease
        r2 = self._run_aggregate_step(session_id)
        self.assertIn("live_claims", r2)
        self.assertIn("readiness", r2)
        budget_after_2 = r2["readiness"]["budget_remaining"]
        self.assertLessEqual(budget_after_2, budget_after_1)

        # Step 3: suggested_action is valid
        r3 = self._run_aggregate_step(session_id)
        self.assertIn(r3["readiness"]["suggested_action"], _VALID_SUGGESTED_ACTIONS)

        # synthesize_findings must NOT return readiness or live_claims
        r_synth = self.client.post(
            f"/sessions/{session_id}/steps/synthesize_findings"
        )
        self.assertEqual(r_synth.status_code, 200)
        synth_body = r_synth.json()
        self.assertNotIn("readiness", synth_body)
        self.assertNotIn("live_claims", synth_body)

        # Evidence endpoint: claims have status + inference_level == L0
        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        for claim in evidence.get("claims", []):
            self.assertIn(claim["status"], {"confirmed", "insufficient"})
            self.assertEqual(claim["inference_level"], "L0")

    def test_inference_level_defaults_to_L0(self) -> None:
        """All claims on a fresh session must have inference_level == 'L0'."""
        session_id = self._create_session("Inference level check")
        self._run_aggregate_step(session_id)
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        for claim in evidence.get("claims", []):
            self.assertEqual(claim["inference_level"], "L0")
            self.assertEqual(claim.get("inference_justification", []), [])

    def test_live_claims_returned_on_primitive_steps(self) -> None:
        """live_claims shape: each element has required fields; status == 'tentative' pre-synthesis."""
        session_id = self._create_session("Live claims shape")
        result = self._run_aggregate_step(session_id)

        live_claims = result["live_claims"]
        self.assertIsInstance(live_claims, list)
        for claim in live_claims:
            self.assertIn("claim_id", claim)
            self.assertIn("text", claim)
            self.assertIn("confidence", claim)
            self.assertIn("status", claim)
            self.assertIn("inference_level", claim)
            # Pre-synthesis: all claims should be tentative
            self.assertEqual(claim["status"], "tentative")

    def test_readiness_dimensions_in_range(self) -> None:
        """All five float dimensions must be in [0.0, 1.0]."""
        session_id = self._create_session("Readiness range check")
        self._run_aggregate_step(session_id)
        result = self._run_aggregate_step(session_id)

        readiness = result["readiness"]
        float_keys = [
            "goal_coverage",
            "evidence_sufficiency",
            "contradiction_resolution",
            "budget_remaining",
            "diminishing_returns",
        ]
        for key in float_keys:
            self.assertIn(key, readiness)
            val = readiness[key]
            self.assertGreaterEqual(val, 0.0, f"{key} below 0.0")
            self.assertLessEqual(val, 1.0, f"{key} above 1.0")

        self.assertIn(readiness["suggested_action"], _VALID_SUGGESTED_ACTIONS)

    def test_synthesize_finds_no_live_claims_in_response(self) -> None:
        """synthesize_findings response must NOT contain readiness or live_claims."""
        session_id = self._create_session("Synthesize exclusion boundary")
        self._run_aggregate_step(session_id)

        resp = self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("readiness", body)
        self.assertNotIn("live_claims", body)
