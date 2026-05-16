"""Tests for Phase 4b-3 canonical item key / finding identity helpers.

Covers:
- make_artifact_item_ref: D2 priority rules, key/index/bare paths
- make_item_identity: atomic co-generation, consistency with individual helpers
- Shared testutil functions: assert_finding_id_stable, assert_stable_key_beats_index,
  assert_projection_order_excluded
- All ArtifactItemRefCollection literals
- index=0 is valid (falsy-safe)
"""

from __future__ import annotations

import unittest

from marivo.core.evidence.canonical_finding import (
    make_artifact_item_ref,
    make_canonical_item_key,
    make_finding_id,
    make_item_identity,
)
from tests.finding_identity_testutil import (
    assert_finding_id_stable,
    assert_projection_order_excluded,
    assert_stable_key_beats_index,
)

# ---------------------------------------------------------------------------
# make_artifact_item_ref — D2 priority: stable key > index > bare collection
# ---------------------------------------------------------------------------


class TestMakeArtifactItemRef(unittest.TestCase):
    # --- key-priority path ---

    def test_key_present_sets_key_clears_index(self) -> None:
        ref = make_artifact_item_ref("rows", key="device_ios", index=5)
        self.assertEqual(ref["key"], "device_ios")
        self.assertIsNone(ref["index"])

    def test_key_present_no_index(self) -> None:
        ref = make_artifact_item_ref("rows", key="device_android")
        self.assertEqual(ref["key"], "device_android")
        self.assertIsNone(ref["index"])
        self.assertEqual(ref["collection"], "rows")

    def test_key_present_index_is_zero(self) -> None:
        """index=0 is falsy but should still be overridden by the stable key."""
        ref = make_artifact_item_ref("rows", key="seg_us", index=0)
        self.assertEqual(ref["key"], "seg_us")
        self.assertIsNone(ref["index"])

    # --- index-only path ---

    def test_index_used_when_no_key(self) -> None:
        ref = make_artifact_item_ref("candidates", index=3)
        self.assertEqual(ref["index"], 3)
        self.assertIsNone(ref["key"])
        self.assertEqual(ref["collection"], "candidates")

    def test_index_zero_is_valid(self) -> None:
        """index=0 must NOT be treated as falsy and discarded."""
        ref = make_artifact_item_ref("rows", index=0)
        self.assertEqual(ref["index"], 0)
        self.assertIsNone(ref["key"])

    # --- bare-collection path ---

    def test_bare_collection_both_none(self) -> None:
        ref = make_artifact_item_ref("value")
        self.assertEqual(ref["collection"], "value")
        self.assertIsNone(ref["key"])
        self.assertIsNone(ref["index"])

    def test_result_collection_bare(self) -> None:
        ref = make_artifact_item_ref("result")
        self.assertEqual(ref["collection"], "result")
        self.assertIsNone(ref["key"])
        self.assertIsNone(ref["index"])

    # --- all canonical collection literals ---

    def test_all_collection_literals_accepted(self) -> None:
        for coll in ("value", "rows", "buckets", "candidates", "points", "result"):
            ref = make_artifact_item_ref(coll)  # type: ignore[arg-type]
            self.assertEqual(ref["collection"], coll)

    # --- TypedDict structure ---

    def test_returns_artifact_item_ref_dict(self) -> None:
        ref = make_artifact_item_ref("rows", key="k1")
        self.assertIsInstance(ref, dict)
        self.assertIn("collection", ref)
        self.assertIn("key", ref)
        self.assertIn("index", ref)


# ---------------------------------------------------------------------------
# make_item_identity — co-generation consistency
# ---------------------------------------------------------------------------


