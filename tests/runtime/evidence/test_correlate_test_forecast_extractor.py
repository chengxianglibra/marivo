"""Tests for correlate / forecast finding extractors (Phase 4d-4).

CorrelateArtifactExtractor (pairwise_time_series_association → CorrelationResultFinding):
- D5: exactly 1 finding per artifact
- finding_id stability across re-extraction
- finding_type = "correlation_result"
- payload fields (method, coefficient, p_value, n, join_basis, left/right artifact_id)
- subject.analysis_axis = "correlation"
- observed_window from matched_time_scope when present; None when absent
- validate_for_commit("correlate", result) passes
- Registered under ("pairwise_time_series_association", "v1"); NULL normalisation

ForecastArtifactExtractor (forecast_series → ForecastPointFinding):
- N buckets → N findings
- finding_id stable per bucket (boundary key, not horizon_index)
- payload fields (bucket_start, bucket_end, predicted_value, prediction_interval, horizon_index)
- subject.analysis_axis = "forecast"; subject.metric from payload
- observed_window per bucket
- horizon_index does NOT enter finding_id
- validate_for_commit("forecast", result) passes for N >= 1
- Empty forecast → validate_for_commit raises FamilyEmptyError
- Registered under ("forecast_series", "v1"); NULL normalisation
"""

# ruff: noqa: I001
from __future__ import annotations

import unittest
from typing import Any

from marivo.core.evidence.canonical_finding import (
    StepRef,
    make_finding_id,
    make_item_identity,
)

# Registry must be imported first so bootstrap runs before individual extractors
# are imported (same bootstrap pattern as other 4d-* test files).
from marivo.runtime.evidence.finding_extractor_registry import (
    default_finding_registry,
    validate_for_commit,
)
from marivo.runtime.evidence.correlate_extractor import CorrelateArtifactExtractor
from marivo.runtime.evidence.forecast_extractor import ForecastArtifactExtractor
from marivo.core.evidence.family_contract import FamilyEmptyError
from tests.finding_identity_testutil import assert_finding_id_stable

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SESSION = "sess_4d4_test"
_STEP_ID = "step_4d4_001"

_CORRELATE_STEP_REF: StepRef = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="correlate")
_FORECAST_STEP_REF: StepRef = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="forecast")

_LEFT_ART_ID = "art_obs_left_001"
_RIGHT_ART_ID = "art_obs_right_001"
_CORRELATE_ART_ID = "art_correlate_001"
_FORECAST_ART_ID = "art_forecast_001"

_CORRELATE_EXTRACTOR = CorrelateArtifactExtractor()
_FORECAST_EXTRACTOR = ForecastArtifactExtractor()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _correlate_payload(
    left_artifact_id: str = _LEFT_ART_ID,
    right_artifact_id: str = _RIGHT_ART_ID,
    method: str = "spearman",
    coefficient: float | None = 0.85,
    p_value: float | None = 0.02,
    n_pairs: int | None = 12,
    pairing_rule: str = "intersection_by_time_bucket",
    matched_time_scope: dict[str, Any] | None = None,
    left_metric: str = "dau",
    right_metric: str = "revenue",
) -> dict[str, Any]:
    if matched_time_scope is None:
        matched_time_scope = {"field": "time", "start": "2024-01-01", "end": "2024-01-12"}
    return {
        "association_type": "pairwise_time_series_association",
        "left_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_left",
            "artifact_id": left_artifact_id,
        },
        "right_ref": {
            "step_type": "observe",
            "session_id": _SESSION,
            "step_id": "step_obs_right",
            "artifact_id": right_artifact_id,
        },
        "left_metric": left_metric,
        "right_metric": right_metric,
        "statistic": {
            "method": method,
            "coefficient": coefficient,
            "p_value": p_value,
            "n_pairs": n_pairs,
        },
        "analytical_metadata": {
            "pairing_rule": pairing_rule,
            "matched_time_scope": matched_time_scope,
        },
    }


