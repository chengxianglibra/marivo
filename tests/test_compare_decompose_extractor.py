"""Tests for the compare / decompose finding extractors (Phase 4d-3).

CompareArtifactExtractor (compare_artifact → DeltaFinding):
- scalar_delta → exactly 1 finding with correct DeltaPayload fields
- segmented_delta → 1 finding per row; segment key stability
- Unknown comparison_type raises ValueError
- Empty segmented rows → validate_for_commit("compare", result) raises FamilyEmptyError
- Registered in default_finding_registry under ("compare_artifact", "v1"); NULL normalisation

DecomposeArtifactExtractor (delta_decomposition → DecompositionItemFinding):
- N rows → N findings with correct DecompositionItemPayload fields
- rank is 1-based and matches artifact sort order
- scope_delta_ref.finding_id is derived deterministically from compare_artifact_id
- session_id flows into scope_delta_ref
- Missing compare_ref.artifact_id raises ValueError
- Empty rows → validate_for_commit("decompose", result) raises FamilyEmptyError
- Registered in default_finding_registry under ("delta_decomposition", "v1"); NULL normalisation
"""

# ruff: noqa: I001
from __future__ import annotations

import unittest
from typing import Any

from app.evidence_engine.canonical_finding import (
    StepRef,
    make_finding_id,
    make_item_identity,
)

# Registry must be imported first so bootstrap runs before individual extractors
# are imported (same pattern as test_detect_extractor.py).
from app.evidence_engine.finding_extractor_registry import (
    default_finding_registry,
    validate_for_commit,
)
from app.evidence_engine.compare_extractor import CompareArtifactExtractor
from app.evidence_engine.decompose_extractor import DecomposeArtifactExtractor
from app.evidence_engine.family_contract import FamilyEmptyError
from tests.finding_identity_testutil import (
    assert_finding_id_stable,
    assert_projection_order_excluded,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ART_ID = "art_compare_test001"
_DECOMP_ART_ID = "art_decomp_test001"
_COMPARE_ART_ID = "art_compare_upstream001"
_SESSION = "sess_cmp_test"
_STEP_ID = "step_cmp_001"
_STEP_REF: StepRef = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="compare")
_DECOMP_STEP_REF: StepRef = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="decompose")

_COMPARE_EXTRACTOR = CompareArtifactExtractor()
_DECOMPOSE_EXTRACTOR = DecomposeArtifactExtractor()