class TestMakeItemIdentity(unittest.TestCase):
    def test_returns_two_tuple(self) -> None:
        result = make_item_identity("rows", key="ios")
        self.assertEqual(len(result), 2)

    def test_canonical_item_key_matches_make_canonical_item_key(self) -> None:
        cik, _ = make_item_identity("rows", key="ios")
        expected = make_canonical_item_key("rows", key="ios")
        self.assertEqual(cik, expected)

    def test_artifact_item_ref_matches_make_artifact_item_ref(self) -> None:
        _, ref = make_item_identity("rows", key="ios")
        expected_ref = make_artifact_item_ref("rows", key="ios")
        self.assertEqual(ref, expected_ref)

    def test_key_priority_in_both_outputs(self) -> None:
        cik, ref = make_item_identity("rows", key="seg_de", index=7)
        self.assertEqual(cik, "rows:seg_de")
        self.assertEqual(ref["key"], "seg_de")
        self.assertIsNone(ref["index"])

    def test_index_path_consistent(self) -> None:
        cik, ref = make_item_identity("candidates", index=2)
        self.assertEqual(cik, "candidates:2")
        self.assertEqual(ref["index"], 2)
        self.assertIsNone(ref["key"])

    def test_bare_collection_path_consistent(self) -> None:
        cik, ref = make_item_identity("value")
        self.assertEqual(cik, "value")
        self.assertIsNone(ref["key"])
        self.assertIsNone(ref["index"])

    def test_stable_across_two_calls(self) -> None:
        """make_item_identity must be deterministic."""
        r1 = make_item_identity("buckets", key="2024-01-01/2024-01-08")
        r2 = make_item_identity("buckets", key="2024-01-01/2024-01-08")
        self.assertEqual(r1[0], r2[0])
        self.assertEqual(r1[1], r2[1])

    def test_index_zero_is_not_discarded(self) -> None:
        cik, ref = make_item_identity("rows", index=0)
        self.assertEqual(cik, "rows:0")
        self.assertEqual(ref["index"], 0)
        self.assertIsNone(ref["key"])

    def test_all_collection_literals(self) -> None:
        for coll in ("value", "rows", "buckets", "candidates", "points", "result"):
            cik, ref = make_item_identity(coll)  # type: ignore[arg-type]
            self.assertEqual(cik, coll)
            self.assertEqual(ref["collection"], coll)


# ---------------------------------------------------------------------------
# Shared testutil functions
# ---------------------------------------------------------------------------


class TestAssertFindingIdStable(unittest.TestCase):
    def test_scalar_value(self) -> None:
        assert_finding_id_stable(self, "art_obs_01", "observation", "value")

    def test_stable_key_row(self) -> None:
        assert_finding_id_stable(self, "art_cmp_01", "delta", "rows", key="device_ios")

    def test_index_candidate(self) -> None:
        assert_finding_id_stable(self, "art_det_01", "anomaly_candidate", "candidates", index=0)

    def test_result_collection(self) -> None:
        assert_finding_id_stable(self, "art_cor_01", "correlation_result", "result")

    def test_prefix_is_fnd(self) -> None:
        cik = make_canonical_item_key("value")
        fid = make_finding_id("art_x", "observation", cik)
        self.assertTrue(fid.startswith("fnd_"))


class TestAssertStableKeyBeatsIndex(unittest.TestCase):
    def test_rows_key_beats_index(self) -> None:
        assert_stable_key_beats_index(self, "rows", "device_ios", 5)

    def test_buckets_key_beats_index(self) -> None:
        assert_stable_key_beats_index(self, "buckets", "2024-01-01/2024-01-08", 0)

    def test_candidates_key_beats_index(self) -> None:
        assert_stable_key_beats_index(self, "candidates", "metric_play_start", 3)

    def test_points_key_beats_index(self) -> None:
        assert_stable_key_beats_index(self, "points", "2024-02-01/2024-02-08", 1)


class TestAssertProjectionOrderExcluded(unittest.TestCase):
    def test_delta_stable_key(self) -> None:
        assert_projection_order_excluded(self, "art_cmp_01", "delta", "rows", "device_ios")

    def test_decomposition_item_stable_key(self) -> None:
        assert_projection_order_excluded(
            self, "art_dec_01", "decomposition_item", "rows", "region_us"
        )

    def test_observation_stable_key(self) -> None:
        assert_projection_order_excluded(
            self, "art_obs_01", "observation", "buckets", "2024-01-01/2024-01-08"
        )


if __name__ == "__main__":
    unittest.main()
