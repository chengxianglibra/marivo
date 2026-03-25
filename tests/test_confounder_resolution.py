"""Tests for confounder auto-resolution (roadmap 1.1)."""

from __future__ import annotations

import unittest
from uuid import uuid4

from app.evidence_engine.confounder_resolution import (
    RESOLUTION_RULES,
    filter_resolved_gap_keys,
    resolve_confounders,
)
from app.evidence_engine.causal_basis import (
    EvidenceGap,
    GAP_CORRELATION_ONLY,
    GAP_NORMALISE_WORKLOAD_VOLUME,
)


def _make_claim(
    metric: str = "query_count",
    slice_dict: dict | None = None,
    status: str = "confirmed",
    confidence: float = 0.91,
    text: str | None = None,
) -> dict:
    claim_id = f"claim_{uuid4().hex[:12]}"
    return {
        "claim_id": claim_id,
        "type": "root_cause_candidate",
        "text": text or f"{metric} changed for {slice_dict}",
        "scope": {"metric": metric, "slice": slice_dict or {}},
        "confidence": confidence,
        "status": status,
        "supporting_observations": [],
        "contradicting_observations": [],
        "confidence_breakdown": {},
        "inference_level": "L0",
        "inference_justification": [],
    }


def _make_rec(claim_id: str, unresolved: list[dict]) -> dict:
    return {
        "rec_id": f"rec_{uuid4().hex[:12]}",
        "claim_id": claim_id,
        "action_text": "Drill into something",
        "priority": "P1",
        "expected_impact": "",
        "risk": "",
        "validation_metric": {},
        "causal_basis": {
            "inference_level": "L0",
            "strongest_evidence_summary": "test",
            "unresolved_confounders": unresolved,
            "suggested_validation": "run something",
        },
    }


