"""Tests for multi-claim recommendation aggregation (roadmap 1.2).

Validates that DefaultRecommendationPolicy groups confirmed claims by slice
and generates aggregated recommendations with supporting_claims.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.evidence_engine.recommendation_policy import DefaultRecommendationPolicy


def _make_observation(obs_id: str, metric: str, delta_pct: float, **slice_kv: Any) -> dict[str, Any]:
    return {
        "observation_id": obs_id,
        "type": "metric_change",
        "subject": {"metric": metric, "slice": dict(slice_kv)},
        "payload": {"delta_pct": delta_pct, "current_value": 100},
        "significance": {"sample_size": 100},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


def _make_claim(
    claim_id: str,
    metric: str,
    status: str = "confirmed",
    confidence: float = 0.7,
    supporting_obs: list[str] | None = None,
    **slice_kv: Any,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "type": "root_cause_candidate",
        "text": f"{metric} changed for test",
        "scope": {"metric": metric, "slice": dict(slice_kv)},
        "confidence": confidence,
        "status": status,
        "supporting_observations": supporting_obs or [],
        "contradicting_observations": [],
        "confidence_breakdown": {},
        "inference_level": "L0",
        "inference_justification": [],
    }


class TestMultiClaimAggregation(unittest.TestCase):
    """Tests for multi-claim recommendation aggregation."""

    def setUp(self) -> None:
        self.policy = DefaultRecommendationPolicy()

    def test_three_claims_same_slice_produce_one_aggregated_rec(self) -> None:
        """3 confirmed claims sharing user=sys_titan → 1 rec with supporting_claims >= 3."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
            _make_observation("obs_3", "cpu_time", 15.0, user="sys_titan"),
        ]
        claims = [
            _make_claim("c1", "query_count", supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", supporting_obs=["obs_2"], user="sys_titan"),
            _make_claim("c3", "cpu_time", supporting_obs=["obs_3"], user="sys_titan"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertIsNotNone(rec.get("supporting_claims"))
        self.assertGreaterEqual(len(rec["supporting_claims"]), 3)
        self.assertIn("query_count", rec["action_text"])
        self.assertIn("queued_time", rec["action_text"])
        self.assertIn("cpu_time", rec["action_text"])

    def test_different_slices_produce_separate_recs(self) -> None:
        """Claims with different slices get independent recommendations."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
            _make_observation("obs_3", "query_count", -10.0, user="sys_oneservice"),
            _make_observation("obs_4", "cpu_time", -5.0, user="sys_oneservice"),
        ]
        claims = [
            _make_claim("c1", "query_count", supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", supporting_obs=["obs_2"], user="sys_titan"),
            _make_claim("c3", "query_count", supporting_obs=["obs_3"], user="sys_oneservice"),
            _make_claim("c4", "cpu_time", supporting_obs=["obs_4"], user="sys_oneservice"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 2)
        # Each rec has supporting_claims
        for rec in recs:
            self.assertIsNotNone(rec.get("supporting_claims"))
            self.assertEqual(len(rec["supporting_claims"]), 2)

    def test_single_claim_group_falls_back_to_single_rec(self) -> None:
        """A group with only 1 claim produces a single-claim rec (no supporting_claims)."""
        obs = [_make_observation("obs_1", "query_count", 33.5, user="sys_titan")]
        claims = [_make_claim("c1", "query_count", supporting_obs=["obs_1"], user="sys_titan")]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        # Single claim rec has supporting_claims = None
        self.assertIsNone(recs[0].get("supporting_claims"))

    def test_only_confirmed_claims_are_aggregated(self) -> None:
        """Insufficient claims should not be included in aggregation groups."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
            _make_observation("obs_3", "cpu_time", 5.0, user="sys_titan"),
        ]
        claims = [
            _make_claim("c1", "query_count", status="confirmed", supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", status="confirmed", supporting_obs=["obs_2"], user="sys_titan"),
            _make_claim("c3", "cpu_time", status="insufficient", supporting_obs=["obs_3"], user="sys_titan"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        # Only 2 confirmed claims are aggregated
        self.assertIsNotNone(rec.get("supporting_claims"))
        self.assertEqual(len(rec["supporting_claims"]), 2)
        self.assertNotIn("c3", rec["supporting_claims"])

    def test_action_text_groups_by_direction(self) -> None:
        """action_text should separate increased and declined metrics."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "error_rate", -15.0, user="sys_titan"),
        ]
        claims = [
            _make_claim("c1", "query_count", supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "error_rate", supporting_obs=["obs_2"], user="sys_titan"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        text = recs[0]["action_text"]
        self.assertIn("increased", text)
        self.assertIn("declined", text)

    def test_priority_takes_highest_urgency(self) -> None:
        """Priority should be the most urgent across the group (all confirmed → P1)."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
        ]
        claims = [
            _make_claim("c1", "query_count", status="confirmed", supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", status="confirmed", supporting_obs=["obs_2"], user="sys_titan"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["priority"], "P1")

    def test_primary_claim_is_highest_confidence(self) -> None:
        """claim_id on the aggregated rec should be the highest-confidence claim."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
        ]
        claims = [
            _make_claim("c1", "query_count", confidence=0.6, supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", confidence=0.9, supporting_obs=["obs_2"], user="sys_titan"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["claim_id"], "c2")

    def test_existing_recommendations_returned_unchanged(self) -> None:
        """If recommendations already exist, they should be returned as-is."""
        existing = [{"rec_id": "rec_existing", "claim_id": "c1", "action_text": "existing"}]
        recs = self.policy.derive([], [], existing)
        self.assertEqual(recs, existing)

    def test_fallback_when_no_confirmed_claims(self) -> None:
        """When no confirmed claims exist, fallback to highest-confidence claim."""
        obs = [_make_observation("obs_1", "query_count", 33.5)]
        claims = [_make_claim("c1", "query_count", status="tentative", confidence=0.3, supporting_obs=["obs_1"])]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["claim_id"], "c1")


class TestSupportingClaimsPersistence(unittest.TestCase):
    """Integration test: supporting_claims_json survives write → read round-trip."""

    def test_supporting_claims_persisted_and_read_back(self) -> None:
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        with tempfile.TemporaryDirectory() as td:
            meta = SQLiteMetadataStore(Path(td) / "test.meta.sqlite")
            meta.initialize()

            # Create a minimal session
            session_id = "sess_test123456"
            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [session_id, "test", "{}", "{}", "{}", "active"],
            )

            # Insert a recommendation with supporting_claims
            supporting = ["claim_aaa", "claim_bbb", "claim_ccc"]
            meta.execute(
                """
                INSERT INTO recommendations (
                    rec_id, session_id, claim_id, action_text, priority,
                    expected_impact, risk, validation_metric_json, supporting_claims_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "rec_test123456", session_id, "claim_aaa",
                    "test action", "P1", "test impact", "test risk",
                    json.dumps({"primary_metric": "m1"}),
                    json.dumps(supporting),
                ],
            )

            # Read back
            row = meta.query_one(
                "SELECT supporting_claims_json FROM recommendations WHERE rec_id = ?",
                ["rec_test123456"],
            )
            self.assertIsNotNone(row)
            read_back = json.loads(row["supporting_claims_json"])
            self.assertEqual(read_back, supporting)

    def test_null_supporting_claims_read_back_as_none(self) -> None:
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        with tempfile.TemporaryDirectory() as td:
            meta = SQLiteMetadataStore(Path(td) / "test.meta.sqlite")
            meta.initialize()

            session_id = "sess_test789012"
            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [session_id, "test", "{}", "{}", "{}", "active"],
            )

            # Insert without supporting_claims (NULL)
            meta.execute(
                """
                INSERT INTO recommendations (
                    rec_id, session_id, claim_id, action_text, priority,
                    expected_impact, risk, validation_metric_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "rec_test789012", session_id, "claim_xxx",
                    "test action", "P1", "test impact", "test risk",
                    json.dumps({"primary_metric": "m1"}),
                ],
            )

            row = meta.query_one(
                "SELECT supporting_claims_json FROM recommendations WHERE rec_id = ?",
                ["rec_test789012"],
            )
            self.assertIsNotNone(row)
            self.assertIsNone(row["supporting_claims_json"])


if __name__ == "__main__":
    unittest.main()
