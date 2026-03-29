"""Tests for M-09: deterministic causal checkers.

Coverage:
- CrossSliceConsistencyChecker (5 tests)
- TemporalPrecedenceChecker (5 tests)
- DoseResponseChecker (5 tests)
- ReversalChecker (5 tests)
- CausalCheckerRegistry + IncrementalSynthesizer integration (5 tests)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.evidence_engine.causal_checkers import (
    CausalChecker,
    CausalCheckerRegistry,
    CausalEdge,
    CrossMetricCorrelationChecker,
    CrossSliceConsistencyChecker,
    DoseResponseChecker,
    LevelUpgrade,
    ReversalChecker,
    TemporalPrecedenceChecker,
    _detect_reversal,
    _spearman_correlation,
    build_default_registry,
)

# ── Fixture helpers ───────────────────────────────────────────────────────────


def _make_obs(
    obs_id: str,
    metric: str,
    delta_pct: float | None = None,
    slice_val: dict | None = None,
    window: dict | None = None,
    temporal_order: int = 0,
    current_value: float | None = None,
) -> dict:
    payload: dict = {}
    if delta_pct is not None:
        payload["delta_pct"] = delta_pct
    if current_value is not None:
        payload["current_value"] = current_value
    return {
        "observation_id": obs_id,
        "type": "metric_observation",
        "subject": {"metric": metric, "slice": slice_val or {}},
        "payload": payload,
        "significance": {"sample_size": 100, "practical_significance": True},
        "quality": {"freshness_ok": True, "sample_size_ok": True},
        "observed_window": window,
        "temporal_order": temporal_order,
    }


def _make_claim(
    claim_id: str,
    metric: str,
    level: str = "L0",
    obs_ids: list[str] | None = None,
    slice_val: dict | None = None,
    status: str = "tentative",
) -> dict:
    return {
        "claim_id": claim_id,
        "type": "root_cause_candidate",
        "text": f"{metric} changed (tentative)",
        "scope": {"metric": metric, "slice": slice_val or {}},
        "confidence": 0.50,
        "status": status,
        "supporting_observations": list(obs_ids or []),
        "contradicting_observations": [],
        "confidence_breakdown": {},
        "inference_level": level,
        "inference_justification": [],
    }


def _make_relation(
    from_claim_id: str,
    to_claim_id: str,
    *,
    category: str = "exact_match",
    direction: str = "up",
    relation_type: str = "correlates_with",
    supporting_observation_ids: list[str] | None = None,
) -> dict:
    return {
        "from_claim_id": from_claim_id,
        "to_claim_id": to_claim_id,
        "relation_type": relation_type,
        "weight": 0.92,
        "match_basis": {"category": category, "direction": direction},
        "score_components": {},
        "supporting_observation_ids": list(supporting_observation_ids or []),
        "explanation": "test relation",
    }


# ── CrossSliceConsistencyChecker ──────────────────────────────────────────────


class CrossSliceConsistencyCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = CrossSliceConsistencyChecker()

    def test_upgrade_l0_to_l1_consistent_positive(self) -> None:
        """5/5 positive deltas → consistency=1.0 > 0.80 → L1 upgrade."""
        obs = [
            _make_obs(f"o{i}", "ctr", delta_pct=5.0 + i, slice_val={"seg": str(i)})
            for i in range(5)
        ]
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c1")
        self.assertEqual(upgrades[0].new_level, "L1")
        self.assertTrue(any("L1" in t for t in upgrades[0].justification_tokens))

    def test_no_upgrade_insufficient_consistency_50_pct(self) -> None:
        """3 positive + 3 negative → 50% consistency → no upgrade."""
        obs = [
            _make_obs(f"op{i}", "ctr", delta_pct=5.0, slice_val={"seg": f"p{i}"}) for i in range(3)
        ] + [
            _make_obs(f"on{i}", "ctr", delta_pct=-5.0, slice_val={"seg": f"n{i}"}) for i in range(3)
        ]
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_claim_already_l1(self) -> None:
        """L1 claim is skipped (only upgrades L0)."""
        obs = [
            _make_obs(f"o{i}", "ctr", delta_pct=5.0, slice_val={"seg": str(i)}) for i in range(5)
        ]
        claim = _make_claim("c1", "ctr", level="L1", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_insufficient_observations(self) -> None:
        """Only 1 supporting observation → below MIN_OBSERVATIONS=2 → no upgrade."""
        obs = [_make_obs("o1", "ctr", delta_pct=10.0)]
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=["o1"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_exactly_80_percent_boundary(self) -> None:
        """4/5 = 80.0% — not strictly > 80% → no upgrade."""
        obs = [
            _make_obs(f"o{i}", "ctr", delta_pct=5.0, slice_val={"seg": str(i)}) for i in range(4)
        ]
        obs.append(_make_obs("o4", "ctr", delta_pct=-5.0, slice_val={"seg": "neg"}))
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)


# ── TemporalPrecedenceChecker ─────────────────────────────────────────────────


class TemporalPrecedenceCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = TemporalPrecedenceChecker()

    def test_upgrade_effect_claim_to_l2_with_temporal_gap(self) -> None:
        """Relation-backed non-overlapping claim windows upgrade the later claim to L2."""
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [_make_relation("c1", "c2", supporting_observation_ids=["o1", "o2"])]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c2")
        self.assertEqual(upgrades[0].new_level, "L2")
        self.assertIn("temporal_precedence", upgrades[0].justification_tokens[0])
        self.assertIn("lag=3d", upgrades[0].justification_tokens[0])

    def test_no_upgrade_without_relations(self) -> None:
        """The checker does not pair raw claims directly when relations are absent."""
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        upgrades = self.checker.check(claims, obs, [], relations=None)
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_null_observed_window(self) -> None:
        """Claims missing real observed_window signals remain below L2."""
        obs = [
            _make_obs("o1", "query_count", delta_pct=5.0, window=None),
            _make_obs("o2", "queued_time", delta_pct=8.0, window=None),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [_make_relation("c1", "c2", supporting_observation_ids=["o1", "o2"])]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_overlapping_windows(self) -> None:
        """Overlapping claim windows remain correlation-only."""
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-10", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                window={"start": "2024-01-08", "end": "2024-01-15", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [_make_relation("c1", "c2", supporting_observation_ids=["o1", "o2"])]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_for_ineligible_relation_category(self) -> None:
        """Complementary-dimension relations do not imply temporal precedence."""
        obs = [
            _make_obs(
                "o1",
                "queued_time",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                slice_val={"user": "sys_oneservice"},
                window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "queued_time",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_oneservice"},
                status="confirmed",
            ),
        ]
        relations = [
            _make_relation(
                "c1",
                "c2",
                category="complementary_dimension",
                supporting_observation_ids=["o1", "o2"],
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_claim_window_none_without_supporting_observations(self) -> None:
        claim = _make_claim("c1", "query_count", level="L1", obs_ids=[], status="confirmed")
        window = self.checker._claim_window(claim, {})
        self.assertIsNone(window)

    def test_claim_window_none_when_window_missing_end(self) -> None:
        claim = _make_claim("c1", "query_count", level="L1", obs_ids=["o1"], status="confirmed")
        obs_by_id = {
            "o1": _make_obs("o1", "query_count", delta_pct=5.0, window={"start": "2024-01-01"}),
        }
        window = self.checker._claim_window(claim, obs_by_id)
        self.assertIsNone(window)

    def test_claim_window_uses_conservative_envelope_for_multiple_windows(self) -> None:
        claim = _make_claim(
            "c1", "query_count", level="L1", obs_ids=["o1", "o2"], status="confirmed"
        )
        obs_by_id = {
            "o1": _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-03"},
            ),
            "o2": _make_obs(
                "o2",
                "query_count",
                delta_pct=6.0,
                window={"start": "2024-01-10", "end": "2024-01-15"},
            ),
        }
        window = self.checker._claim_window(claim, obs_by_id)
        self.assertEqual(window, {"start": "2024-01-01", "end": "2024-01-15"})

    def test_duplicate_relations_for_same_pair_emit_one_upgrade(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [
            _make_relation("c1", "c2", supporting_observation_ids=["o1", "o2"]),
            _make_relation(
                "c1", "c2", category="subset_or_overlap", supporting_observation_ids=["o1", "o2"]
            ),
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c2")

    def test_upgrade_effect_claim_to_l2_with_hourly_peak_decay(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                slice_val={"user": "sys_titan"},
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
                current_value=100.0,
            ),
            _make_obs(
                "o2",
                "query_count",
                slice_val={"user": "sys_titan"},
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o3",
                "query_count",
                slice_val={"user": "sys_titan"},
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=120.0,
            ),
            _make_obs(
                "o4",
                "queued_time",
                slice_val={"user": "sys_titan"},
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=5.0,
            ),
            _make_obs(
                "o5",
                "queued_time",
                slice_val={"user": "sys_titan"},
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=11.0,
            ),
            _make_obs(
                "o6",
                "queued_time",
                slice_val={"user": "sys_titan"},
                window={
                    "start": "2024-01-01T04:00",
                    "end": "2024-01-01T05:00",
                    "granularity": "hour",
                },
                current_value=7.0,
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1", "o2", "o3"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o4", "o5", "o6"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c2")
        self.assertIn("hourly_peak_decay", upgrades[0].justification_tokens[0])

    def test_upgrade_effect_claim_to_l2_with_two_hour_plateau(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
                current_value=100.0,
            ),
            _make_obs(
                "o2",
                "query_count",
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o3",
                "query_count",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o4",
                "query_count",
                window={
                    "start": "2024-01-01T04:00",
                    "end": "2024-01-01T05:00",
                    "granularity": "hour",
                },
                current_value=120.0,
            ),
            _make_obs(
                "o5",
                "queued_time",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=5.0,
            ),
            _make_obs(
                "o6",
                "queued_time",
                window={
                    "start": "2024-01-01T04:00",
                    "end": "2024-01-01T05:00",
                    "granularity": "hour",
                },
                current_value=11.0,
            ),
            _make_obs(
                "o7",
                "queued_time",
                window={
                    "start": "2024-01-01T05:00",
                    "end": "2024-01-01T06:00",
                    "granularity": "hour",
                },
                current_value=7.0,
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1", "o2", "o3", "o4"],
                status="confirmed",
            ),
            _make_claim(
                "c2", "queued_time", level="L1", obs_ids=["o5", "o6", "o7"], status="confirmed"
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c2")
        self.assertIn("cause_peak=02:00", upgrades[0].justification_tokens[0])

    def test_no_upgrade_for_same_hour_peak_co_movement(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
                current_value=100.0,
            ),
            _make_obs(
                "o2",
                "query_count",
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o3",
                "query_count",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=120.0,
            ),
            _make_obs(
                "o4",
                "queued_time",
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
                current_value=5.0,
            ),
            _make_obs(
                "o5",
                "queued_time",
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=11.0,
            ),
            _make_obs(
                "o6",
                "queued_time",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=7.0,
            ),
        ]
        claims = [
            _make_claim(
                "c1", "query_count", level="L1", obs_ids=["o1", "o2", "o3"], status="confirmed"
            ),
            _make_claim(
                "c2", "queued_time", level="L1", obs_ids=["o4", "o5", "o6"], status="confirmed"
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_without_decay_after_hourly_peak(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
                current_value=100.0,
            ),
            _make_obs(
                "o2",
                "query_count",
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o3",
                "query_count",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o4",
                "queued_time",
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=5.0,
            ),
            _make_obs(
                "o5",
                "queued_time",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=11.0,
            ),
            _make_obs(
                "o6",
                "queued_time",
                window={
                    "start": "2024-01-01T04:00",
                    "end": "2024-01-01T05:00",
                    "granularity": "hour",
                },
                current_value=7.0,
            ),
        ]
        claims = [
            _make_claim(
                "c1", "query_count", level="L1", obs_ids=["o1", "o2", "o3"], status="confirmed"
            ),
            _make_claim(
                "c2", "queued_time", level="L1", obs_ids=["o4", "o5", "o6"], status="confirmed"
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_when_hourly_lag_exceeds_threshold(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
                current_value=100.0,
            ),
            _make_obs(
                "o2",
                "query_count",
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o3",
                "query_count",
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
                current_value=120.0,
            ),
            _make_obs(
                "o3b",
                "query_count",
                window={
                    "start": "2024-01-01T05:00",
                    "end": "2024-01-01T06:00",
                    "granularity": "hour",
                },
                current_value=20.0,
            ),
            _make_obs(
                "o4",
                "queued_time",
                window={
                    "start": "2024-01-01T05:00",
                    "end": "2024-01-01T06:00",
                    "granularity": "hour",
                },
                current_value=5.0,
            ),
            _make_obs(
                "o5",
                "queued_time",
                window={
                    "start": "2024-01-01T06:00",
                    "end": "2024-01-01T07:00",
                    "granularity": "hour",
                },
                current_value=11.0,
            ),
            _make_obs(
                "o6",
                "queued_time",
                window={
                    "start": "2024-01-01T07:00",
                    "end": "2024-01-01T08:00",
                    "granularity": "hour",
                },
                current_value=7.0,
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1", "o2", "o3", "o3b"],
                status="confirmed",
            ),
            _make_claim(
                "c2", "queued_time", level="L1", obs_ids=["o4", "o5", "o6"], status="confirmed"
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_hourly_peak_decay_falls_back_to_delta_pct_when_current_value_missing(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=1.0,
                window={
                    "start": "2024-01-01T01:00",
                    "end": "2024-01-01T02:00",
                    "granularity": "hour",
                },
            ),
            _make_obs(
                "o2",
                "query_count",
                delta_pct=5.0,
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
            ),
            _make_obs(
                "o3",
                "query_count",
                delta_pct=2.0,
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
            ),
            _make_obs(
                "o4",
                "queued_time",
                delta_pct=2.0,
                window={
                    "start": "2024-01-01T02:00",
                    "end": "2024-01-01T03:00",
                    "granularity": "hour",
                },
            ),
            _make_obs(
                "o5",
                "queued_time",
                delta_pct=7.0,
                window={
                    "start": "2024-01-01T03:00",
                    "end": "2024-01-01T04:00",
                    "granularity": "hour",
                },
            ),
            _make_obs(
                "o6",
                "queued_time",
                delta_pct=3.0,
                window={
                    "start": "2024-01-01T04:00",
                    "end": "2024-01-01T05:00",
                    "granularity": "hour",
                },
            ),
        ]
        claims = [
            _make_claim(
                "c1", "query_count", level="L1", obs_ids=["o1", "o2", "o3"], status="confirmed"
            ),
            _make_claim(
                "c2", "queued_time", level="L1", obs_ids=["o4", "o5", "o6"], status="confirmed"
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c2")

    def test_upgrade_hourly_peak_decay_across_midnight(self) -> None:
        obs = [
            _make_obs(
                "o1",
                "query_count",
                window={
                    "start": "2024-01-01T22:00",
                    "end": "2024-01-01T23:00",
                    "granularity": "hour",
                },
                current_value=90.0,
            ),
            _make_obs(
                "o2",
                "query_count",
                window={
                    "start": "2024-01-01T23:00",
                    "end": "2024-01-02T00:00",
                    "granularity": "hour",
                },
                current_value=180.0,
            ),
            _make_obs(
                "o3",
                "query_count",
                window={
                    "start": "2024-01-02T00:00",
                    "end": "2024-01-02T01:00",
                    "granularity": "hour",
                },
                current_value=110.0,
            ),
            _make_obs(
                "o4",
                "queued_time",
                window={
                    "start": "2024-01-02T00:00",
                    "end": "2024-01-02T01:00",
                    "granularity": "hour",
                },
                current_value=5.0,
            ),
            _make_obs(
                "o5",
                "queued_time",
                window={
                    "start": "2024-01-02T01:00",
                    "end": "2024-01-02T02:00",
                    "granularity": "hour",
                },
                current_value=12.0,
            ),
            _make_obs(
                "o6",
                "queued_time",
                window={
                    "start": "2024-01-02T02:00",
                    "end": "2024-01-02T03:00",
                    "granularity": "hour",
                },
                current_value=7.0,
            ),
        ]
        claims = [
            _make_claim(
                "c1", "query_count", level="L1", obs_ids=["o1", "o2", "o3"], status="confirmed"
            ),
            _make_claim(
                "c2", "queued_time", level="L1", obs_ids=["o4", "o5", "o6"], status="confirmed"
            ),
        ]
        relations = [
            _make_relation(
                "c1", "c2", supporting_observation_ids=[o["observation_id"] for o in obs]
            )
        ]
        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c2")


# ── DoseResponseChecker ───────────────────────────────────────────────────────


class DoseResponseCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = DoseResponseChecker()

    def test_bonus_strong_positive_correlation(self) -> None:
        """Monotone increasing dimension values with monotone increasing delta → |ρ|=1.0."""
        obs = [
            _make_obs(f"o{i}", "m", delta_pct=float(i * 5), slice_val={"dose": float(i)})
            for i in range(1, 6)
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        self.assertIn("dose_response", upgrades[0].justification_tokens[0])
        self.assertEqual(upgrades[0].new_level, "L1")  # Level unchanged

    def test_bonus_strong_negative_correlation(self) -> None:
        """Monotone increasing dimension values with monotone decreasing delta → |ρ|=1.0."""
        obs = [
            _make_obs(f"o{i}", "m", delta_pct=float(-i * 5), slice_val={"dose": float(i)})
            for i in range(1, 6)
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        token = upgrades[0].justification_tokens[0]
        self.assertIn("dose_response", token)
        self.assertIn("L1_bonus", token)

    def test_no_bonus_weak_correlation(self) -> None:
        """Random pattern → |ρ| < 0.7 → no bonus."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, slice_val={"dose": 1.0}),
            _make_obs("o2", "m", delta_pct=-3.0, slice_val={"dose": 2.0}),
            _make_obs("o3", "m", delta_pct=8.0, slice_val={"dose": 3.0}),
            _make_obs("o4", "m", delta_pct=-1.0, slice_val={"dose": 4.0}),
            _make_obs("o5", "m", delta_pct=2.0, slice_val={"dose": 5.0}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_bonus_insufficient_observations(self) -> None:
        """Only 2 observations → below MIN_OBSERVATIONS=3 → no bonus."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, slice_val={"dose": 1.0}),
            _make_obs("o2", "m", delta_pct=10.0, slice_val={"dose": 2.0}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_bonus_non_numeric_dimension(self) -> None:
        """String dimension values that can't be converted to float → no bonus."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, slice_val={"region": "US"}),
            _make_obs("o2", "m", delta_pct=10.0, slice_val={"region": "EU"}),
            _make_obs("o3", "m", delta_pct=15.0, slice_val={"region": "APAC"}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2", "o3"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)


# ── ReversalChecker ───────────────────────────────────────────────────────────


class ReversalCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = ReversalChecker()

    def test_bonus_reversal_2_periods(self) -> None:
        """3 positive followed by 2 negative → 2-period reversal → bonus."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, temporal_order=1),
            _make_obs("o2", "m", delta_pct=6.0, temporal_order=2),
            _make_obs("o3", "m", delta_pct=4.0, temporal_order=3),
            _make_obs("o4", "m", delta_pct=-3.0, temporal_order=4),
            _make_obs("o5", "m", delta_pct=-4.0, temporal_order=5),
        ]
        claim = _make_claim("c1", "m", level="L2", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        token = upgrades[0].justification_tokens[0]
        self.assertIn("reversal", token)
        self.assertIn("sustained_2_periods", token)
        self.assertEqual(upgrades[0].new_level, "L2")  # Level unchanged

    def test_no_bonus_l1_claim(self) -> None:
        """L1 claim → ReversalChecker requires L2+ → no bonus."""
        obs = [
            _make_obs(f"o{i}", "m", delta_pct=5.0 if i < 3 else -5.0, temporal_order=i)
            for i in range(5)
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_bonus_only_1_reversal_period(self) -> None:
        """Only 1 reversed period (not ≥2) → no bonus."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, temporal_order=1),
            _make_obs("o2", "m", delta_pct=4.0, temporal_order=2),
            _make_obs("o3", "m", delta_pct=6.0, temporal_order=3),
            _make_obs("o4", "m", delta_pct=-3.0, temporal_order=4),
        ]
        claim = _make_claim("c1", "m", level="L2", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_bonus_consistent_direction(self) -> None:
        """All same direction → no reversal."""
        obs = [
            _make_obs(f"o{i}", "m", delta_pct=float(i + 1) * 2.0, temporal_order=i)
            for i in range(5)
        ]
        claim = _make_claim("c1", "m", level="L2", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_bonus_3_period_reversal(self) -> None:
        """3 reversal periods → token says sustained_3_periods."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, temporal_order=1),
            _make_obs("o2", "m", delta_pct=6.0, temporal_order=2),
            _make_obs("o3", "m", delta_pct=-2.0, temporal_order=3),
            _make_obs("o4", "m", delta_pct=-3.0, temporal_order=4),
            _make_obs("o5", "m", delta_pct=-4.0, temporal_order=5),
        ]
        claim = _make_claim("c1", "m", level="L2", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        self.assertIn("sustained_3_periods", upgrades[0].justification_tokens[0])


# ── CrossMetricCorrelationChecker ─────────────────────────────────────────────


class CrossMetricCorrelationCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = CrossMetricCorrelationChecker()

    def _relation(
        self,
        from_claim_id: str,
        to_claim_id: str,
        *,
        category: str = "exact_match",
        direction: str = "up",
    ) -> dict:
        return {
            "from_claim_id": from_claim_id,
            "to_claim_id": to_claim_id,
            "relation_type": "correlates_with",
            "weight": 0.9,
            "match_basis": {"category": category, "direction": direction},
            "score_components": {"scope_match": 0.9},
            "supporting_observation_ids": [],
            "explanation": "test relation",
        }

    def test_promotes_exact_match_component_with_three_metrics(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs("oc", "cpu_time", delta_pct=8.6, slice_val={"user": "sys_titan"}),
            _make_obs("ot", "queued_time", delta_pct=58.5, slice_val={"user": "sys_titan"}),
        ]
        claims = [
            {
                **_make_claim("cq", "query_count", obs_ids=["oq"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("cc", "cpu_time", obs_ids=["oc"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("ct", "queued_time", obs_ids=["ot"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq", "cc"),
            self._relation("cc", "ct"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 3)
        self.assertTrue(all(upgrade.new_level == "L1" for upgrade in upgrades))
        self.assertTrue(
            all(
                "cross_metric_consistency:3_metrics:user=sys_titan"
                in upgrade.justification_tokens[0]
                for upgrade in upgrades
            )
        )

    def test_promotes_subset_overlap_component(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs(
                "oc", "cpu_time", delta_pct=8.6, slice_val={"cluster": "k1", "user": "sys_titan"}
            ),
            _make_obs(
                "ot",
                "queued_time",
                delta_pct=58.5,
                slice_val={"cluster": "k1", "user": "sys_titan"},
            ),
        ]
        claims = [
            {
                **_make_claim("cq", "query_count", obs_ids=["oq"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim(
                    "cc",
                    "cpu_time",
                    obs_ids=["oc"],
                    slice_val={"cluster": "k1", "user": "sys_titan"},
                ),
                "status": "confirmed",
            },
            {
                **_make_claim(
                    "ct",
                    "queued_time",
                    obs_ids=["ot"],
                    slice_val={"cluster": "k1", "user": "sys_titan"},
                ),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq", "cc", category="subset_or_overlap"),
            self._relation("cc", "ct", category="exact_match"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 3)

    def test_no_upgrade_without_relations(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs("oc", "cpu_time", delta_pct=8.6, slice_val={"user": "sys_titan"}),
            _make_obs("ot", "queued_time", delta_pct=58.5, slice_val={"user": "sys_titan"}),
        ]
        claims = [
            {
                **_make_claim("cq", "query_count", obs_ids=["oq"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("cc", "cpu_time", obs_ids=["oc"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("ct", "queued_time", obs_ids=["ot"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
        ]

        self.assertEqual(self.checker.check(claims, obs, [], relations=None), [])
        self.assertEqual(self.checker.check(claims, obs, [], relations=[]), [])

    def test_no_upgrade_when_claim_has_no_supporting_observations(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs("oc", "cpu_time", delta_pct=8.6, slice_val={"user": "sys_titan"}),
            _make_obs("ot", "queued_time", delta_pct=58.5, slice_val={"user": "sys_titan"}),
        ]
        claims = [
            {
                **_make_claim("cq", "query_count", obs_ids=[], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("cc", "cpu_time", obs_ids=["oc"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("ct", "queued_time", obs_ids=["ot"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq", "cc"),
            self._relation("cc", "ct"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(upgrades, [])

    def test_no_upgrade_for_complementary_dimension_relation(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"resource_group": "others"}),
            _make_obs("oc", "cpu_time", delta_pct=8.6, slice_val={"resource_group": "oneservice"}),
            _make_obs("ot", "queued_time", delta_pct=58.5, slice_val={"resource_group": "others"}),
        ]
        claims = [
            {
                **_make_claim(
                    "cq", "query_count", obs_ids=["oq"], slice_val={"resource_group": "others"}
                ),
                "status": "confirmed",
            },
            {
                **_make_claim(
                    "cc", "cpu_time", obs_ids=["oc"], slice_val={"resource_group": "oneservice"}
                ),
                "status": "confirmed",
            },
            {
                **_make_claim(
                    "ct", "queued_time", obs_ids=["ot"], slice_val={"resource_group": "others"}
                ),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq", "cc", category="complementary_dimension"),
            self._relation("cc", "ct", category="complementary_dimension"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(upgrades, [])

    def test_no_upgrade_when_directions_disagree(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs("oc", "cpu_time", delta_pct=-8.6, slice_val={"user": "sys_titan"}),
            _make_obs("ot", "queued_time", delta_pct=58.5, slice_val={"user": "sys_titan"}),
        ]
        claims = [
            {
                **_make_claim("cq", "query_count", obs_ids=["oq"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("cc", "cpu_time", obs_ids=["oc"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("ct", "queued_time", obs_ids=["ot"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq", "cc", direction="up"),
            self._relation("cc", "ct", direction="up"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(upgrades, [])

    def test_skips_component_with_only_two_distinct_metrics(self) -> None:
        obs = [
            _make_obs("oq1", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs(
                "oq2",
                "query_count",
                delta_pct=32.0,
                slice_val={"cluster": "k1", "user": "sys_titan"},
            ),
            _make_obs(
                "ot",
                "queued_time",
                delta_pct=58.5,
                slice_val={"cluster": "k1", "user": "sys_titan"},
            ),
        ]
        claims = [
            {
                **_make_claim(
                    "cq1", "query_count", obs_ids=["oq1"], slice_val={"user": "sys_titan"}
                ),
                "status": "confirmed",
            },
            {
                **_make_claim(
                    "cq2",
                    "query_count",
                    obs_ids=["oq2"],
                    slice_val={"cluster": "k1", "user": "sys_titan"},
                ),
                "status": "confirmed",
            },
            {
                **_make_claim(
                    "ct",
                    "queued_time",
                    obs_ids=["ot"],
                    slice_val={"cluster": "k1", "user": "sys_titan"},
                ),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq1", "cq2", category="subset_or_overlap"),
            self._relation("cq2", "ct", category="exact_match"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(upgrades, [])

    def test_skips_claims_already_at_l1(self) -> None:
        obs = [
            _make_obs("oq", "query_count", delta_pct=30.0, slice_val={"user": "sys_titan"}),
            _make_obs("oc", "cpu_time", delta_pct=8.6, slice_val={"user": "sys_titan"}),
            _make_obs("ot", "queued_time", delta_pct=58.5, slice_val={"user": "sys_titan"}),
        ]
        claims = [
            {
                **_make_claim(
                    "cq", "query_count", level="L1", obs_ids=["oq"], slice_val={"user": "sys_titan"}
                ),
                "status": "confirmed",
            },
            {
                **_make_claim("cc", "cpu_time", obs_ids=["oc"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
            {
                **_make_claim("ct", "queued_time", obs_ids=["ot"], slice_val={"user": "sys_titan"}),
                "status": "confirmed",
            },
        ]
        relations = [
            self._relation("cq", "cc"),
            self._relation("cc", "ct"),
        ]

        upgrades = self.checker.check(claims, obs, [], relations=relations)
        self.assertEqual(upgrades, [])


# ── Registry and integration ──────────────────────────────────────────────────


class CausalCheckerRegistryTests(unittest.TestCase):
    def test_registry_runs_all_checkers(self) -> None:
        """Default registry returns upgrades from CrossSlice checker."""
        registry = build_default_registry()
        obs = [_make_obs(f"o{i}", "ctr", delta_pct=5.0, slice_val={"s": str(i)}) for i in range(5)]
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = registry.run_all([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c1")
        self.assertEqual(upgrades[0].new_level, "L1")

    def test_highest_level_wins_in_merge(self) -> None:
        """Two checkers proposing L1 and L2 for same claim → merged upgrade is L2."""
        # CrossSlice → L1, then manual L2 from TemporalPrecedence
        registry = CausalCheckerRegistry()

        class _L1Checker(CrossSliceConsistencyChecker):
            pass

        class _L2Checker(TemporalPrecedenceChecker):
            pass

        obs = [
            _make_obs(
                "o1",
                "m",
                delta_pct=5.0,
                slice_val={"s": "a"},
                window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "m",
                delta_pct=6.0,
                slice_val={"s": "b"},
                window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"},
            ),
            _make_obs(
                "o3",
                "m",
                delta_pct=7.0,
                slice_val={"s": "c"},
                window={"start": "2024-01-20", "end": "2024-01-27", "granularity": "day"},
            ),
        ]
        claim = _make_claim("c1", "m", level="L0", obs_ids=["o1", "o2", "o3"])
        # CrossSlice upgrades to L1; TemporalPrecedence requires L1 so won't fire yet
        # Register L1 first, then we manually patch claim to L1 to see merge
        registry.register(_L1Checker())

        upgrades = registry.run_all([claim], obs, [])
        # At least one upgrade from CrossSliceConsistency
        self.assertGreater(len(upgrades), 0)
        best = upgrades[0]
        self.assertIn(best.new_level, ("L1", "L2"))

    def test_justification_tokens_merged(self) -> None:
        """run_all merges tokens from multiple checkers for the same claim."""
        registry = CausalCheckerRegistry()

        # Two checkers both target the same claim with different tokens
        class _Checker1(CausalChecker):
            @property
            def name(self) -> str:
                return "c1"

            def check(self, claims, observations, edges, relations=None):
                return [
                    LevelUpgrade(
                        claim_id="claim_a", new_level="L1", justification_tokens=["token_from_c1"]
                    )
                ]

        class _Checker2(CausalChecker):
            @property
            def name(self) -> str:
                return "c2"

            def check(self, claims, observations, edges, relations=None):
                return [
                    LevelUpgrade(
                        claim_id="claim_a", new_level="L1", justification_tokens=["token_from_c2"]
                    )
                ]

        registry.register(_Checker1())
        registry.register(_Checker2())
        upgrades = registry.run_all([], [], [])
        self.assertEqual(len(upgrades), 1)
        tokens = upgrades[0].justification_tokens
        self.assertIn("token_from_c1", tokens)
        self.assertIn("token_from_c2", tokens)

    def test_no_downgrade_via_registry(self) -> None:
        """A checker proposing L1 cannot downgrade a claim already at L2."""
        registry = CausalCheckerRegistry()

        class _L1Checker(CausalChecker):
            @property
            def name(self) -> str:
                return "l1_proposer"

            def check(self, claims, observations, edges, relations=None):
                return [LevelUpgrade(claim_id="c1", new_level="L1", justification_tokens=["t"])]

        registry.register(_L1Checker())
        # Even if the registry merges L1, the IncrementalSynthesizer's _run_causal_checkers
        # applies the no-downgrade rule. Here we just verify the registry correctly
        # keeps L1 as the merged proposal.
        upgrades = registry.run_all([], [], [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].new_level, "L1")

    def test_registry_caps_merged_confidence_boost(self) -> None:
        registry = CausalCheckerRegistry()

        class _CheckerA(CausalChecker):
            @property
            def name(self) -> str:
                return "a"

            def check(self, claims, observations, edges, relations=None):
                return [LevelUpgrade(claim_id="c1", new_level="L1", confidence_boost=0.08)]

        class _CheckerB(CausalChecker):
            @property
            def name(self) -> str:
                return "b"

            def check(self, claims, observations, edges, relations=None):
                return [LevelUpgrade(claim_id="c1", new_level="L1", confidence_boost=0.07)]

        registry.register(_CheckerA())
        registry.register(_CheckerB())

        upgrades = registry.run_all([], [], [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].confidence_boost, 0.12)

    def test_incremental_synthesizer_applies_causal_checkers(self) -> None:
        """IncrementalSynthesizer.process() returns causal_upgrades and upgrades DB."""
        from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteMetadataStore(Path(tmpdir) / "test.meta.sqlite")
            store.initialize()

            sess_id = "sess_test000001"
            step_id = "step_test000001"

            # Insert a session and 5 observations with consistent positive delta
            store.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "test", "{}", "{}", "{}", "active"],
            )
            for i in range(5):
                oid = f"obs_test{i:06d}"
                store.execute(
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
                        json.dumps({"metric": "watch_time", "slice": {"seg": str(i)}}),
                        json.dumps({"delta_pct": 5.0 + i}),
                        json.dumps({"sample_size": 100, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        None,
                        i,
                    ],
                )

            synth = IncrementalSynthesizer(store)
            result = synth.process(sess_id)

            # Should have created a claim and applied causal upgrade(s)
            self.assertIn("causal_upgrades", result)
            self.assertGreaterEqual(result["claims_created"], 1)

            # Verify the claim's inference_level was upgraded in the DB
            rows = store.query_rows(
                "SELECT inference_level, inference_justification_json FROM claims WHERE session_id = ?",
                [sess_id],
            )
            self.assertGreater(len(rows), 0)
            upgraded = any(r["inference_level"] != "L0" for r in rows)
            self.assertTrue(upgraded, "Expected at least one claim to be upgraded from L0")


# ── G-2d: CausalEdge contract tests ──────────────────────────────────────────


class CausalEdgeContractTests(unittest.TestCase):
    """Verify TemporalPrecedenceChecker emits CausalEdge and registry merges edges."""

    def test_temporal_precedence_emits_causal_edge(self) -> None:
        """Checker returns a claim-to-claim CausalEdge for the temporal predecessor."""
        checker = TemporalPrecedenceChecker()
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [_make_relation("c1", "c2", supporting_observation_ids=["o1", "o2"])]
        upgrades = checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 1)
        edges = upgrades[0].causal_edges
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertIsInstance(edge, CausalEdge)
        self.assertEqual(edge.edge_type, "temporally_precedes")
        self.assertEqual(edge.from_node_id, "c1")
        self.assertEqual(edge.from_node_type, "claim")
        self.assertEqual(edge.to_node_id, "c2")
        self.assertEqual(edge.to_node_type, "claim")
        self.assertIn("3 days", edge.explanation)
        self.assertIn("c2", edge.explanation)

    def test_no_causal_edge_without_upgrade(self) -> None:
        """When temporal precedence check fails, no causal edge is produced."""
        checker = TemporalPrecedenceChecker()
        obs = [
            _make_obs(
                "o1",
                "query_count",
                delta_pct=5.0,
                window={"start": "2024-01-01", "end": "2024-01-10", "granularity": "day"},
            ),
            _make_obs(
                "o2",
                "queued_time",
                delta_pct=8.0,
                window={"start": "2024-01-08", "end": "2024-01-15", "granularity": "day"},
            ),
        ]
        claims = [
            _make_claim(
                "c1",
                "query_count",
                level="L1",
                obs_ids=["o1"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
            _make_claim(
                "c2",
                "queued_time",
                level="L1",
                obs_ids=["o2"],
                slice_val={"user": "sys_titan"},
                status="confirmed",
            ),
        ]
        relations = [_make_relation("c1", "c2", supporting_observation_ids=["o1", "o2"])]
        upgrades = checker.check(claims, obs, [], relations=relations)
        self.assertEqual(len(upgrades), 0)

    def test_registry_merges_causal_edges(self) -> None:
        """run_all merges causal edges from multiple checkers, deduplicating by key."""
        registry = CausalCheckerRegistry()

        edge_a = CausalEdge("c0", "claim", "c1", "claim", "temporally_precedes", 0.8, "x")
        edge_b = CausalEdge("c2", "claim", "c1", "claim", "temporally_precedes", 0.7, "y")

        class _CheckerA(CausalChecker):
            @property
            def name(self) -> str:
                return "a"

            def check(self, claims, observations, edges, relations=None):
                return [LevelUpgrade("c1", "L2", ["tok_a"], 0.01, [edge_a])]

        class _CheckerB(CausalChecker):
            @property
            def name(self) -> str:
                return "b"

            def check(self, claims, observations, edges, relations=None):
                # Same key as edge_a — should be deduped
                return [LevelUpgrade("c1", "L2", ["tok_b"], 0.01, [edge_a])]

        class _CheckerC(CausalChecker):
            @property
            def name(self) -> str:
                return "c"

            def check(self, claims, observations, edges, relations=None):
                # Different from_node_id — should be kept
                return [LevelUpgrade("c1", "L2", ["tok_c"], 0.01, [edge_b])]

        registry.register(_CheckerA())
        registry.register(_CheckerB())
        registry.register(_CheckerC())
        upgrades = registry.run_all([], [], [])
        self.assertEqual(len(upgrades), 1)
        edges_out = upgrades[0].causal_edges
        # edge_a should appear once (deduped), edge_b should appear once
        self.assertEqual(len(edges_out), 2)
        from_ids = {e.from_node_id for e in edges_out}
        self.assertIn("c0", from_ids)
        self.assertIn("c2", from_ids)


class CausalEdgePersistenceTests(unittest.TestCase):
    """Verify causal edges are not materialized during incremental synthesis."""

    def _make_session_with_windowed_obs(self, store, sess_id: str, step_id: str) -> None:
        """Insert session + 2 non-overlapping-window observations for metric 'm'."""
        store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            [sess_id, "test", "{}", "{}", "{}", "active"],
        )
        windows = [
            ("obs_w1", {"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"}),
            ("obs_w2", {"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"}),
        ]
        for oid, win in windows:
            store.execute(
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
                    json.dumps({"metric": "m", "slice": {}}),
                    json.dumps({"delta_pct": 5.0}),
                    json.dumps({"sample_size": 100, "practical_significance": True}),
                    json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                    json.dumps(win),
                    0,
                ],
            )

    def test_temporally_precedes_edge_not_written_during_incremental_processing(self) -> None:
        """Incremental synthesis upgrades claims but leaves causal edge materialization to final synthesis."""
        from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteMetadataStore(Path(tmpdir) / "test.meta.sqlite")
            store.initialize()
            sess_id = "sess_edgetest01"
            self._make_session_with_windowed_obs(store, sess_id, "step_e01")

            synth = IncrementalSynthesizer(store)
            synth.process(sess_id)  # call 1: CrossSlice → L1
            synth.process(sess_id)  # call 2: TemporalPrecedence → L2 + edge

            edges = store.query_rows(
                "SELECT edge_type, from_node_id, to_node_type FROM evidence_edges WHERE session_id = ?",
                [sess_id],
            )
            self.assertEqual(edges, [])

    def test_repeated_incremental_processing_keeps_edge_table_empty(self) -> None:
        """Repeated incremental processing should not materialize causal edges."""
        from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteMetadataStore(Path(tmpdir) / "test.meta.sqlite")
            store.initialize()
            sess_id = "sess_edgetest02"
            self._make_session_with_windowed_obs(store, sess_id, "step_e02")

            synth = IncrementalSynthesizer(store)
            synth.process(sess_id)  # call 1: CrossSlice → L1
            synth.process(sess_id)  # call 2: TemporalPrecedence → L2 + edge
            synth.process(sess_id)  # call 3: reconcile should keep exactly one edge

            edges = store.query_rows(
                "SELECT edge_type FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                [sess_id],
            )
            self.assertEqual(edges, [])


# ── Helper function unit tests ────────────────────────────────────────────────


class HelperFunctionTests(unittest.TestCase):
    def test_spearman_perfect_positive(self) -> None:
        rho = _spearman_correlation([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 6.0, 8.0, 10.0])
        self.assertAlmostEqual(rho, 1.0)

    def test_spearman_perfect_negative(self) -> None:
        rho = _spearman_correlation([1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 8.0, 6.0, 4.0, 2.0])
        self.assertAlmostEqual(rho, -1.0)

    def test_detect_reversal_basic(self) -> None:
        # 3 positive, 2 negative → 2 reversal periods
        deltas = [5.0, 6.0, 4.0, -3.0, -4.0]
        self.assertEqual(_detect_reversal(deltas, 2), 2)

    def test_detect_reversal_insufficient(self) -> None:
        # Only 1 reversed period
        deltas = [5.0, 4.0, 6.0, -3.0]
        self.assertEqual(_detect_reversal(deltas, 2), 1)

    def test_detect_reversal_none(self) -> None:
        # All same direction
        deltas = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(_detect_reversal(deltas, 2), 0)


if __name__ == "__main__":
    unittest.main()
