"""Tests for multi-claim recommendation aggregation (roadmap 1.2).

Validates that DefaultRecommendationPolicy uses claim relations to decide
whether same-slice claims should be aggregated.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.evidence_engine.recommendation_policy import (
    DefaultRecommendationPolicy,
    attach_causal_chain_metadata,
)


def _make_observation(
    obs_id: str, metric: str, delta_pct: float, **slice_kv: Any
) -> dict[str, Any]:
    return {
        "observation_id": obs_id,
        "type": "metric_observation",
        "subject": {"metric": metric, "slice": dict(slice_kv)},
        "payload": {"delta_pct": delta_pct, "current_value": 100},
        "significance": {"sample_size": 100},
        "quality": {"sample_size_ok": True, "freshness_ok": True},
    }


def _make_claim(
    claim_id: str,
    metric: str,
    delta_pct: float = 10.0,
    status: str = "confirmed",
    confidence: float = 0.7,
    supporting_obs: list[str] | None = None,
    inference_level: str = "L0",
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
        "confidence_breakdown": {
            "primary_delta_pct": delta_pct,
            "primary_direction": "up" if delta_pct > 0 else "down",
            "current_value": 100,
        },
        "inference_level": inference_level,
        "inference_justification": [],
    }


def _relation(left: str, right: str, relation_type: str = "correlates_with") -> dict[str, Any]:
    return {
        "from_claim_id": left,
        "to_claim_id": right,
        "relation_type": relation_type,
        "weight": 0.9,
        "match_basis": {},
        "score_components": {},
        "supporting_observation_ids": [],
        "explanation": "test relation",
    }


class TestMultiClaimAggregation(unittest.TestCase):
    """Tests for multi-claim recommendation aggregation."""

    def setUp(self) -> None:
        self.policy = DefaultRecommendationPolicy()

    def test_three_claims_same_slice_with_relations_produce_one_aggregated_rec(self) -> None:
        """3 confirmed claims with same slice + relations → 1 aggregated recommendation."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
            _make_observation("obs_3", "cpu_time", 15.0, user="sys_titan"),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                supporting_obs=["obs_2"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c3",
                "cpu_time",
                15.0,
                supporting_obs=["obs_3"],
                inference_level="L1",
                user="sys_titan",
            ),
        ]
        relations = [_relation("c1", "c2"), _relation("c2", "c3")]
        recs = self.policy.derive(obs, claims, [], relations)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertIsNotNone(rec.get("supporting_claims"))
        self.assertGreaterEqual(len(rec["supporting_claims"]), 3)
        self.assertEqual(rec["template_id"], "multi_claim_correlated_action_v1")
        self.assertIn("query_count", rec["action_text"])
        self.assertIn("queued_time", rec["action_text"])
        self.assertIn("cpu_time", rec["action_text"])

    def test_same_slice_without_relations_falls_back_to_single_claim_recs(self) -> None:
        """Same-slice claims without relations should not be force-aggregated."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
        ]
        claims = [
            _make_claim("c1", "query_count", 33.5, supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", 120.3, supporting_obs=["obs_2"], user="sys_titan"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 2)
        for rec in recs:
            self.assertIsNone(rec.get("supporting_claims"))
            self.assertEqual(rec["template_id"], "single_claim_action_v1")

    def test_different_slices_produce_separate_recs(self) -> None:
        """Claims with different slices get independent recommendations."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
            _make_observation("obs_3", "query_count", -10.0, user="sys_oneservice"),
            _make_observation("obs_4", "cpu_time", -5.0, user="sys_oneservice"),
        ]
        claims = [
            _make_claim("c1", "query_count", 33.5, supporting_obs=["obs_1"], user="sys_titan"),
            _make_claim("c2", "queued_time", 120.3, supporting_obs=["obs_2"], user="sys_titan"),
            _make_claim(
                "c3", "query_count", -10.0, supporting_obs=["obs_3"], user="sys_oneservice"
            ),
            _make_claim("c4", "cpu_time", -5.0, supporting_obs=["obs_4"], user="sys_oneservice"),
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 4)

    def test_single_claim_group_falls_back_to_single_rec(self) -> None:
        """A group with only 1 claim produces a single-claim rec (no supporting_claims)."""
        obs = [_make_observation("obs_1", "query_count", 33.5, user="sys_titan")]
        claims = [
            _make_claim("c1", "query_count", 33.5, supporting_obs=["obs_1"], user="sys_titan")
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(len(recs), 1)
        self.assertIsNone(recs[0].get("supporting_claims"))
        self.assertEqual(recs[0]["template_id"], "single_claim_action_v1")

    def test_only_confirmed_claims_are_aggregated(self) -> None:
        """Insufficient claims should not be included in aggregation groups."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
            _make_observation("obs_3", "cpu_time", 5.0, user="sys_titan"),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                status="confirmed",
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                status="confirmed",
                supporting_obs=["obs_2"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c3",
                "cpu_time",
                5.0,
                status="insufficient",
                supporting_obs=["obs_3"],
                user="sys_titan",
            ),
        ]
        recs = self.policy.derive(obs, claims, [], [_relation("c1", "c2")])
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertIsNotNone(rec.get("supporting_claims"))
        self.assertEqual(len(rec["supporting_claims"]), 2)
        self.assertNotIn("c3", rec["supporting_claims"])

    def test_priority_takes_highest_urgency(self) -> None:
        """Priority should be the most urgent across the group (all confirmed → P1)."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                status="confirmed",
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                status="confirmed",
                supporting_obs=["obs_2"],
                inference_level="L1",
                user="sys_titan",
            ),
        ]
        recs = self.policy.derive(obs, claims, [], [_relation("c1", "c2")])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["priority"], "P1")

    def test_primary_claim_is_highest_confidence(self) -> None:
        """claim_id on the aggregated rec should be the highest-confidence claim."""
        obs = [
            _make_observation("obs_1", "query_count", 33.5, user="sys_titan"),
            _make_observation("obs_2", "queued_time", 120.3, user="sys_titan"),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                confidence=0.6,
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                confidence=0.9,
                supporting_obs=["obs_2"],
                inference_level="L1",
                user="sys_titan",
            ),
        ]
        recs = self.policy.derive(obs, claims, [], [_relation("c1", "c2")])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["claim_id"], "c2")

    def test_no_recommendation_when_no_confirmed_claims(self) -> None:
        """Without confirmed claims, recommendation derivation should return no actions."""
        obs = [_make_observation("obs_1", "query_count", 33.5)]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                status="tentative",
                confidence=0.3,
                supporting_obs=["obs_1"],
            )
        ]
        recs = self.policy.derive(obs, claims, [])
        self.assertEqual(recs, [])