def _scalar_delta_payload(
    metric: str = "daily_users",
    left_value: float | None = 1000.0,
    right_value: float | None = 800.0,
    absolute_delta: float | None = 200.0,
    relative_delta: float | None = 0.25,
    direction: str = "increase",
    unit: str | None = None,
    left_scope: dict[str, Any] | None = None,
    left_time_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_type": "compare_artifact",
        "schema_version": "1.0",
        "comparison_type": "scalar_delta",
        "metric": metric,
        "left_ref": {"session_id": _SESSION, "step_id": "step_obs_left", "step_type": "observe"},
        "right_ref": {"session_id": _SESSION, "step_id": "step_obs_right", "step_type": "observe"},
        "unit": unit,
        "left_value": left_value,
        "right_value": right_value,
        "absolute_delta": absolute_delta,
        "relative_delta": relative_delta,
        "direction": direction,
        "resolved_input_summary": {
            "left_scope": left_scope or {},
            "right_scope": {},
            "left_time_scope": left_time_scope
            or {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
        },
        "comparability": {"status": "comparable", "issues": []},
        "analytical_metadata": {},
    }


def _calendar_alignment_summary(
    *,
    aligned_ratio: float = 1.0,
    unpaired_bucket_count: int = 0,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    coverage = {
        "aligned_bucket_count": 7,
        "unpaired_bucket_count": unpaired_bucket_count,
        "aligned_ratio": aligned_ratio,
    }
    return {
        "reuse_source": "observation_resolved_policy_summary",
        "policy_ref": "calendar_policy.holiday_yoy",
        "comparison_basis": "yoy",
        "resolved_calendar_source": "calendar.cn_holidays",
        "resolved_calendar_version": "2026.01",
        "comparability_warnings": list(warnings or []),
        "left_coverage_summary": dict(coverage),
        "right_coverage_summary": dict(coverage),
        "effective_coverage_summary": dict(coverage),
    }


def _segmented_delta_payload(
    metric: str = "revenue",
    rows: list[dict[str, Any]] | None = None,
    unit: str | None = "usd",
) -> dict[str, Any]:
    if rows is None:
        rows = [
            {
                "keys": {"country": "US"},
                "left_value": 500.0,
                "right_value": 400.0,
                "absolute_delta": 100.0,
                "relative_delta": 0.25,
                "direction": "increase",
                "presence": "both",
            },
            {
                "keys": {"country": "UK"},
                "left_value": 200.0,
                "right_value": 250.0,
                "absolute_delta": -50.0,
                "relative_delta": -0.20,
                "direction": "decrease",
                "presence": "both",
            },
        ]
    return {
        "artifact_type": "compare_artifact",
        "schema_version": "1.0",
        "comparison_type": "segmented_delta",
        "metric": metric,
        "left_ref": {"session_id": _SESSION, "step_id": "step_obs_left", "step_type": "observe"},
        "right_ref": {"session_id": _SESSION, "step_id": "step_obs_right", "step_type": "observe"},
        "dimensions": ["country"],
        "unit": unit,
        "rows": rows,
        "resolved_input_summary": {
            "left_scope": {},
            "right_scope": {},
            "left_time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
        },
        "comparability": {"status": "comparable", "issues": []},
        "analytical_metadata": {},
    }


def _decompose_payload(
    metric: str = "daily_users",
    dimension: str = "platform",
    compare_artifact_id: str = _COMPARE_ART_ID,
    rows: list[dict[str, Any]] | None = None,
    unit: str | None = None,
) -> dict[str, Any]:
    if rows is None:
        rows = [
            {
                "key": "ios",
                "left_value": 600.0,
                "right_value": 500.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.5,
                "direction": "increase",
                "presence": "both",
            },
            {
                "key": "android",
                "left_value": 400.0,
                "right_value": 300.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.5,
                "direction": "increase",
                "presence": "both",
            },
        ]
    return {
        "decomposition_type": "delta_decomposition",
        "metric": metric,
        "dimension": dimension,
        "unit": unit,
        "compare_ref": {
            "step_type": "compare",
            "session_id": _SESSION,
            "step_id": "step_cmp_upstream",
            "artifact_id": compare_artifact_id,
            "comparison_type": "scalar_delta",
        },
        "left_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_left",
            "artifact_id": None,
        },
        "right_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_right",
            "artifact_id": None,
        },
        "rows": rows,
        "scope_absolute_delta": 200.0,
        "scope_relative_delta": 0.25,
        "scope_direction": "increase",
        "unexplained_absolute_delta": 0.0,
        "unexplained_share": 0.0,
        "unexplained_reason": "rounding",
        "attribution": {"status": "attributable", "issues": []},
        "analytical_metadata": {},
    }


# ===========================================================================
# CompareArtifactExtractor — scalar_delta
# ===========================================================================


