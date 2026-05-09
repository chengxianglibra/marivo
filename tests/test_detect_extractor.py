"""Tests for the detect artifact → anomaly_candidate finding extractor (Phase 4d-2).

Covers acceptance criteria:
- Empty candidates list → 0 findings (success-empty, D4 detect allows).
- Non-empty candidates → 1 finding per candidate with correct payload fields.
- finding_id is stable across replay for each candidate.
- window.start used as stable candidate key; index fallback when window absent.
- analysis_axis = "time" for time-bucket candidates; "segment" / "scalar" for others.
- DetectArtifactExtractor is registered in default_finding_registry under
  ("anomaly_candidates", "v1"), including NULL version normalisation.
- validate_for_commit("detect", result) passes for both empty and non-empty results.
"""

# ruff: noqa: I001
from __future__ import annotations

import unittest
from typing import Any

from marivo.evidence_engine.canonical_finding import StepRef

# finding_extractor_registry must be imported before detect_extractor so the
# bootstrap runs cleanly (detect_extractor subclasses FindingExtractor from
# this module; if detect_extractor were loaded first it would trigger the
# bootstrap while the module is only partially initialised → circular import).
from marivo.evidence_engine.finding_extractor_registry import (
    default_finding_registry,
    validate_for_commit,
)
from marivo.evidence_engine.detect_extractor import DetectArtifactExtractor
from tests.finding_identity_testutil import (
    assert_finding_id_stable,
    assert_projection_order_excluded,
    assert_stable_key_beats_index,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ART_ID = "art_detect_test001"
_SESSION = "sess_det_test"
_STEP_ID = "step_det_001"
_STEP_REF: StepRef = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="detect")

_EXTRACTOR = DetectArtifactExtractor()


def _candidate(
    window_start: str = "2024-01-05",
    window_end: str = "2024-01-06",
    observed_value: float | None = 320.0,
    expected_value: float | None = 100.0,
    deviation_abs: float | None = 220.0,
    deviation_pct: float | None = 2.2,
    candidate_score: float | None = 3.5,
    flag_level: str | None = "high",
    candidate_slice: Any = None,
) -> dict[str, Any]:
    return {
        "window": {"start": window_start, "end": window_end},
        "slice": candidate_slice,
        "observed_value": observed_value,
        "expected_value": expected_value,
        "deviation_abs": deviation_abs,
        "deviation_pct": deviation_pct,
        "candidate_score": candidate_score,
        "flag_level": flag_level,
        "direction": "up",
        "candidate_ref": {
            "artifact_ref": {
                "session_id": _SESSION,
                "step_id": _STEP_ID,
                "step_type": "detect",
                "artifact_id": None,
            },
            "item_ref": {"collection": "candidates", "index": 0, "key": None},
        },
    }


def _artifact(
    candidates: list[dict[str, Any]] | None = None,
    metric: str = "daily_users",
    scope: dict[str, Any] | None = None,
    total_candidate_count: int | None = None,
    grain: str | None = "day",
) -> dict[str, Any]:
    if candidates is None:
        candidates = [_candidate()]
    if total_candidate_count is None:
        total_candidate_count = len(candidates)
    return {
        "artifact_type": "anomaly_candidates",
        "artifact_schema_version": "v1",
        "metric": metric,
        "scope": scope,
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        "granularity": grain,
        "candidates": candidates,
        "scan_summary": {
            "total_candidate_count": total_candidate_count,
        },
        "analytical_metadata": {
            "baseline_method": {
                "patterns": ["point_anomaly"],
                "methods": {"point_anomaly": "scan_window_zscore"},
            },
        },
    }


# ---------------------------------------------------------------------------
# TestDetectExtractorEmpty — success-empty semantics
# ---------------------------------------------------------------------------


