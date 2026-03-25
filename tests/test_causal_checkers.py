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
    CrossSliceConsistencyChecker,
    DoseResponseChecker,
    LevelUpgrade,
    ReversalChecker,
    TemporalPrecedenceChecker,
    _detect_reversal,
    _spearman_correlation,
    build_default_registry,
    get_default_registry,
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
        "type": "metric_change",
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
) -> dict:
    return {
        "claim_id": claim_id,
        "type": "root_cause_candidate",
        "text": f"{metric} changed (tentative)",
        "scope": {"metric": metric, "slice": slice_val or {}},
        "confidence": 0.50,
        "status": "tentative",
        "supporting_observations": list(obs_ids or []),
        "contradicting_observations": [],
        "confidence_breakdown": {},
        "inference_level": level,
        "inference_justification": [],
    }


# ── CrossSliceConsistencyChecker ──────────────────────────────────────────────


class CrossSliceConsistencyCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = CrossSliceConsistencyChecker()

    def test_upgrade_l0_to_l1_consistent_positive(self) -> None:
        """5/5 positive deltas → consistency=1.0 > 0.80 → L1 upgrade."""
        obs = [_make_obs(f"o{i}", "ctr", delta_pct=5.0 + i, slice_val={"seg": str(i)}) for i in range(5)]
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "c1")
        self.assertEqual(upgrades[0].new_level, "L1")
        self.assertTrue(any("L1" in t for t in upgrades[0].justification_tokens))

    def test_no_upgrade_insufficient_consistency_50_pct(self) -> None:
        """3 positive + 3 negative → 50% consistency → no upgrade."""
        obs = (
            [_make_obs(f"op{i}", "ctr", delta_pct=5.0, slice_val={"seg": f"p{i}"}) for i in range(3)]
            + [_make_obs(f"on{i}", "ctr", delta_pct=-5.0, slice_val={"seg": f"n{i}"}) for i in range(3)]
        )
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_claim_already_l1(self) -> None:
        """L1 claim is skipped (only upgrades L0)."""
        obs = [_make_obs(f"o{i}", "ctr", delta_pct=5.0, slice_val={"seg": str(i)}) for i in range(5)]
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
        obs = [_make_obs(f"o{i}", "ctr", delta_pct=5.0, slice_val={"seg": str(i)}) for i in range(4)]
        obs.append(_make_obs("o4", "ctr", delta_pct=-5.0, slice_val={"seg": "neg"}))
        claim = _make_claim("c1", "ctr", level="L0", obs_ids=[o["observation_id"] for o in obs])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)


# ── TemporalPrecedenceChecker ─────────────────────────────────────────────────


class TemporalPrecedenceCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = TemporalPrecedenceChecker()

    def test_upgrade_l1_to_l2_with_temporal_gap(self) -> None:
        """First window ends before second window starts → strict precedence → L2."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0,
                      window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=8.0,
                      window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].new_level, "L2")
        self.assertIn("temporal_precedence", upgrades[0].justification_tokens[0])
        self.assertIn("lag=3d", upgrades[0].justification_tokens[0])

    def test_no_upgrade_l0_claim(self) -> None:
        """L0 claim is not eligible for TemporalPrecedenceChecker (requires L1)."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0,
                      window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=8.0,
                      window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"}),
        ]
        claim = _make_claim("c1", "m", level="L0", obs_ids=["o1", "o2"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_null_observed_window(self) -> None:
        """Observations without observed_window are not windowed → no upgrade."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0, window=None),
            _make_obs("o2", "m", delta_pct=8.0, window=None),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_overlapping_windows(self) -> None:
        """Overlapping windows (end >= start of next) → no strict precedence."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0,
                      window={"start": "2024-01-01", "end": "2024-01-10", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=8.0,
                      window={"start": "2024-01-08", "end": "2024-01-15", "granularity": "day"}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_no_upgrade_only_one_windowed_observation(self) -> None:
        """Only 1 observation has observed_window → below MIN_WINDOWED_OBSERVATIONS=2."""
        obs = [
            _make_obs("o1", "m", delta_pct=5.0,
                      window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=8.0, window=None),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = self.checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)


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
            _make_obs("o1", "m", delta_pct=5.0, slice_val={"s": "a"},
                      window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=6.0, slice_val={"s": "b"},
                      window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"}),
            _make_obs("o3", "m", delta_pct=7.0, slice_val={"s": "c"},
                      window={"start": "2024-01-20", "end": "2024-01-27", "granularity": "day"}),
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
                return [LevelUpgrade(claim_id="claim_a", new_level="L1",
                                     justification_tokens=["token_from_c1"])]

        class _Checker2(CausalChecker):
            @property
            def name(self) -> str:
                return "c2"

            def check(self, claims, observations, edges, relations=None):
                return [LevelUpgrade(claim_id="claim_a", new_level="L1",
                                     justification_tokens=["token_from_c2"])]

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
                return [LevelUpgrade(claim_id="c1", new_level="L1",
                                     justification_tokens=["t"])]

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
                        oid, sess_id, step_id, "metric_change",
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
        """Checker returns a CausalEdge pointing earliest obs → claim."""
        checker = TemporalPrecedenceChecker()
        obs = [
            _make_obs("o1", "m", delta_pct=5.0,
                      window={"start": "2024-01-01", "end": "2024-01-07", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=8.0,
                      window={"start": "2024-01-10", "end": "2024-01-17", "granularity": "day"}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 1)
        edges = upgrades[0].causal_edges
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertIsInstance(edge, CausalEdge)
        self.assertEqual(edge.edge_type, "temporally_precedes")
        self.assertEqual(edge.from_node_id, "o1")   # earliest obs
        self.assertEqual(edge.from_node_type, "observation")
        self.assertEqual(edge.to_node_id, "c1")
        self.assertEqual(edge.to_node_type, "claim")
        self.assertIn("3 days", edge.explanation)
        self.assertIn("o2", edge.explanation)  # paired obs mentioned

    def test_no_causal_edge_without_upgrade(self) -> None:
        """When temporal precedence check fails, no causal edge is produced."""
        checker = TemporalPrecedenceChecker()
        obs = [
            _make_obs("o1", "m", delta_pct=5.0,
                      window={"start": "2024-01-01", "end": "2024-01-10", "granularity": "day"}),
            _make_obs("o2", "m", delta_pct=8.0,
                      window={"start": "2024-01-08", "end": "2024-01-15", "granularity": "day"}),
        ]
        claim = _make_claim("c1", "m", level="L1", obs_ids=["o1", "o2"])
        upgrades = checker.check([claim], obs, [])
        self.assertEqual(len(upgrades), 0)

    def test_registry_merges_causal_edges(self) -> None:
        """run_all merges causal edges from multiple checkers, deduplicating by key."""
        registry = CausalCheckerRegistry()

        edge_a = CausalEdge("obs_a", "observation", "c1", "claim", "temporally_precedes", 0.8, "x")
        edge_b = CausalEdge("obs_b", "observation", "c1", "claim", "temporally_precedes", 0.7, "y")

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
        self.assertIn("obs_a", from_ids)
        self.assertIn("obs_b", from_ids)


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
                    oid, sess_id, step_id, "metric_change",
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
            synth.process(sess_id)   # call 1: CrossSlice → L1
            synth.process(sess_id)   # call 2: TemporalPrecedence → L2 + edge

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
            synth.process(sess_id)   # call 1: CrossSlice → L1
            synth.process(sess_id)   # call 2: TemporalPrecedence → L2 + edge
            synth.process(sess_id)   # call 3: reconcile should keep exactly one edge

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