class TestCompareScalarDelta(unittest.TestCase):
    def _extract(self, **kwargs: Any) -> Any:
        return _COMPARE_EXTRACTOR.extract(
            artifact_id=_ART_ID,
            artifact_payload=_scalar_delta_payload(**kwargs),
            step_ref=_STEP_REF,
            session_id=_SESSION,
        )

    def test_returns_exactly_one_finding(self) -> None:
        result = self._extract()
        self.assertEqual(result["finding_count"], 1)
        self.assertEqual(len(result["findings"]), 1)

    def test_finding_type_is_delta(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["finding_type"], "delta")

    def test_artifact_id_propagated(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["artifact_id"], _ART_ID)

    def test_step_ref_propagated(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["step_ref"], _STEP_REF)

    def test_delta_kind_is_scalar_delta(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["payload"]["delta_kind"], "scalar_delta")

    def test_payload_values_correct(self) -> None:
        result = self._extract(
            left_value=1000.0,
            right_value=800.0,
            absolute_delta=200.0,
            relative_delta=0.25,
        )
        p = result["findings"][0]["payload"]
        self.assertEqual(p["left_value"], 1000.0)
        self.assertEqual(p["right_value"], 800.0)
        self.assertEqual(p["absolute_delta"], 200.0)
        self.assertEqual(p["relative_delta"], 0.25)

    def test_direction_preserved(self) -> None:
        for d in ("increase", "decrease", "flat", "undefined"):
            with self.subTest(direction=d):
                result = self._extract(direction=d)
                self.assertEqual(result["findings"][0]["payload"]["direction"], d)

    def test_comparability_summary_propagated(self) -> None:
        result = _COMPARE_EXTRACTOR.extract(
            artifact_id=_ART_ID,
            artifact_payload={
                **_scalar_delta_payload(),
                "comparability": {
                    "status": "needs_attention",
                    "issues": [
                        {
                            "code": "alignment_coverage_insufficient",
                            "severity": "warning",
                            "message": "coverage warning",
                        }
                    ],
                },
                "resolved_input_summary": {
                    **_scalar_delta_payload()["resolved_input_summary"],
                    "calendar_alignment": _calendar_alignment_summary(aligned_ratio=0.8),
                },
            },
            step_ref=_STEP_REF,
            session_id=_SESSION,
        )
        payload = result["findings"][0]["payload"]
        self.assertEqual(payload["comparability"]["status"], "needs_attention")
        self.assertEqual(
            payload["comparability"]["issues"][0]["code"], "alignment_coverage_insufficient"
        )
        self.assertEqual(payload["calendar_alignment"]["policy_ref"], "calendar_policy.holiday_yoy")

    def test_invalid_direction_normalised_to_undefined(self) -> None:
        payload = _scalar_delta_payload(direction="unknown_dir")
        result = _COMPARE_EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["payload"]["direction"], "undefined")

    def test_presence_is_both_for_scalar(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["payload"]["presence"], "both")

    def test_unit_propagated(self) -> None:
        result = self._extract(unit="usd")
        self.assertEqual(result["findings"][0]["payload"]["unit"], "usd")

    def test_unit_none_when_absent(self) -> None:
        result = self._extract(unit=None)
        self.assertIsNone(result["findings"][0]["payload"]["unit"])

    def test_metric_in_subject(self) -> None:
        result = self._extract(metric="revenue")
        self.assertEqual(result["findings"][0]["subject"]["metric"], "revenue")

    def test_analysis_axis_is_scalar(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["subject"]["analysis_axis"], "scalar")

    def test_subject_slice_from_left_scope(self) -> None:
        result = self._extract(left_scope={"region": "APAC"})
        self.assertEqual(result["findings"][0]["subject"]["slice"], {"region": "APAC"})

    def test_observed_window_from_left_time_scope(self) -> None:
        ts = {"kind": "range", "start": "2024-02-01", "end": "2024-02-08"}
        result = self._extract(left_time_scope=ts)
        self.assertEqual(result["findings"][0]["observed_window"], ts)

    def test_canonical_item_key_is_result(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["provenance"]["canonical_item_key"], "result")

    def test_finding_id_stability(self) -> None:
        result1 = self._extract()
        result2 = self._extract()
        self.assertEqual(result1["findings"][0]["finding_id"], result2["findings"][0]["finding_id"])

    def test_finding_id_correct_formula(self) -> None:
        result = self._extract()
        expected_key, _ = make_item_identity("result")
        expected_id = make_finding_id(_ART_ID, "delta", expected_key)
        self.assertEqual(result["findings"][0]["finding_id"], expected_id)

    def test_finding_id_stable_helper(self) -> None:
        # scalar_delta canonical collection is "result" with no key
        assert_finding_id_stable(self, _ART_ID, "delta", "result")

    def test_extractor_metadata(self) -> None:
        result = self._extract()
        self.assertEqual(result["extractor_name"], "compare_artifact_v1")
        self.assertEqual(result["extractor_version"], "1.0.0")
        self.assertEqual(result["artifact_schema_version"], "v1")

    def test_left_ref_artifact_id_is_empty_v1_limitation(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["payload"]["left_ref"]["artifact_id"], "")

    def test_right_ref_artifact_id_is_empty_v1_limitation(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["payload"]["right_ref"]["artifact_id"], "")

    def test_validate_for_commit_passes(self) -> None:
        result = self._extract()
        validate_for_commit("compare", result)  # must not raise

    def test_null_left_value_accepted(self) -> None:
        result = self._extract(left_value=None)
        self.assertIsNone(result["findings"][0]["payload"]["left_value"])

    def test_null_right_value_accepted(self) -> None:
        result = self._extract(right_value=None)
        self.assertIsNone(result["findings"][0]["payload"]["right_value"])