class TestRecommendationPersistence(unittest.TestCase):
    """Integration tests for recommendation persistence columns."""

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
                    rec_id, session_id, claim_id, action_text, template_id, priority,
                    expected_impact, risk, validation_metric_json, supporting_claims_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "rec_test123456",
                    session_id,
                    "claim_aaa",
                    "test action",
                    "multi_claim_correlated_action_v1",
                    "P1",
                    "test impact",
                    "test risk",
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
                    "rec_test789012",
                    session_id,
                    "claim_xxx",
                    "test action",
                    "P1",
                    "test impact",
                    "test risk",
                    json.dumps({"primary_metric": "m1"}),
                ],
            )

            row = meta.query_one(
                "SELECT supporting_claims_json FROM recommendations WHERE rec_id = ?",
                ["rec_test789012"],
            )
            self.assertIsNotNone(row)
            self.assertIsNone(row["supporting_claims_json"])


class TestCausalChainNarrative(unittest.TestCase):
    def _recommendation(
        self, primary_claim_id: str, supporting_claims: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "rec_id": "rec_1",
            "type": "action_required",
            "claim_id": primary_claim_id,
            "supporting_claims": supporting_claims,
            "template_id": "multi_claim_correlated_action_v1",
            "action_text": "test",
            "priority": "P1",
            "expected_impact": "test",
            "risk": "low",
            "validation_metric": {},
            "causal_basis": {
                "inference_level": "L2",
                "strongest_evidence_summary": "test",
                "unresolved_confounders": [],
                "resolved_confounders": [],
                "suggested_validation": "test",
            },
        }

    def test_causal_chain_generated_from_local_subgraph(self) -> None:
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                supporting_obs=["obs_2"],
                inference_level="L2",
                user="sys_titan",
            ),
            _make_claim(
                "c3",
                "cpu_time",
                15.0,
                supporting_obs=["obs_3"],
                inference_level="L1",
                user="sys_titan",
            ),
        ]
        rec = self._recommendation("c2", ["c1", "c2", "c3"])
        relations = [_relation("c1", "c3"), _relation("c3", "c2")]
        promoted_edges = [
            {
                "from_node_id": "c1",
                "from_node_type": "claim",
                "to_node_id": "c2",
                "to_node_type": "claim",
                "edge_type": "temporally_precedes",
                "weight": 0.8,
                "explanation": "temporal precedence",
            }
        ]

        result = attach_causal_chain_metadata([rec], claims, relations, promoted_edges)
        causal_basis = result[0]["causal_basis"]
        self.assertEqual(causal_basis["causal_path_claim_ids"], ["c1", "c2"])
        self.assertEqual(causal_basis["causal_chain"], "query_count +33.5% -> queued_time +120.3%")

    def test_causal_chain_requires_directional_edge(self) -> None:
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                supporting_obs=["obs_2"],
                inference_level="L1",
                user="sys_titan",
            ),
        ]
        rec = self._recommendation("c2", ["c1", "c2"])
        relations = [_relation("c1", "c2")]

        result = attach_causal_chain_metadata([rec], claims, relations, [])
        causal_basis = result[0]["causal_basis"]
        self.assertIsNone(causal_basis["causal_chain"])
        self.assertEqual(causal_basis["causal_path_claim_ids"], [])

    def test_causal_chain_ignores_claims_outside_recommendation_scope(self) -> None:
        claims = [
            _make_claim(
                "c1",
                "query_count",
                33.5,
                supporting_obs=["obs_1"],
                inference_level="L1",
                user="sys_titan",
            ),
            _make_claim(
                "c2",
                "queued_time",
                120.3,
                supporting_obs=["obs_2"],
                inference_level="L2",
                user="sys_titan",
            ),
            _make_claim(
                "c3",
                "resource_pressure",
                88.0,
                supporting_obs=["obs_3"],
                inference_level="L3",
                user="sys_other",
            ),
        ]
        rec = self._recommendation("c2", ["c1", "c2"])
        promoted_edges = [
            {
                "from_node_id": "c3",
                "from_node_type": "claim",
                "to_node_id": "c2",
                "to_node_type": "claim",
                "edge_type": "mechanistically_explains",
                "weight": 0.9,
                "explanation": "external mechanism",
            },
            {
                "from_node_id": "c1",
                "from_node_type": "claim",
                "to_node_id": "c2",
                "to_node_type": "claim",
                "edge_type": "temporally_precedes",
                "weight": 0.8,
                "explanation": "local temporal precedence",
            },
        ]

        result = attach_causal_chain_metadata(
            [rec], claims, [_relation("c1", "c2")], promoted_edges
        )
        causal_basis = result[0]["causal_basis"]
        self.assertEqual(causal_basis["causal_path_claim_ids"], ["c1", "c2"])
        self.assertNotIn("resource_pressure", causal_basis["causal_chain"])

    def test_template_id_persisted_and_read_back(self) -> None:
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        with tempfile.TemporaryDirectory() as td:
            meta = SQLiteMetadataStore(Path(td) / "test.meta.sqlite")
            meta.initialize()

            session_id = "sess_test345678"
            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [session_id, "test", "{}", "{}", "{}", "active"],
            )
            meta.execute(
                """
                INSERT INTO recommendations (
                    rec_id, session_id, claim_id, action_text, template_id, priority,
                    expected_impact, risk, validation_metric_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "rec_test345678",
                    session_id,
                    "claim_aaa",
                    "test action",
                    "single_claim_action_v1",
                    "P1",
                    "impact",
                    "risk",
                    json.dumps({"primary_metric": "m1"}),
                ],
            )
            row = meta.query_one(
                "SELECT template_id FROM recommendations WHERE rec_id = ?",
                ["rec_test345678"],
            )
            self.assertIsNotNone(row)
            self.assertEqual(row["template_id"], "single_claim_action_v1")


if __name__ == "__main__":
    unittest.main()