def _forecast_bucket(
    start: str,
    end: str,
    bucket_index: int,
    point_forecast: float = 100.0,
    prediction_interval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "bucket_index": bucket_index,
        "window": {"start": start, "end": end},
        "point_forecast": point_forecast,
        "prediction_interval": prediction_interval,
    }


def _forecast_payload(
    metric: str = "dau",
    buckets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if buckets is None:
        buckets = [
            _forecast_bucket("2024-01-08", "2024-01-09", 1, 95.0),
            _forecast_bucket("2024-01-09", "2024-01-10", 2, 96.5),
            _forecast_bucket("2024-01-10", "2024-01-11", 3, 98.0),
        ]
    return {
        "observation_type": "forecast_series",
        "artifact_schema_version": "v1",
        "metric": metric,
        "profile": "trend",
        "interval_level": 0.95,
        "forecast": buckets,
    }


# ===========================================================================
# CorrelateArtifactExtractor tests
# ===========================================================================


class TestCorrelateExtractor(unittest.TestCase):
    def _extract(self, payload: dict[str, Any] | None = None) -> Any:
        p = payload if payload is not None else _correlate_payload()
        return _CORRELATE_EXTRACTOR.extract(_CORRELATE_ART_ID, p, _CORRELATE_STEP_REF, _SESSION)

    def test_d5_exactly_one_finding(self) -> None:
        result = self._extract()
        self.assertEqual(result["finding_count"], 1)
        self.assertEqual(len(result["findings"]), 1)

    def test_finding_type(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["finding_type"], "correlation_result")

    def test_finding_id_stable(self) -> None:
        assert_finding_id_stable(
            self,
            _CORRELATE_ART_ID,
            "correlation_result",
            "result",
        )

    def test_finding_id_derivation(self) -> None:
        result = self._extract()
        expected_key, _ = make_item_identity("result")
        expected_id = make_finding_id(_CORRELATE_ART_ID, "correlation_result", expected_key)
        self.assertEqual(result["findings"][0]["finding_id"], expected_id)

    def test_payload_method(self) -> None:
        result = self._extract(_correlate_payload(method="pearson"))
        self.assertEqual(result["findings"][0]["payload"]["method"], "pearson")

    def test_payload_coefficient_p_value_n(self) -> None:
        p = _correlate_payload(coefficient=0.72, p_value=0.01, n_pairs=20)
        result = self._extract(p)
        payload = result["findings"][0]["payload"]
        self.assertAlmostEqual(payload["coefficient"], 0.72)
        self.assertAlmostEqual(payload["p_value"], 0.01)
        self.assertEqual(payload["n"], 20)

    def test_payload_coefficient_none(self) -> None:
        result = self._extract(_correlate_payload(coefficient=None))
        self.assertIsNone(result["findings"][0]["payload"]["coefficient"])

    def test_payload_join_basis(self) -> None:
        result = self._extract(_correlate_payload(pairing_rule="intersection_by_time_bucket"))
        self.assertEqual(
            result["findings"][0]["payload"]["join_basis"], "intersection_by_time_bucket"
        )

    def test_payload_left_right_artifact_ids(self) -> None:
        result = self._extract()
        payload = result["findings"][0]["payload"]
        self.assertEqual(payload["left_ref"]["artifact_id"], _LEFT_ART_ID)
        self.assertEqual(payload["right_ref"]["artifact_id"], _RIGHT_ART_ID)

    def test_subject_analysis_axis(self) -> None:
        result = self._extract()
        self.assertEqual(result["findings"][0]["subject"]["analysis_axis"], "correlation")

    def test_subject_metric_from_left_metric(self) -> None:
        result = self._extract(_correlate_payload(left_metric="dau"))
        self.assertEqual(result["findings"][0]["subject"]["metric"], "dau")

    def test_observed_window_from_matched_time_scope(self) -> None:
        scope = {"field": "time", "start": "2024-01-01", "end": "2024-01-12"}
        result = self._extract(_correlate_payload(matched_time_scope=scope))
        ow = result["findings"][0]["observed_window"]
        self.assertIsNotNone(ow)
        self.assertEqual(ow["start"], "2024-01-01")
        self.assertEqual(ow["end"], "2024-01-12")

    def test_observed_window_none_when_absent(self) -> None:
        payload = _correlate_payload()
        payload["analytical_metadata"].pop("matched_time_scope", None)
        result = self._extract(payload)
        self.assertIsNone(result["findings"][0]["observed_window"])

    def test_validate_for_commit_passes(self) -> None:
        result = self._extract()
        # Should not raise
        validate_for_commit("correlate", result)

    def test_registered_in_default_registry(self) -> None:
        extractor = default_finding_registry.find("pairwise_time_series_association", "v1")
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, CorrelateArtifactExtractor)

    def test_null_version_normalisation(self) -> None:
        extractor = default_finding_registry.find("pairwise_time_series_association", None)
        self.assertIsNotNone(extractor)

    def test_payload_method_unknown_passes_through(self) -> None:
        """Unknown method string is preserved as-is, not coerced to a default."""
        result = self._extract(_correlate_payload(method="kendall"))
        self.assertEqual(result["findings"][0]["payload"]["method"], "kendall")

    def test_missing_left_right_ref_artifact_id_empty(self) -> None:
        """Absent left_ref / right_ref keys produce artifact_id='' without crashing."""
        p = _correlate_payload()
        del p["left_ref"]
        del p["right_ref"]
        result = self._extract(p)
        payload = result["findings"][0]["payload"]
        self.assertEqual(payload["left_ref"]["artifact_id"], "")
        self.assertEqual(payload["right_ref"]["artifact_id"], "")