# ===========================================================================
# CompareArtifactExtractor — segmented_delta
# ===========================================================================


class TestCompareSegmentedDelta(unittest.TestCase):
    def _extract(self, rows: list[dict[str, Any]] | None = None) -> Any:
        return _COMPARE_EXTRACTOR.extract(
            artifact_id=_ART_ID,
            artifact_payload=_segmented_delta_payload(rows=rows),
            step_ref=_STEP_REF,
            session_id=_SESSION,
        )

    def test_two_rows_produce_two_findings(self) -> None:
        result = self._extract()
        self.assertEqual(result["finding_count"], 2)
        self.assertEqual(len(result["findings"]), 2)

    def test_all_findings_are_delta_type(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["finding_type"], "delta")

    def test_delta_kind_is_segmented_delta(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["payload"]["delta_kind"], "segmented_delta")

    def test_analysis_axis_is_segment(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["subject"]["analysis_axis"], "segment")

    def test_subject_slice_from_row_keys(self) -> None:
        rows = [
            {
                "keys": {"country": "DE"},
                "left_value": 100.0,
                "right_value": 80.0,
                "absolute_delta": 20.0,
                "relative_delta": 0.25,
                "direction": "increase",
                "presence": "both",
            }
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["subject"]["slice"], {"country": "DE"})

    def test_presence_propagated(self) -> None:
        rows = [
            {
                "keys": {"cat": "A"},
                "left_value": 100.0,
                "right_value": None,
                "absolute_delta": 100.0,
                "relative_delta": None,
                "direction": "undefined",
                "presence": "left_only",
            },
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["presence"], "left_only")

    def test_right_only_presence_preserved(self) -> None:
        rows = [
            {
                "keys": {"cat": "B"},
                "left_value": None,
                "right_value": 50.0,
                "absolute_delta": -50.0,
                "relative_delta": None,
                "direction": "undefined",
                "presence": "right_only",
            },
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["presence"], "right_only")

    def test_invalid_presence_normalised_to_none(self) -> None:
        rows = [
            {
                "keys": {"cat": "C"},
                "left_value": 1.0,
                "right_value": 1.0,
                "absolute_delta": 0.0,
                "relative_delta": 0.0,
                "direction": "flat",
                "presence": "unknown_val",
            }
        ]
        result = self._extract(rows=rows)
        self.assertIsNone(result["findings"][0]["payload"]["presence"])

    def test_finding_ids_are_distinct(self) -> None:
        result = self._extract()
        ids = [f["finding_id"] for f in result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_segment_key_stability_across_replay(self) -> None:
        result1 = self._extract()
        result2 = self._extract()
        ids1 = sorted(f["finding_id"] for f in result1["findings"])
        ids2 = sorted(f["finding_id"] for f in result2["findings"])
        self.assertEqual(ids1, ids2)

    def test_segment_canonical_key_uses_sorted_kv(self) -> None:
        """Segment canonical_item_key is derived from sorted dimension KV pairs."""
        rows_ab = [
            {
                "keys": {"a": "1", "b": "2"},
                "left_value": 1.0,
                "right_value": 1.0,
                "absolute_delta": 0.0,
                "relative_delta": 0.0,
                "direction": "flat",
                "presence": "both",
            }
        ]
        rows_ba = [
            {
                "keys": {"b": "2", "a": "1"},
                "left_value": 1.0,
                "right_value": 1.0,
                "absolute_delta": 0.0,
                "relative_delta": 0.0,
                "direction": "flat",
                "presence": "both",
            }
        ]
        result_ab = self._extract(rows=rows_ab)
        result_ba = self._extract(rows=rows_ba)
        self.assertEqual(
            result_ab["findings"][0]["finding_id"],
            result_ba["findings"][0]["finding_id"],
        )

    def test_projection_order_excluded(self) -> None:
        """Stable segment key beats a projection rank index."""
        # The stable key for {"region": "APAC"} is "region=APAC"
        assert_projection_order_excluded(self, _ART_ID, "delta", "rows", "region=APAC")

    def test_unit_shared_across_findings(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["payload"]["unit"], "usd")

    def test_observed_window_from_resolved_summary(self) -> None:
        """segmented_delta findings carry observed_window from resolved_input_summary."""
        result = self._extract()
        ts = {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"}
        for f in result["findings"]:
            self.assertEqual(f["observed_window"], ts)

    def test_finding_id_stability_helper(self) -> None:
        # Segmented delta canonical collection is "rows" with a stable segment key
        assert_finding_id_stable(self, _ART_ID, "delta", "rows", key="country=US")

    def test_validate_for_commit_passes_nonempty(self) -> None:
        result = self._extract()
        validate_for_commit("compare", result)  # must not raise

    def test_validate_for_commit_fails_empty_rows(self) -> None:
        result = self._extract(rows=[])
        with self.assertRaises(FamilyEmptyError):
            validate_for_commit("compare", result)


# ===========================================================================
# CompareArtifactExtractor — edge cases
# ===========================================================================


class TestCompareEdgeCases(unittest.TestCase):
    def test_unknown_comparison_type_raises(self) -> None:
        payload = _scalar_delta_payload()
        payload["comparison_type"] = "unknown_type"
        with self.assertRaises(ValueError, msg="unknown comparison_type"):
            _COMPARE_EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)

    def test_missing_comparison_type_raises(self) -> None:
        payload = _scalar_delta_payload()
        del payload["comparison_type"]
        with self.assertRaises(ValueError):
            _COMPARE_EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)

    def test_finding_count_matches_len_findings(self) -> None:
        for rows_count in (1, 3, 5):
            rows = [
                {
                    "keys": {"k": str(i)},
                    "left_value": float(i),
                    "right_value": 1.0,
                    "absolute_delta": float(i) - 1.0,
                    "relative_delta": None,
                    "direction": "increase",
                    "presence": "both",
                }
                for i in range(rows_count)
            ]
            result = _COMPARE_EXTRACTOR.extract(
                _ART_ID, _segmented_delta_payload(rows=rows), _STEP_REF, _SESSION
            )
            self.assertEqual(result["finding_count"], len(result["findings"]))