class TestDetectExtractorEmpty(unittest.TestCase):
    def setUp(self) -> None:
        self.result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[]), _STEP_REF, _SESSION)

    def test_produces_zero_findings(self) -> None:
        self.assertEqual(len(self.result["findings"]), 0)

    def test_finding_count_is_zero(self) -> None:
        self.assertEqual(self.result["finding_count"], 0)

    def test_finding_count_matches_findings(self) -> None:
        self.assertEqual(self.result["finding_count"], len(self.result["findings"]))

    def test_extractor_name_correct(self) -> None:
        self.assertEqual(self.result["extractor_name"], "detect_artifact_v1")

    def test_extractor_version_present(self) -> None:
        self.assertEqual(self.result["extractor_version"], "1.0.0")

    def test_artifact_schema_version_correct(self) -> None:
        self.assertEqual(self.result["artifact_schema_version"], "v1")

    def test_validate_for_commit_passes_empty(self) -> None:
        # D4: detect allows success-empty
        validate_for_commit("detect", self.result)

    def test_none_candidates_treated_as_empty(self) -> None:
        payload = _artifact(candidates=[])
        payload["candidates"] = None
        result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertEqual(result["finding_count"], 0)

    def test_missing_candidates_key_treated_as_empty(self) -> None:
        payload: dict[str, Any] = {
            "artifact_type": "anomaly_candidates",
            "artifact_schema_version": "v1",
            "metric": "daily_users",
        }
        result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertEqual(result["finding_count"], 0)


# ---------------------------------------------------------------------------
# TestDetectExtractorSingleCandidate — basic mapping
# ---------------------------------------------------------------------------


class TestDetectExtractorSingleCandidate(unittest.TestCase):
    def setUp(self) -> None:
        self.result = _EXTRACTOR.extract(_ART_ID, _artifact(), _STEP_REF, _SESSION)

    def test_produces_one_finding(self) -> None:
        self.assertEqual(len(self.result["findings"]), 1)

    def test_finding_count_matches_findings(self) -> None:
        self.assertEqual(self.result["finding_count"], 1)

    def test_finding_type_is_anomaly_candidate(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["finding_type"], "anomaly_candidate")

    def test_finding_id_starts_with_fnd(self) -> None:
        f = self.result["findings"][0]
        self.assertTrue(f["finding_id"].startswith("fnd_"))

    def test_artifact_id_recorded(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["artifact_id"], _ART_ID)

    def test_step_ref_recorded(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["step_ref"], _STEP_REF)

    def test_subject_metric_correct(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["subject"]["metric"], "daily_users")

    def test_subject_analysis_axis_is_time(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["subject"]["analysis_axis"], "time")

    def test_subject_slice_empty_when_no_scope(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["subject"]["slice"], {})

    def test_observed_window_range(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["observed_window"]["kind"], "range")
        self.assertEqual(f["observed_window"]["start"], "2024-01-05")
        self.assertEqual(f["observed_window"]["end"], "2024-01-06")

    def test_canonical_item_key_uses_window_start(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["provenance"]["canonical_item_key"], "candidates:2024-01-05")

    def test_artifact_item_ref_collection_is_candidates(self) -> None:
        f = self.result["findings"][0]
        ref = f["provenance"]["artifact_item_ref"]
        self.assertEqual(ref["collection"], "candidates")

    def test_artifact_item_ref_key_is_window_start(self) -> None:
        f = self.result["findings"][0]
        ref = f["provenance"]["artifact_item_ref"]
        self.assertEqual(ref["key"], "2024-01-05")

    def test_artifact_item_ref_index_is_none_when_key_present(self) -> None:
        f = self.result["findings"][0]
        ref = f["provenance"]["artifact_item_ref"]
        self.assertIsNone(ref["index"])

    def test_provenance_extractor_name(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["provenance"]["extractor_name"], "detect_artifact_v1")

    def test_provenance_projection_ref_is_none(self) -> None:
        f = self.result["findings"][0]
        self.assertIsNone(f["provenance"]["projection_ref"])


# ---------------------------------------------------------------------------
# TestDetectExtractorPayloadFields — AnomalyCandidatePayload mapping
# ---------------------------------------------------------------------------