class TestResolveConfounders(unittest.TestCase):
    """Unit tests for resolve_confounders()."""

    def test_basic_resolution_workload_volume(self) -> None:
        """Confirmed query_count claim resolves normalise_workload_volume gap."""
        volume_claim = _make_claim("query_count", {"user": "sys_titan"})
        backing_claim = _make_claim("queued_time", {"user": "sys_titan"})
        rec = _make_rec(
            backing_claim["claim_id"],
            [{"key": GAP_NORMALISE_WORKLOAD_VOLUME, "text": "check workload volume"}],
        )
        result = resolve_confounders([rec], [volume_claim, backing_claim])
        self.assertEqual(len(result), 1)
        cb = result[0]["causal_basis"]
        self.assertEqual(cb["unresolved_confounders"], [])
        self.assertEqual(len(cb["resolved_confounders"]), 1)
        resolved = cb["resolved_confounders"][0]
        self.assertEqual(resolved["key"], GAP_NORMALISE_WORKLOAD_VOLUME)
        self.assertEqual(resolved["resolved_by"], volume_claim["claim_id"])
        self.assertIn("query_count", resolved["summary"])

    def test_no_match_keeps_unresolved(self) -> None:
        """Without a volume claim, normalise_workload_volume stays unresolved."""
        backing_claim = _make_claim("queued_time", {"user": "sys_titan"})
        other_claim = _make_claim("cpu_time", {"user": "sys_titan"})
        rec = _make_rec(
            backing_claim["claim_id"],
            [{"key": GAP_NORMALISE_WORKLOAD_VOLUME, "text": "check workload volume"}],
        )
        result = resolve_confounders([rec], [other_claim, backing_claim])
        cb = result[0]["causal_basis"]
        self.assertEqual(len(cb["unresolved_confounders"]), 1)
        self.assertEqual(cb["unresolved_confounders"][0]["key"], GAP_NORMALISE_WORKLOAD_VOLUME)
        self.assertEqual(cb["resolved_confounders"], [])

    def test_same_slice_preferred(self) -> None:
        """When multiple volume claims exist, the same-slice one is chosen."""
        same_slice_claim = _make_claim("query_count", {"user": "sys_titan"})
        other_slice_claim = _make_claim("query_count", {"user": "sys_other"})
        backing_claim = _make_claim("queued_time", {"user": "sys_titan"})
        rec = _make_rec(
            backing_claim["claim_id"],
            [{"key": GAP_NORMALISE_WORKLOAD_VOLUME, "text": "check workload volume"}],
        )
        result = resolve_confounders(
            [rec],
            [other_slice_claim, same_slice_claim, backing_claim],
        )
        resolved = result[0]["causal_basis"]["resolved_confounders"][0]
        self.assertEqual(resolved["resolved_by"], same_slice_claim["claim_id"])

    def test_null_causal_basis_skipped(self) -> None:
        """Recommendations with causal_basis=None are passed through unchanged."""
        rec = {
            "rec_id": "rec_old",
            "claim_id": "claim_old",
            "action_text": "old rec",
            "causal_basis": None,
        }
        volume_claim = _make_claim("query_count")
        result = resolve_confounders([rec], [volume_claim])
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["causal_basis"])

    def test_no_resolution_rule_gap_unchanged(self) -> None:
        """Fallback gaps (like correlation_only) have no rule and stay unresolved."""
        backing_claim = _make_claim("queued_time", {"user": "sys_titan"})
        volume_claim = _make_claim("query_count", {"user": "sys_titan"})
        rec = _make_rec(
            backing_claim["claim_id"],
            [
                {"key": GAP_CORRELATION_ONLY, "text": "correlation only"},
                {"key": GAP_NORMALISE_WORKLOAD_VOLUME, "text": "check workload volume"},
            ],
        )
        result = resolve_confounders([rec], [volume_claim, backing_claim])
        cb = result[0]["causal_basis"]
        # correlation_only stays unresolved
        self.assertEqual(len(cb["unresolved_confounders"]), 1)
        self.assertEqual(cb["unresolved_confounders"][0]["key"], GAP_CORRELATION_ONLY)
        # normalise_workload_volume resolved
        self.assertEqual(len(cb["resolved_confounders"]), 1)
        self.assertEqual(cb["resolved_confounders"][0]["key"], GAP_NORMALISE_WORKLOAD_VOLUME)

    def test_empty_confounders_get_resolved_field(self) -> None:
        """Recs with no unresolved confounders get an empty resolved_confounders list."""
        backing_claim = _make_claim("queued_time")
        rec = _make_rec(backing_claim["claim_id"], [])
        result = resolve_confounders([rec], [backing_claim])
        self.assertEqual(result[0]["causal_basis"]["resolved_confounders"], [])


class TestFilterResolvedGapKeys(unittest.TestCase):
    """Tests for filter_resolved_gap_keys (used by reflection context)."""

    def test_filters_resolved_gap(self) -> None:
        gaps = [
            EvidenceGap(key=GAP_NORMALISE_WORKLOAD_VOLUME, text="check workload"),
            EvidenceGap(key=GAP_CORRELATION_ONLY, text="correlation only"),
        ]
        confirmed = [_make_claim("query_count", {"user": "sys_titan"})]
        filtered = filter_resolved_gap_keys(gaps, confirmed)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].key, GAP_CORRELATION_ONLY)

    def test_no_confirmed_claims_returns_all(self) -> None:
        gaps = [
            EvidenceGap(key=GAP_NORMALISE_WORKLOAD_VOLUME, text="check workload"),
        ]
        filtered = filter_resolved_gap_keys(gaps, [])
        self.assertEqual(len(filtered), 1)