# ===========================================================================
# CompareArtifactExtractor — registry
# ===========================================================================


class TestCompareRegistration(unittest.TestCase):
    def test_registered_under_compare_artifact_v1(self) -> None:
        extractor = default_finding_registry.find("compare_artifact", "v1")
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, CompareArtifactExtractor)

    def test_null_version_normalised_to_v1(self) -> None:
        extractor = default_finding_registry.find("compare_artifact", None)
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, CompareArtifactExtractor)

    def test_snapshot_contains_compare_entry(self) -> None:
        entries = {e["artifact_type"]: e for e in default_finding_registry.snapshot()}
        self.assertIn("compare_artifact", entries)
        entry = entries["compare_artifact"]
        self.assertEqual(entry["artifact_schema_version"], "v1")
        self.assertEqual(entry["family"], "compare")
        self.assertEqual(entry["extractor_name"], "compare_artifact_v1")
        self.assertEqual(entry["finding_schema_version"], "v1")


# ===========================================================================
# DecomposeArtifactExtractor — rows
# ===========================================================================


class TestDecomposeRows(unittest.TestCase):
    def _extract(self, **kwargs: Any) -> Any:
        return _DECOMPOSE_EXTRACTOR.extract(
            artifact_id=_DECOMP_ART_ID,
            artifact_payload=_decompose_payload(**kwargs),
            step_ref=_DECOMP_STEP_REF,
            session_id=_SESSION,
        )

    def test_two_rows_produce_two_findings(self) -> None:
        result = self._extract()
        self.assertEqual(result["finding_count"], 2)
        self.assertEqual(len(result["findings"]), 2)

    def test_finding_type_is_decomposition_item(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["finding_type"], "decomposition_item")

    def test_artifact_id_propagated(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["artifact_id"], _DECOMP_ART_ID)

    def test_step_ref_propagated(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["step_ref"], _DECOMP_STEP_REF)

    def test_dimension_in_payload(self) -> None:
        result = self._extract(dimension="platform")
        for f in result["findings"]:
            self.assertEqual(f["payload"]["dimension"], "platform")

    def test_keys_dict_has_dimension_key(self) -> None:
        result = self._extract(dimension="platform")
        for f in result["findings"]:
            keys = f["payload"]["keys"]
            self.assertIn("platform", keys)

    def test_keys_dict_value_matches_row_key(self) -> None:
        rows = [
            {
                "key": "ios",
                "left_value": 600.0,
                "right_value": 500.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.5,
                "direction": "increase",
                "presence": "both",
            },
        ]
        result = self._extract(dimension="platform", rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["keys"], {"platform": "ios"})

    def test_contribution_value_from_absolute_contribution(self) -> None:
        rows = [
            {
                "key": "ios",
                "left_value": 600.0,
                "right_value": 500.0,
                "absolute_contribution": 123.0,
                "contribution_share": 0.6,
                "direction": "increase",
                "presence": "both",
            }
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["contribution_value"], 123.0)

    def test_contribution_share_propagated(self) -> None:
        rows = [
            {
                "key": "ios",
                "left_value": 600.0,
                "right_value": 500.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.75,
                "direction": "increase",
                "presence": "both",
            }
        ]
        result = self._extract(rows=rows)
        self.assertAlmostEqual(result["findings"][0]["payload"]["contribution_share"], 0.75)

    def test_rank_is_one_based_and_sequential(self) -> None:
        result = self._extract()
        ranks = [f["payload"]["rank"] for f in result["findings"]]
        self.assertEqual(ranks, [1, 2])

    def test_rank_preserves_artifact_sort_order(self) -> None:
        """First row in artifact gets rank 1, second gets rank 2."""
        rows = [
            {
                "key": "android",
                "left_value": 400.0,
                "right_value": 300.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.5,
                "direction": "increase",
                "presence": "both",
            },
            {
                "key": "ios",
                "left_value": 600.0,
                "right_value": 500.0,
                "absolute_contribution": 100.0,
                "contribution_share": 0.5,
                "direction": "increase",
                "presence": "both",
            },
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["rank"], 1)
        self.assertEqual(result["findings"][1]["payload"]["rank"], 2)
        # Keys for rank 1 should be android (first row)
        self.assertEqual(result["findings"][0]["payload"]["keys"]["platform"], "android")

    def test_direction_propagated(self) -> None:
        rows = [
            {
                "key": "x",
                "left_value": 1.0,
                "right_value": 2.0,
                "absolute_contribution": -1.0,
                "contribution_share": -0.5,
                "direction": "decrease",
                "presence": "both",
            }
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["direction"], "decrease")

    def test_invalid_direction_normalised_to_undefined(self) -> None:
        rows = [
            {
                "key": "x",
                "left_value": 1.0,
                "right_value": 1.0,
                "absolute_contribution": 0.0,
                "contribution_share": 0.0,
                "direction": "sideways",
                "presence": "both",
            }
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["findings"][0]["payload"]["direction"], "undefined")

    def test_analysis_axis_is_decomposition(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["subject"]["analysis_axis"], "decomposition")

    def test_subject_metric_propagated(self) -> None:
        result = self._extract(metric="revenue")
        for f in result["findings"]:
            self.assertEqual(f["subject"]["metric"], "revenue")

    def test_subject_slice_has_dimension_key(self) -> None:
        result = self._extract(dimension="platform")
        for f in result["findings"]:
            self.assertIn("platform", f["subject"]["slice"])

    def test_finding_ids_are_distinct(self) -> None:
        result = self._extract()
        ids = [f["finding_id"] for f in result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_finding_id_stability_across_replay(self) -> None:
        result1 = self._extract()
        result2 = self._extract()
        ids1 = sorted(f["finding_id"] for f in result1["findings"])
        ids2 = sorted(f["finding_id"] for f in result2["findings"])
        self.assertEqual(ids1, ids2)

    def test_finding_id_stability_helper(self) -> None:
        # Decompose canonical collection is "rows" with a stable dimension:key
        assert_finding_id_stable(
            self, _DECOMP_ART_ID, "decomposition_item", "rows", key="platform:ios"
        )

    def test_none_key_handled_gracefully(self) -> None:
        rows = [
            {
                "key": None,
                "left_value": 100.0,
                "right_value": 80.0,
                "absolute_contribution": 20.0,
                "contribution_share": 0.1,
                "direction": "increase",
                "presence": "both",
            }
        ]
        result = self._extract(rows=rows)
        self.assertEqual(result["finding_count"], 1)
        # None key should map to empty string in stable_key
        self.assertIn("platform:", result["findings"][0]["provenance"]["canonical_item_key"])

    def test_canonical_item_key_includes_dimension_and_key(self) -> None:
        rows = [
            {
                "key": "ios",
                "left_value": 1.0,
                "right_value": 1.0,
                "absolute_contribution": 0.0,
                "contribution_share": 0.0,
                "direction": "flat",
                "presence": "both",
            }
        ]
        result = self._extract(dimension="platform", rows=rows)
        cik = result["findings"][0]["provenance"]["canonical_item_key"]
        # Should contain both the escaped dimension and key
        self.assertIn("platform", cik)
        self.assertIn("ios", cik)

    def test_extractor_metadata(self) -> None:
        result = self._extract()
        self.assertEqual(result["extractor_name"], "decompose_artifact_v1")
        self.assertEqual(result["extractor_version"], "1.0.0")
        self.assertEqual(result["artifact_schema_version"], "v1")

    def test_validate_for_commit_passes_nonempty(self) -> None:
        result = self._extract()
        validate_for_commit("decompose", result)  # must not raise

    def test_validate_for_commit_fails_empty_rows(self) -> None:
        result = self._extract(rows=[])
        with self.assertRaises(FamilyEmptyError):
            validate_for_commit("decompose", result)


# ===========================================================================
# DecomposeArtifactExtractor — scope_delta_ref
# ===========================================================================


class TestDecomposeScopeDeltaRef(unittest.TestCase):
    def test_scope_delta_ref_session_id_matches_session(self) -> None:
        result = _DECOMPOSE_EXTRACTOR.extract(
            _DECOMP_ART_ID, _decompose_payload(), _DECOMP_STEP_REF, _SESSION
        )
        for f in result["findings"]:
            self.assertEqual(f["payload"]["scope_delta_ref"]["session_id"], _SESSION)

    def test_scope_delta_ref_finding_id_deterministic(self) -> None:
        """scope_delta_ref.finding_id == make_finding_id(compare_artifact_id, "delta", "result")."""
        compare_art_id = "art_cmp_specific_001"
        result = _DECOMPOSE_EXTRACTOR.extract(
            _DECOMP_ART_ID,
            _decompose_payload(compare_artifact_id=compare_art_id),
            _DECOMP_STEP_REF,
            _SESSION,
        )
        expected_key, _ = make_item_identity("result")
        expected_fid = make_finding_id(compare_art_id, "delta", expected_key)
        for f in result["findings"]:
            self.assertEqual(f["payload"]["scope_delta_ref"]["finding_id"], expected_fid)

    def test_scope_delta_ref_is_consistent_across_all_findings(self) -> None:
        result = _DECOMPOSE_EXTRACTOR.extract(
            _DECOMP_ART_ID, _decompose_payload(), _DECOMP_STEP_REF, _SESSION
        )
        refs = [f["payload"]["scope_delta_ref"] for f in result["findings"]]
        self.assertTrue(all(r == refs[0] for r in refs))

    def test_scope_delta_ref_changes_with_compare_artifact_id(self) -> None:
        result_a = _DECOMPOSE_EXTRACTOR.extract(
            _DECOMP_ART_ID,
            _decompose_payload(compare_artifact_id="art_A"),
            _DECOMP_STEP_REF,
            _SESSION,
        )
        result_b = _DECOMPOSE_EXTRACTOR.extract(
            _DECOMP_ART_ID,
            _decompose_payload(compare_artifact_id="art_B"),
            _DECOMP_STEP_REF,
            _SESSION,
        )
        ref_a = result_a["findings"][0]["payload"]["scope_delta_ref"]["finding_id"]
        ref_b = result_b["findings"][0]["payload"]["scope_delta_ref"]["finding_id"]
        self.assertNotEqual(ref_a, ref_b)

    def test_scope_delta_ref_session_id_tracks_session_arg(self) -> None:
        other_session = "sess_other_999"
        result = _DECOMPOSE_EXTRACTOR.extract(
            _DECOMP_ART_ID, _decompose_payload(), _DECOMP_STEP_REF, other_session
        )
        for f in result["findings"]:
            self.assertEqual(f["payload"]["scope_delta_ref"]["session_id"], other_session)


# ===========================================================================
# DecomposeArtifactExtractor — error cases
# ===========================================================================


class TestDecomposeErrorCases(unittest.TestCase):
    def test_missing_compare_artifact_id_raises(self) -> None:
        payload = _decompose_payload()
        payload["compare_ref"]["artifact_id"] = None
        with self.assertRaises(ValueError, msg="compare_ref.artifact_id is required"):
            _DECOMPOSE_EXTRACTOR.extract(_DECOMP_ART_ID, payload, _DECOMP_STEP_REF, _SESSION)

    def test_empty_compare_artifact_id_raises(self) -> None:
        payload = _decompose_payload()
        payload["compare_ref"]["artifact_id"] = ""
        with self.assertRaises(ValueError):
            _DECOMPOSE_EXTRACTOR.extract(_DECOMP_ART_ID, payload, _DECOMP_STEP_REF, _SESSION)

    def test_missing_dimension_raises(self) -> None:
        payload = _decompose_payload()
        payload["dimension"] = ""
        with self.assertRaises(ValueError, msg="dimension is required"):
            _DECOMPOSE_EXTRACTOR.extract(_DECOMP_ART_ID, payload, _DECOMP_STEP_REF, _SESSION)

    def test_segmented_delta_compare_ref_raises(self) -> None:
        payload = _decompose_payload()
        payload["compare_ref"]["comparison_type"] = "segmented_delta"
        with self.assertRaises(ValueError, msg="comparison_type segmented_delta"):
            _DECOMPOSE_EXTRACTOR.extract(_DECOMP_ART_ID, payload, _DECOMP_STEP_REF, _SESSION)

    def test_finding_count_matches_len_findings(self) -> None:
        for rows_count in (1, 3, 5):
            rows = [
                {
                    "key": str(i),
                    "left_value": float(i),
                    "right_value": 1.0,
                    "absolute_contribution": float(i) - 1.0,
                    "contribution_share": None,
                    "direction": "increase",
                    "presence": "both",
                }
                for i in range(1, rows_count + 1)
            ]
            result = _DECOMPOSE_EXTRACTOR.extract(
                _DECOMP_ART_ID, _decompose_payload(rows=rows), _DECOMP_STEP_REF, _SESSION
            )
            self.assertEqual(result["finding_count"], len(result["findings"]))


# ===========================================================================
# DecomposeArtifactExtractor — registry
# ===========================================================================


class TestDecomposeRegistration(unittest.TestCase):
    def test_registered_under_delta_decomposition_v1(self) -> None:
        extractor = default_finding_registry.find("delta_decomposition", "v1")
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, DecomposeArtifactExtractor)

    def test_null_version_normalised_to_v1(self) -> None:
        extractor = default_finding_registry.find("delta_decomposition", None)
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, DecomposeArtifactExtractor)

    def test_snapshot_contains_decompose_entry(self) -> None:
        entries = {e["artifact_type"]: e for e in default_finding_registry.snapshot()}
        self.assertIn("delta_decomposition", entries)
        entry = entries["delta_decomposition"]
        self.assertEqual(entry["artifact_schema_version"], "v1")
        self.assertEqual(entry["family"], "decompose")
        self.assertEqual(entry["extractor_name"], "decompose_artifact_v1")
        self.assertEqual(entry["finding_schema_version"], "v1")


if __name__ == "__main__":
    unittest.main()