class TestDetectExtractorPayloadFields(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = _candidate(
            observed_value=320.0,
            expected_value=100.0,
            deviation_abs=220.0,
            deviation_pct=2.2,
            candidate_score=3.5,
            flag_level="high",
        )
        self.result = _EXTRACTOR.extract(
            _ART_ID, _artifact(candidates=[self.candidate]), _STEP_REF, _SESSION
        )
        self.payload = self.result["findings"][0]["payload"]

    def test_actual_value_mapped(self) -> None:
        self.assertAlmostEqual(self.payload["actual_value"], 320.0)

    def test_expected_value_mapped(self) -> None:
        self.assertAlmostEqual(self.payload["expected_value"], 100.0)

    def test_deviation_absolute_mapped(self) -> None:
        self.assertAlmostEqual(self.payload["deviation_absolute"], 220.0)

    def test_deviation_relative_mapped(self) -> None:
        self.assertAlmostEqual(self.payload["deviation_relative"], 2.2)

    def test_score_mapped(self) -> None:
        self.assertAlmostEqual(self.payload["score"], 3.5)

    def test_flag_level_high(self) -> None:
        self.assertEqual(self.payload["flag_level"], "high")

    def test_candidate_ref_artifact_id(self) -> None:
        self.assertEqual(self.payload["candidate_ref"]["artifact_id"], _ART_ID)

    def test_candidate_ref_item_ref_collection(self) -> None:
        self.assertEqual(self.payload["candidate_ref"]["item_ref"]["collection"], "candidates")

    def test_candidate_ref_item_ref_key(self) -> None:
        self.assertEqual(self.payload["candidate_ref"]["item_ref"]["key"], "2024-01-05")

    def test_flag_level_medium(self) -> None:
        c = _candidate(flag_level="medium")
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["payload"]["flag_level"], "medium")

    def test_flag_level_low(self) -> None:
        c = _candidate(flag_level="low")
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["payload"]["flag_level"], "low")

    def test_invalid_flag_level_becomes_none(self) -> None:
        c = _candidate(flag_level="critical")
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][0]["payload"]["flag_level"])

    def test_none_flag_level_stays_none(self) -> None:
        c = _candidate(flag_level=None)
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][0]["payload"]["flag_level"])

    def test_none_numeric_fields_map_to_none(self) -> None:
        c = _candidate(
            observed_value=None,
            expected_value=None,
            deviation_abs=None,
            deviation_pct=None,
            candidate_score=None,
        )
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        p = result["findings"][0]["payload"]
        self.assertIsNone(p["actual_value"])
        self.assertIsNone(p["expected_value"])
        self.assertIsNone(p["deviation_absolute"])
        self.assertIsNone(p["deviation_relative"])
        self.assertIsNone(p["score"])


# ---------------------------------------------------------------------------
# TestDetectExtractorMultipleCandidates — multi-candidate artifact
# ---------------------------------------------------------------------------


class TestDetectExtractorMultipleCandidates(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = [
            _candidate(window_start="2024-01-03", window_end="2024-01-04", candidate_score=4.1),
            _candidate(window_start="2024-01-05", window_end="2024-01-06", candidate_score=3.5),
            _candidate(window_start="2024-01-07", window_end="2024-01-08", candidate_score=2.8),
        ]
        self.result = _EXTRACTOR.extract(
            _ART_ID, _artifact(candidates=self.candidates), _STEP_REF, _SESSION
        )

    def test_produces_three_findings(self) -> None:
        self.assertEqual(len(self.result["findings"]), 3)

    def test_finding_count_matches_findings(self) -> None:
        self.assertEqual(self.result["finding_count"], 3)

    def test_each_candidate_has_distinct_finding_id(self) -> None:
        ids = [f["finding_id"] for f in self.result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_canonical_keys_are_window_start_based(self) -> None:
        keys = [f["provenance"]["canonical_item_key"] for f in self.result["findings"]]
        self.assertEqual(
            keys,
            [
                "candidates:2024-01-03",
                "candidates:2024-01-05",
                "candidates:2024-01-07",
            ],
        )

    def test_scope_propagated_to_all_findings(self) -> None:
        scope = {"region": "US"}
        result = _EXTRACTOR.extract(
            _ART_ID,
            _artifact(candidates=self.candidates, scope=scope),
            _STEP_REF,
            _SESSION,
        )
        for f in result["findings"]:
            self.assertEqual(f["subject"]["slice"], {"region": "US"})


# ---------------------------------------------------------------------------
# TestDetectExtractorFindingId — stability and key priority
# ---------------------------------------------------------------------------


class TestDetectExtractorFindingId(unittest.TestCase):
    def test_finding_id_stable_on_replay(self) -> None:
        payload = _artifact()
        result1 = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        result2 = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertEqual(result1["findings"][0]["finding_id"], result2["findings"][0]["finding_id"])

    def test_finding_id_stable_helper(self) -> None:
        assert_finding_id_stable(self, _ART_ID, "anomaly_candidate", "candidates", key="2024-01-05")

    def test_stable_key_beats_index(self) -> None:
        # If we supply both key and index, key must win (D2 priority).
        assert_stable_key_beats_index(self, "candidates", "2024-01-05", 0)

    def test_projection_order_excluded(self) -> None:
        assert_projection_order_excluded(
            self, _ART_ID, "anomaly_candidate", "candidates", "2024-01-05"
        )

    def test_finding_id_differs_across_candidates(self) -> None:
        c1 = _candidate(window_start="2024-01-05", window_end="2024-01-06")
        c2 = _candidate(window_start="2024-01-06", window_end="2024-01-07")
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c1, c2]), _STEP_REF, _SESSION)
        self.assertNotEqual(
            result["findings"][0]["finding_id"],
            result["findings"][1]["finding_id"],
        )

    def test_index_fallback_when_window_missing(self) -> None:
        # A candidate with no window falls back to index-based identity.
        c: dict[str, Any] = {
            "window": None,
            "slice": None,
            "observed_value": 50.0,
            "expected_value": 20.0,
            "deviation_abs": 30.0,
            "deviation_pct": 1.5,
            "candidate_score": 2.5,
            "flag_level": "medium",
            "direction": "up",
        }
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        f = result["findings"][0]
        # Should use index=0, so canonical_item_key = "candidates:0"
        self.assertEqual(f["provenance"]["canonical_item_key"], "candidates:0")
        self.assertIsNone(f["provenance"]["artifact_item_ref"]["key"])
        self.assertEqual(f["provenance"]["artifact_item_ref"]["index"], 0)

    def test_empty_window_start_falls_back_to_index(self) -> None:
        c: dict[str, Any] = {
            "window": {"start": "", "end": ""},
            "slice": None,
            "observed_value": 50.0,
            "expected_value": 20.0,
            "deviation_abs": 30.0,
            "deviation_pct": 1.5,
            "candidate_score": 2.5,
            "flag_level": "low",
            "direction": "up",
        }
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        f = result["findings"][0]
        self.assertEqual(f["provenance"]["canonical_item_key"], "candidates:0")