class TestResolutionRules(unittest.TestCase):
    """Sanity checks for RESOLUTION_RULES predicates."""

    def test_volume_rule_matches_count_metrics(self) -> None:
        rule = RESOLUTION_RULES[GAP_NORMALISE_WORKLOAD_VOLUME]
        for metric in ("query_count", "request_count", "total_volume", "qps_metric"):
            claim = _make_claim(metric)
            self.assertTrue(rule(claim), f"Expected match for metric={metric}")

    def test_volume_rule_rejects_non_volume_metrics(self) -> None:
        rule = RESOLUTION_RULES[GAP_NORMALISE_WORKLOAD_VOLUME]
        for metric in ("cpu_time", "queued_time", "error_rate", "latency"):
            claim = _make_claim(metric)
            self.assertFalse(rule(claim), f"Expected no match for metric={metric}")

    def test_volume_rule_rejects_false_positive_substring_matches(self) -> None:
        """Metrics like 'discount_rate' contain 'count' but are not volume metrics."""
        rule = RESOLUTION_RULES[GAP_NORMALISE_WORKLOAD_VOLUME]
        for metric in ("discount_rate", "account_id", "account_count"):
            claim = _make_claim(metric)
            self.assertFalse(rule(claim), f"Expected no match for metric={metric}")


class TestScopeOverlap(unittest.TestCase):
    """Tests for _scope_overlap edge cases."""

    def test_unhashable_slice_values_do_not_crash(self) -> None:
        from app.evidence_engine.confounder_resolution import _scope_overlap
        overlap = _scope_overlap(
            {"user": "sys_titan", "tags": ["a", "b"]},
            {"user": "sys_titan", "tags": ["a", "b"]},
        )
        self.assertAlmostEqual(overlap, 1.0)

    def test_unhashable_different_values(self) -> None:
        from app.evidence_engine.confounder_resolution import _scope_overlap
        overlap = _scope_overlap(
            {"user": "sys_titan", "tags": ["a"]},
            {"user": "sys_titan", "tags": ["b"]},
        )
        self.assertAlmostEqual(overlap, 1.0 / 3.0, places=4)


class TestResolveConfoundersNoConfirmedClaims(unittest.TestCase):
    """Verify resolved_confounders field is present even with no confirmed claims."""

    def test_resolved_field_present_when_no_confirmed_claims(self) -> None:
        backing_claim = _make_claim("queued_time")
        rec = _make_rec(
            backing_claim["claim_id"],
            [{"key": GAP_NORMALISE_WORKLOAD_VOLUME, "text": "check workload"}],
        )
        result = resolve_confounders([rec], [])
        self.assertIn("resolved_confounders", result[0]["causal_basis"])
        self.assertEqual(result[0]["causal_basis"]["resolved_confounders"], [])

    def test_null_causal_basis_preserved_when_no_confirmed_claims(self) -> None:
        rec = {"rec_id": "rec_old", "claim_id": "c", "causal_basis": None}
        result = resolve_confounders([rec], [])
        self.assertIsNone(result[0]["causal_basis"])


class TestPipelineIntegration(unittest.TestCase):
    """End-to-end: resolved_confounders appears in EvidencePipeline.build_synthesis() output."""

    def test_build_synthesis_includes_resolved_confounders(self) -> None:
        from app.evidence_engine import EvidencePipeline
        from app.evidence_engine.synthesizers import DefaultClaimSynthesizer

        pipeline = EvidencePipeline(DefaultClaimSynthesizer())

        # Two observations: same metric, different slices → triggers normalise_workload_volume
        obs_base = {
            "type": "metric_change",
            "payload": {"current_value": 100, "baseline_value": 90, "delta_pct": 11.1,
                        "current_sessions": 500, "baseline_sessions": 480},
            "significance": {"sample_size": 500, "practical_significance": True},
            "quality": {"freshness_ok": True, "sample_size_ok": True},
        }
        obs_volume = {
            **obs_base,
            "observation_id": f"obs_{uuid4().hex[:12]}",
            "subject": {"metric": "query_count", "slice": {"user": "sys_titan"}},
        }
        obs_queued = {
            **obs_base,
            "observation_id": f"obs_{uuid4().hex[:12]}",
            "subject": {"metric": "query_count", "slice": {"user": "sys_other"}},
        }
        result = pipeline.build_synthesis([obs_volume, obs_queued])
        for rec in result["recommendations"]:
            cb = rec.get("causal_basis")
            if cb is None:
                continue
            self.assertIn("resolved_confounders", cb)


if __name__ == "__main__":
    unittest.main()
