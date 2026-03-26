"""Tests for M-04 Readiness Signal (app/evidence_engine/readiness.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from app.evidence_engine.readiness import compute_readiness, load_live_claims
from app.storage.sqlite_metadata import SQLiteMetadataStore


def _claim(
    session_id: str,
    *,
    confidence: float = 0.8,
    status: str = "tentative",
    supporting: list[str] | None = None,
    contradicting: list[str] | None = None,
) -> dict:
    return {
        "claim_id": f"claim_{uuid4().hex[:12]}",
        "session_id": session_id,
        "claim_type": "trend",
        "text": "test claim",
        "scope_json": "{}",
        "confidence": confidence,
        "status": status,
        "supporting_observation_ids_json": json.dumps(supporting or []),
        "contradicting_observation_ids_json": json.dumps(contradicting or []),
        "confidence_breakdown_json": "{}",
        "inference_level": "L0",
        "inference_justification_json": "[]",
    }


def _step(session_id: str, step_type: str = "metric_query") -> dict:
    return {
        "step_id": f"step_{uuid4().hex[:12]}",
        "session_id": session_id,
        "step_type": step_type,
        "status": "completed",
        "summary": "test step",
        "result_json": "{}",
    }


def _insert_claim(store: SQLiteMetadataStore, c: dict) -> None:
    store.execute(
        """
        INSERT INTO claims (
            claim_id, session_id, claim_type, text, scope_json, confidence, status,
            supporting_observation_ids_json, contradicting_observation_ids_json,
            confidence_breakdown_json, inference_level, inference_justification_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            c["claim_id"], c["session_id"], c["claim_type"], c["text"],
            c["scope_json"], c["confidence"], c["status"],
            c["supporting_observation_ids_json"], c["contradicting_observation_ids_json"],
            c["confidence_breakdown_json"], c["inference_level"], c["inference_justification_json"],
        ],
    )


def _insert_step(store: SQLiteMetadataStore, s: dict) -> None:
    store.execute(
        """
        INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [s["step_id"], s["session_id"], s["step_type"], s["status"], s["summary"], s["result_json"]],
    )


class ReadinessEmptySessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()
        self.session_id = "sess_test001"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_session_returns_defaults(self) -> None:
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["goal_coverage"], 0.0)
        self.assertEqual(result["evidence_sufficiency"], 0.0)
        self.assertEqual(result["contradiction_resolution"], 1.0)
        self.assertEqual(result["budget_remaining"], 1.0)
        self.assertEqual(result["diminishing_returns"], 1.0)

    def test_empty_session_suggests_continue_exploring(self) -> None:
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["suggested_action"], "continue_exploring")

    def test_load_live_claims_empty(self) -> None:
        claims = load_live_claims(self.store, self.session_id)
        self.assertEqual(claims, [])


class ReadinessSingleStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()
        self.session_id = "sess_test002"
        _insert_step(self.store, _step(self.session_id))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_one_claim_low_coverage(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, confidence=0.8, supporting=["o1", "o2"]))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertAlmostEqual(result["goal_coverage"], 1 / 5)

    def test_insufficient_claim_not_counted_in_goal_coverage(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, confidence=0.4, status="insufficient"))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["goal_coverage"], 0.0)

    def test_confirmed_claim_counted(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, confidence=0.9, status="confirmed"))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertAlmostEqual(result["goal_coverage"], 1 / 5)


class ReadinessMultipleClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()
        self.session_id = "sess_test003"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_five_claims_full_coverage(self) -> None:
        for _ in range(5):
            _insert_claim(self.store, _claim(self.session_id, confidence=0.8))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["goal_coverage"], 1.0)

    def test_coverage_clips_at_one(self) -> None:
        for _ in range(10):
            _insert_claim(self.store, _claim(self.session_id, confidence=0.9))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["goal_coverage"], 1.0)

    def test_evidence_sufficiency_three_supporting(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, supporting=["o1", "o2", "o3"]))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertAlmostEqual(result["evidence_sufficiency"], 1.0)


class ReadinessContradictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()
        self.session_id = "sess_test004"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_contradiction_resolution_full_when_no_contradictions(self) -> None:
        _insert_claim(self.store, _claim(self.session_id))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["contradiction_resolution"], 1.0)

    def test_contradiction_resolution_partial(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, contradicting=["o_bad"]))
        _insert_claim(self.store, _claim(self.session_id))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertAlmostEqual(result["contradiction_resolution"], 0.5)

    def test_contradiction_triggers_resolve_action(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, contradicting=["o_bad"]))
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["suggested_action"], "resolve_contradiction")


class ReadinessBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()
        self.session_id = "sess_test005"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_budget_remaining_no_budget_is_one(self) -> None:
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["budget_remaining"], 1.0)

    def test_budget_remaining_decreases_with_steps(self) -> None:
        for _ in range(5):
            _insert_step(self.store, _step(self.session_id))
        result = compute_readiness(self.store, self.session_id, {"max_steps": 10})
        self.assertAlmostEqual(result["budget_remaining"], 0.5)

    def test_budget_exhausted_triggers_stop(self) -> None:
        for _ in range(9):
            _insert_step(self.store, _step(self.session_id))
        result = compute_readiness(self.store, self.session_id, {"max_steps": 10})
        self.assertEqual(result["suggested_action"], "stop")

    def test_synthesize_findings_not_counted_in_step_count(self) -> None:
        _insert_step(self.store, _step(self.session_id, step_type="synthesize_findings"))
        result = compute_readiness(self.store, self.session_id, {"max_steps": 10})
        self.assertAlmostEqual(result["budget_remaining"], 1.0)


class ReadinessSuggestedActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()
        self.session_id = "sess_test006"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_synthesize_when_high_coverage_and_sufficiency(self) -> None:
        # 5 claims with 3 supporting obs each
        for _ in range(5):
            _insert_claim(
                self.store,
                _claim(self.session_id, confidence=0.9, supporting=["o1", "o2", "o3"]),
            )
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["suggested_action"], "synthesize")

    def test_continue_exploring_default(self) -> None:
        result = compute_readiness(self.store, self.session_id, {})
        self.assertEqual(result["suggested_action"], "continue_exploring")

    def test_all_dimensions_present_in_result(self) -> None:
        result = compute_readiness(self.store, self.session_id, {})
        for key in (
            "goal_coverage", "evidence_sufficiency", "contradiction_resolution",
            "budget_remaining", "diminishing_returns", "suggested_action",
        ):
            self.assertIn(key, result)

    def test_all_float_dimensions_in_range(self) -> None:
        _insert_claim(self.store, _claim(self.session_id, confidence=0.8, supporting=["o1"]))
        result = compute_readiness(self.store, self.session_id, {})
        for key in ("goal_coverage", "evidence_sufficiency", "contradiction_resolution",
                    "budget_remaining", "diminishing_returns"):
            self.assertGreaterEqual(result[key], 0.0)
            self.assertLessEqual(result[key], 1.0)


if __name__ == "__main__":
    unittest.main()