# ---------------------------------------------------------------------------
# TestDetectExtractorAnalysisAxis
# ---------------------------------------------------------------------------


class TestDetectExtractorAnalysisAxis(unittest.TestCase):
    def test_time_axis_for_window_candidate(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["analysis_axis"], "time")

    def test_scalar_axis_for_no_window_no_slice(self) -> None:
        c: dict[str, Any] = {
            "window": None,
            "slice": None,
            "observed_value": 50.0,
            "expected_value": 20.0,
            "deviation_abs": 30.0,
            "deviation_pct": 1.5,
            "candidate_score": 2.5,
            "flag_level": "medium",
            "direction": "up",
        }
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["analysis_axis"], "scalar")

    def test_segment_axis_for_slice_without_window(self) -> None:
        c: dict[str, Any] = {
            "window": None,
            "slice": {"region": "EU"},
            "observed_value": 50.0,
            "expected_value": 20.0,
            "deviation_abs": 30.0,
            "deviation_pct": 1.5,
            "candidate_score": 2.5,
            "flag_level": "low",
            "direction": "up",
        }
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["analysis_axis"], "segment")

    def test_slice_beats_time_axis_when_window_present(self) -> None:
        c = _candidate(candidate_slice={"region": "US"})
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["analysis_axis"], "segment")

    def test_same_window_segment_candidates_have_distinct_ids(self) -> None:
        c_eu = _candidate(candidate_slice={"region": "EU"})
        c_us = _candidate(candidate_slice={"region": "US"})
        result = _EXTRACTOR.extract(
            _ART_ID,
            _artifact(candidates=[c_eu, c_us]),
            _STEP_REF,
            _SESSION,
        )
        ids = [f["finding_id"] for f in result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))


# ---------------------------------------------------------------------------
# TestDetectExtractorRegistration — registry wiring
# ---------------------------------------------------------------------------