# ===========================================================================
# ForecastArtifactExtractor tests
# ===========================================================================


class TestForecastExtractor(unittest.TestCase):
    def _extract(self, payload: dict[str, Any] | None = None) -> Any:
        p = payload if payload is not None else _forecast_payload()
        return _FORECAST_EXTRACTOR.extract(_FORECAST_ART_ID, p, _FORECAST_STEP_REF, _SESSION)

    def test_n_buckets_n_findings(self) -> None:
        result = self._extract()
        self.assertEqual(result["finding_count"], 3)
        self.assertEqual(len(result["findings"]), 3)

    def test_single_bucket(self) -> None:
        p = _forecast_payload(buckets=[_forecast_bucket("2024-01-08", "2024-01-09", 1, 100.0)])
        result = self._extract(p)
        self.assertEqual(result["finding_count"], 1)

    def test_finding_type(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["finding_type"], "forecast_point")

    def test_finding_id_stable(self) -> None:
        assert_finding_id_stable(
            self,
            _FORECAST_ART_ID,
            "forecast_point",
            "points",
            key="2024-01-08/2024-01-09",
        )

    def test_finding_id_derivation(self) -> None:
        result = self._extract()
        # First bucket: "2024-01-08/2024-01-09"
        expected_key, _ = make_item_identity("points", key="2024-01-08/2024-01-09")
        expected_id = make_finding_id(_FORECAST_ART_ID, "forecast_point", expected_key)
        self.assertEqual(result["findings"][0]["finding_id"], expected_id)

    def test_horizon_index_does_not_enter_finding_id(self) -> None:
        """Two payloads with the same bucket window but different horizon_index
        should produce the same finding_id (only boundary enters identity)."""
        b1 = _forecast_bucket("2024-01-08", "2024-01-09", 1, 100.0)
        b2 = _forecast_bucket("2024-01-08", "2024-01-09", 99, 100.0)
        r1 = _FORECAST_EXTRACTOR.extract(
            _FORECAST_ART_ID, _forecast_payload(buckets=[b1]), _FORECAST_STEP_REF, _SESSION
        )
        r2 = _FORECAST_EXTRACTOR.extract(
            _FORECAST_ART_ID, _forecast_payload(buckets=[b2]), _FORECAST_STEP_REF, _SESSION
        )
        self.assertEqual(r1["findings"][0]["finding_id"], r2["findings"][0]["finding_id"])

    def test_payload_bucket_start_end(self) -> None:
        result = self._extract()
        p = result["findings"][0]["payload"]
        self.assertEqual(p["bucket_start"], "2024-01-08")
        self.assertEqual(p["bucket_end"], "2024-01-09")

    def test_payload_predicted_value(self) -> None:
        result = self._extract()
        self.assertAlmostEqual(result["findings"][0]["payload"]["predicted_value"], 95.0)

    def test_payload_horizon_index(self) -> None:
        result = self._extract()
        for i, f in enumerate(result["findings"]):
            self.assertEqual(f["payload"]["horizon_index"], i + 1)

    def test_payload_prediction_interval_present(self) -> None:
        pi = {"level": 0.95, "lower": 80.0, "upper": 110.0}
        buckets = [_forecast_bucket("2024-01-08", "2024-01-09", 1, 95.0, pi)]
        result = self._extract(_forecast_payload(buckets=buckets))
        stored_pi = result["findings"][0]["payload"]["prediction_interval"]
        self.assertIsNotNone(stored_pi)
        self.assertAlmostEqual(stored_pi["lower"], 80.0)
        self.assertAlmostEqual(stored_pi["upper"], 110.0)
        self.assertAlmostEqual(stored_pi["level"], 0.95)

    def test_payload_prediction_interval_none(self) -> None:
        buckets = [_forecast_bucket("2024-01-08", "2024-01-09", 1, 95.0, None)]
        result = self._extract(_forecast_payload(buckets=buckets))
        self.assertIsNone(result["findings"][0]["payload"]["prediction_interval"])

    def test_subject_analysis_axis(self) -> None:
        result = self._extract()
        for f in result["findings"]:
            self.assertEqual(f["subject"]["analysis_axis"], "forecast")

    def test_subject_metric(self) -> None:
        result = self._extract(_forecast_payload(metric="revenue"))
        for f in result["findings"]:
            self.assertEqual(f["subject"]["metric"], "revenue")

    def test_observed_window_per_bucket(self) -> None:
        result = self._extract()
        f = result["findings"][0]
        ow = f["observed_window"]
        self.assertIsNotNone(ow)
        self.assertEqual(ow["field"], "time")
        self.assertEqual(ow["start"], "2024-01-08")
        self.assertEqual(ow["end"], "2024-01-09")

    def test_validate_for_commit_passes(self) -> None:
        result = self._extract()
        validate_for_commit("forecast", result)

    def test_empty_forecast_raises_family_empty_error(self) -> None:
        result = self._extract(_forecast_payload(buckets=[]))
        with self.assertRaises(FamilyEmptyError):
            validate_for_commit("forecast", result)

    def test_registered_in_default_registry(self) -> None:
        extractor = default_finding_registry.find("forecast_series", "v1")
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, ForecastArtifactExtractor)

    def test_null_version_normalisation(self) -> None:
        extractor = default_finding_registry.find("forecast_series", None)
        self.assertIsNotNone(extractor)

    def test_bucket_with_no_window(self) -> None:
        """Bucket missing 'window' key produces observed_window=None and empty
        bucket_start/bucket_end in the payload — no crash."""
        bucket = {"bucket_index": 1, "point_forecast": 77.0}  # no 'window' key
        result = self._extract(_forecast_payload(buckets=[bucket]))
        f = result["findings"][0]
        self.assertIsNone(f["observed_window"])
        self.assertEqual(f["payload"]["bucket_start"], "")
        self.assertEqual(f["payload"]["bucket_end"], "")
        self.assertEqual(f["payload"]["predicted_value"], 77.0)


# ===========================================================================
# Registry snapshot completeness (7 extractors after 4d-4)
# ===========================================================================


class TestRegistrySnapshotCompleteness(unittest.TestCase):
    def test_snapshot_contains_all_six_extractors(self) -> None:
        snapshot = default_finding_registry.snapshot()
        artifact_types = {e["artifact_type"] for e in snapshot}
        expected = {
            "observation",
            "anomaly_candidates",
            "compare_artifact",
            "delta_decomposition",
            "pairwise_time_series_association",
            "forecast_series",
        }
        self.assertEqual(artifact_types, expected)

    def test_snapshot_has_six_entries(self) -> None:
        snapshot = default_finding_registry.snapshot()
        self.assertEqual(len(snapshot), 6)