class TestDetectExtractorRegistration(unittest.TestCase):
    def test_registered_under_anomaly_candidates_v1(self) -> None:
        self.assertIn(("anomaly_candidates", "v1"), default_finding_registry.registered_keys())

    def test_find_returns_detect_extractor(self) -> None:
        extractor = default_finding_registry.find("anomaly_candidates", "v1")
        self.assertIsInstance(extractor, DetectArtifactExtractor)

    def test_find_with_none_version_returns_detect_extractor(self) -> None:
        # NULL version normalises to "v1"
        extractor = default_finding_registry.find("anomaly_candidates", None)
        self.assertIsInstance(extractor, DetectArtifactExtractor)

    def test_extractor_name_in_snapshot(self) -> None:
        snap = default_finding_registry.snapshot()
        names = [e["extractor_name"] for e in snap]
        self.assertIn("detect_artifact_v1", names)

    def test_family_in_snapshot(self) -> None:
        snap = default_finding_registry.snapshot()
        for entry in snap:
            if entry["artifact_type"] == "anomaly_candidates":
                self.assertEqual(entry["family"], "detect")
                break
        else:
            self.fail("anomaly_candidates entry not found in snapshot")


# ---------------------------------------------------------------------------
# TestDetectExtractorValidateForCommit — D4 commit gate
# ---------------------------------------------------------------------------


class TestDetectExtractorValidateForCommit(unittest.TestCase):
    def test_validate_passes_for_non_empty(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(), _STEP_REF, _SESSION)
        validate_for_commit("detect", result)

    def test_validate_passes_for_empty(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[]), _STEP_REF, _SESSION)
        validate_for_commit("detect", result)

    def test_validate_passes_for_multi_candidate(self) -> None:
        candidates = [
            _candidate(window_start="2024-01-03", window_end="2024-01-04"),
            _candidate(window_start="2024-01-05", window_end="2024-01-06"),
        ]
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=candidates), _STEP_REF, _SESSION)
        validate_for_commit("detect", result)

    def test_extractor_class_vars_correct(self) -> None:
        self.assertEqual(DetectArtifactExtractor.artifact_type, "anomaly_candidates")
        self.assertEqual(DetectArtifactExtractor.artifact_schema_version, "v1")
        self.assertEqual(DetectArtifactExtractor.family, "detect")
        self.assertEqual(DetectArtifactExtractor.extractor_name, "detect_artifact_v1")
        self.assertEqual(DetectArtifactExtractor.finding_schema_version, "v1")


# ---------------------------------------------------------------------------
# TestDetectExtractorGrain — subject.grain extraction from granularity
# ---------------------------------------------------------------------------


class TestDetectExtractorGrain(unittest.TestCase):
    def test_grain_day_extracted(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(grain="day"), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["grain"], "day")

    def test_grain_week_extracted(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(grain="week"), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["grain"], "week")

    def test_grain_month_extracted(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(grain="month"), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["grain"], "month")

    def test_grain_hour_extracted(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(grain="hour"), _STEP_REF, _SESSION)
        self.assertEqual(result["findings"][0]["subject"]["grain"], "hour")

    def test_grain_null_when_granularity_absent(self) -> None:
        payload: dict[str, Any] = {
            "artifact_type": "anomaly_candidates",
            "artifact_schema_version": "v1",
            "metric": "daily_users",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "candidates": [_candidate()],
        }
        result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][0]["subject"]["grain"])

    def test_grain_null_when_granularity_field_absent(self) -> None:
        payload = _artifact()
        del payload["granularity"]
        result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][0]["subject"]["grain"])

    def test_grain_null_for_invalid_value(self) -> None:
        payload = _artifact()
        payload["granularity"] = "minute"
        result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][0]["subject"]["grain"])

    def test_grain_null_when_grain_is_none(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(grain=None), _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][0]["subject"]["grain"])


# ---------------------------------------------------------------------------
# TestDetectExtractorSegmentCandidate — segment analysis_axis handling
# ---------------------------------------------------------------------------


def _segment_candidate(
    region: str = "EU",
    observed_value: float = 50.0,
    expected_value: float = 20.0,
) -> dict[str, Any]:
    """A candidate with a non-null slice and no window (segment anomaly)."""
    return {
        "window": None,
        "slice": {"region": region},
        "observed_value": observed_value,
        "expected_value": expected_value,
        "deviation_abs": observed_value - expected_value,
        "deviation_pct": (observed_value - expected_value) / expected_value,
        "candidate_score": 2.5,
        "flag_level": "medium",
        "direction": "up",
    }


class TestDetectExtractorSegmentCandidate(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = _segment_candidate(region="EU")
        self.result = _EXTRACTOR.extract(
            _ART_ID,
            _artifact(candidates=[self.candidate], scope={"country": "US"}),
            _STEP_REF,
            _SESSION,
        )
        self.finding = self.result["findings"][0]

    def test_analysis_axis_is_segment(self) -> None:
        self.assertEqual(self.finding["subject"]["analysis_axis"], "segment")

    def test_subject_slice_uses_candidate_slice_not_scope(self) -> None:
        # For segment candidates the subject.slice must reflect the candidate's
        # own slice dimensions (what segment is anomalous), not the artifact scope.
        self.assertEqual(self.finding["subject"]["slice"], {"region": "EU"})

    def test_subject_slice_does_not_contain_scope(self) -> None:
        self.assertNotIn("country", self.finding["subject"]["slice"])

    def test_canonical_item_key_uses_segment_stable_key(self) -> None:
        # Stable key derived from candidate.slice via sorted k=v|k=v encoding.
        self.assertEqual(self.finding["provenance"]["canonical_item_key"], "candidates:region=EU")

    def test_artifact_item_ref_key_is_segment_key(self) -> None:
        ref = self.finding["provenance"]["artifact_item_ref"]
        self.assertEqual(ref["key"], "region=EU")
        self.assertIsNone(ref["index"])

    def test_finding_id_stable_on_replay(self) -> None:
        payload = _artifact(candidates=[self.candidate])
        r1 = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        r2 = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        self.assertEqual(r1["findings"][0]["finding_id"], r2["findings"][0]["finding_id"])

    def test_distinct_segment_candidates_have_distinct_ids(self) -> None:
        c_eu = _segment_candidate(region="EU")
        c_us = _segment_candidate(region="US")
        result = _EXTRACTOR.extract(
            _ART_ID,
            _artifact(candidates=[c_eu, c_us]),
            _STEP_REF,
            _SESSION,
        )
        ids = [f["finding_id"] for f in result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_multi_dim_segment_key_is_sorted(self) -> None:
        c: dict[str, Any] = {
            "window": None,
            "slice": {"device": "iOS", "country": "US"},
            "observed_value": 50.0,
            "expected_value": 20.0,
            "deviation_abs": 30.0,
            "deviation_pct": 1.5,
            "candidate_score": 2.5,
            "flag_level": "low",
            "direction": "up",
        }
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        # Sorted: country before device
        key = result["findings"][0]["provenance"]["canonical_item_key"]
        self.assertEqual(key, "candidates:country=US|device=iOS")

    def test_observed_window_is_none_for_segment_candidate(self) -> None:
        self.assertIsNone(self.finding["observed_window"])


# ---------------------------------------------------------------------------
# TestDetectExtractorArtifactEmbeddedCandidateRef — runner-embedded ref ignored
# ---------------------------------------------------------------------------


class TestDetectExtractorArtifactEmbeddedCandidateRef(unittest.TestCase):
    """The detect runner embeds a ``candidate_ref`` inside each candidate dict.

    The extractor must ignore it and reconstruct the canonical ref from the
    D2-priority key (window.start / segment key / index).  These tests verify
    that the extractor's output ref is stable regardless of what the embedded
    runner ref contains.
    """

    def test_extractor_ignores_embedded_candidate_ref_index(self) -> None:
        # The runner embeds index=0 in the candidate_ref, but the extractor
        # should use window.start as the stable key (D2 priority).
        c = _candidate(window_start="2024-01-05", window_end="2024-01-06")
        # The embedded candidate_ref claims index=99 — extractor must not use it.
        c["candidate_ref"]["item_ref"]["index"] = 99
        result = _EXTRACTOR.extract(_ART_ID, _artifact(candidates=[c]), _STEP_REF, _SESSION)
        ref = result["findings"][0]["provenance"]["artifact_item_ref"]
        # Key-based identity from window.start, index must be None.
        self.assertEqual(ref["key"], "2024-01-05")
        self.assertIsNone(ref["index"])

    def test_payload_candidate_ref_uses_d2_stable_key(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(), _STEP_REF, _SESSION)
        payload_ref = result["findings"][0]["payload"]["candidate_ref"]
        # Must use the same D2-priority key as provenance, not the runner-embedded ref.
        self.assertEqual(payload_ref["item_ref"]["key"], "2024-01-05")
        self.assertIsNone(payload_ref["item_ref"]["index"])

    def test_payload_candidate_ref_artifact_id_is_correct(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _artifact(), _STEP_REF, _SESSION)
        # artifact_id must be the extractor's input, not null from the embedded ref.
        self.assertEqual(result["findings"][0]["payload"]["candidate_ref"]["artifact_id"], _ART_ID)
