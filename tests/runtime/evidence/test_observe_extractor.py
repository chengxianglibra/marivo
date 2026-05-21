"""Tests for the observe metric_frame -> observation finding extractor.

Covers acceptance criteria:
- All metric_frame shapes (scalar, time_series, segmented, panel)
  produce correct ObservationFindings.
- time_series / segmented / panel success-empty is legal.
- finding_id is stable across replay for every mode.
- Segment canonical_item_key is derived from sorted key-value pairs (projection order
  does not affect identity).
- ObserveArtifactExtractor is registered in default_finding_registry under
  ("metric_frame", None).
- validate_for_commit("observe", result) passes for all modes, including empty.
"""

from __future__ import annotations

import unittest
from typing import Any

from marivo.core.evidence.canonical_finding import StepRef, make_finding_id, make_item_identity
from marivo.runtime.evidence.finding_extractor_registry import (
    default_finding_registry,
    validate_for_commit,
)
from marivo.runtime.evidence.observe_extractor import ObserveArtifactExtractor
from tests.finding_identity_testutil import (
    assert_finding_id_stable,
    assert_projection_order_excluded,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ART_ID = "art_observe_test001"
_SESSION = "sess_obs_test"
_STEP_ID = "step_obs_001"
_STEP_REF: StepRef = StepRef(session_id=_SESSION, step_id=_STEP_ID, step_type="observe")

_EXTRACTOR = ObserveArtifactExtractor()


def _scalar_payload(
    metric: str = "daily_users",
    value: float | None = 1234.0,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": metric,
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": scope or {},
        },
        "axes": [],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"value": value}]}]},
    }


def _time_series_payload(
    points: list[dict[str, Any]] | None = None,
    grain: str = "day",
) -> dict[str, Any]:
    if points is None:
        points = [
            {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 100.0},
            {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 200.0},
        ]
    return {
        "artifact_family": "metric_frame",
        "shape": "time_series",
        "subject": {
            "kind": "metric",
            "metric_ref": "daily_users",
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": {},
        },
        "axes": [{"kind": "time", "grain": grain}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": points}]},
    }


def _segmented_payload(
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if series is None:
        series = [
            {"keys": {"region": "US"}, "points": [{"value": 500.0}]},
            {"keys": {"region": "EU"}, "points": [{"value": 300.0}]},
        ]
    return {
        "artifact_family": "metric_frame",
        "shape": "segmented",
        "subject": {
            "kind": "metric",
            "metric_ref": "daily_users",
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": {},
        },
        "axes": [{"kind": "dimension", "name": "region"}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": series},
    }


def _panel_payload(series: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if series is None:
        series = [
            {
                "keys": {"region": "US"},
                "points": [
                    {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 100.0},
                    {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": None},
                ],
            },
            {
                "keys": {"region": "EU"},
                "points": [
                    {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 80.0},
                ],
            },
        ]
    return {
        "artifact_family": "metric_frame",
        "shape": "panel",
        "subject": {
            "kind": "metric",
            "metric_ref": "daily_users",
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": {"market": "global"},
        },
        "axes": [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": series},
    }


# ---------------------------------------------------------------------------
# TestObserveExtractorScalar
# ---------------------------------------------------------------------------


class TestObserveExtractorScalar(unittest.TestCase):
    def setUp(self) -> None:
        self.result = _EXTRACTOR.extract(_ART_ID, _scalar_payload(), _STEP_REF, _SESSION)

    def test_produces_one_finding(self) -> None:
        self.assertEqual(len(self.result["findings"]), 1)

    def test_finding_count_matches_findings(self) -> None:
        self.assertEqual(self.result["finding_count"], len(self.result["findings"]))

    def test_observation_kind_is_scalar(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["payload"]["observation_kind"], "scalar")

    def test_canonical_item_key_is_value(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["provenance"]["canonical_item_key"], "value")

    def test_finding_id_starts_with_fnd(self) -> None:
        f = self.result["findings"][0]
        self.assertTrue(f["finding_id"].startswith("fnd_"))

    def test_finding_id_stable_on_replay(self) -> None:
        result2 = _EXTRACTOR.extract(_ART_ID, _scalar_payload(), _STEP_REF, _SESSION)
        self.assertEqual(
            self.result["findings"][0]["finding_id"],
            result2["findings"][0]["finding_id"],
        )

    def test_null_value_still_produces_one_finding(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _scalar_payload(value=None), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 1)
        self.assertIsNone(result["findings"][0]["payload"]["value"])

    def test_quality_is_empty_for_metric_frame(self) -> None:
        f = self.result["findings"][0]
        self.assertIsNone(f["quality"]["quality_status"])
        self.assertEqual(f["quality"]["quality_warnings"], [])

    def test_subject_metric_and_analysis_axis(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["subject"]["metric"], "daily_users")
        self.assertEqual(f["subject"]["analysis_axis"], "scalar")

    def test_subject_grain_is_none(self) -> None:
        f = self.result["findings"][0]
        self.assertIsNone(f["subject"]["grain"])

    def test_projection_ref_is_none(self) -> None:
        f = self.result["findings"][0]
        self.assertIsNone(f["provenance"]["projection_ref"])

    def test_extractor_name_in_provenance(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["provenance"]["extractor_name"], "observe_metric_frame_v1")

    def test_finding_type_is_observation(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["finding_type"], "observation")

    def test_artifact_id_in_finding(self) -> None:
        f = self.result["findings"][0]
        self.assertEqual(f["artifact_id"], _ART_ID)


# ---------------------------------------------------------------------------
# TestObserveExtractorTimeSeries
# ---------------------------------------------------------------------------


class TestObserveExtractorTimeSeries(unittest.TestCase):
    def test_findings_per_bucket(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 2)

    def test_finding_count_matches_findings(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        self.assertEqual(result["finding_count"], 2)

    def test_observation_kind_is_time_bucket(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        for f in result["findings"]:
            self.assertEqual(f["payload"]["observation_kind"], "time_bucket")

    def test_empty_series_is_success_empty(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(points=[]), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 0)
        self.assertEqual(result["finding_count"], 0)

    def test_observed_window_per_finding(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        f0 = result["findings"][0]
        self.assertEqual(f0["observed_window"]["field"], "time")
        self.assertEqual(f0["observed_window"]["start"], "2024-01-01")
        self.assertEqual(f0["observed_window"]["end"], "2024-01-02")

    def test_grain_propagated_to_subject(self) -> None:
        result = _EXTRACTOR.extract(
            _ART_ID, _time_series_payload(grain="week"), _STEP_REF, _SESSION
        )
        for f in result["findings"]:
            self.assertEqual(f["subject"]["grain"], "week")

    def test_analysis_axis_is_time(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        for f in result["findings"]:
            self.assertEqual(f["subject"]["analysis_axis"], "time")

    def test_bucket_canonical_key_embeds_window(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        f0 = result["findings"][0]
        self.assertEqual(f0["provenance"]["canonical_item_key"], "buckets:2024-01-01/2024-01-02")

    def test_different_buckets_get_different_finding_ids(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        ids = [f["finding_id"] for f in result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_bucket_finding_id_stable_on_replay(self) -> None:
        result1 = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        result2 = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        ids1 = [f["finding_id"] for f in result1["findings"]]
        ids2 = [f["finding_id"] for f in result2["findings"]]
        self.assertEqual(ids1, ids2)

    def test_null_bucket_value_still_produces_finding(self) -> None:
        result = _EXTRACTOR.extract(
            _ART_ID,
            _time_series_payload(
                points=[{"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": None}]
            ),
            _STEP_REF,
            _SESSION,
        )
        self.assertEqual(len(result["findings"]), 1)
        self.assertIsNone(result["findings"][0]["payload"]["value"])


# ---------------------------------------------------------------------------
# TestObserveExtractorSegmented
# ---------------------------------------------------------------------------


class TestObserveExtractorSegmented(unittest.TestCase):
    def test_findings_per_segment(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 2)

    def test_finding_count_matches_findings(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        self.assertEqual(result["finding_count"], 2)

    def test_observation_kind_is_segment(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        for f in result["findings"]:
            self.assertEqual(f["payload"]["observation_kind"], "segment")

    def test_empty_segments_is_success_empty(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(series=[]), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 0)
        self.assertEqual(result["finding_count"], 0)

    def test_slice_per_finding_matches_segment_keys(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        slices = [f["subject"]["slice"] for f in result["findings"]]
        self.assertIn({"region": "US"}, slices)
        self.assertIn({"region": "EU"}, slices)

    def test_analysis_axis_is_segment(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        for f in result["findings"]:
            self.assertEqual(f["subject"]["analysis_axis"], "segment")

    def test_segment_canonical_key_is_sorted_kv(self) -> None:
        segs = [{"keys": {"device": "mobile", "region": "US"}, "points": [{"value": 100.0}]}]
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(series=segs), _STEP_REF, _SESSION)
        f = result["findings"][0]
        # sorted({"device": "mobile", "region": "US"}.items()) → device, region
        self.assertEqual(f["provenance"]["canonical_item_key"], "rows:device=mobile|region=US")

    def test_segment_key_order_does_not_affect_finding_id(self) -> None:
        """Key insertion order must not affect canonical_item_key."""
        segs_ab = [{"keys": {"a": "1", "b": "2"}, "points": [{"value": 10.0}]}]
        segs_ba = [{"keys": {"b": "2", "a": "1"}, "points": [{"value": 10.0}]}]
        res_ab = _EXTRACTOR.extract(
            _ART_ID, _segmented_payload(series=segs_ab), _STEP_REF, _SESSION
        )
        res_ba = _EXTRACTOR.extract(
            _ART_ID, _segmented_payload(series=segs_ba), _STEP_REF, _SESSION
        )
        self.assertEqual(
            res_ab["findings"][0]["finding_id"],
            res_ba["findings"][0]["finding_id"],
        )

    def test_segment_rank_is_none(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        for f in result["findings"]:
            self.assertIsNone(f["payload"]["rank"])

    def test_different_segments_get_different_finding_ids(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(), _STEP_REF, _SESSION)
        ids = [f["finding_id"] for f in result["findings"]]
        self.assertEqual(len(ids), len(set(ids)))


# ---------------------------------------------------------------------------
# TestObserveExtractorPanel
# ---------------------------------------------------------------------------


class TestObserveExtractorPanel(unittest.TestCase):
    def test_findings_per_series_point(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _panel_payload(), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 3)

    def test_panel_subject_slice_combines_scope_and_keys(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _panel_payload(), _STEP_REF, _SESSION)
        self.assertIn(
            {"market": "global", "region": "US"},
            [f["subject"]["slice"] for f in result["findings"]],
        )

    def test_panel_canonical_key_includes_segment_and_window(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _panel_payload(), _STEP_REF, _SESSION)
        self.assertEqual(
            result["findings"][0]["provenance"]["canonical_item_key"],
            "buckets:region=US|2024-01-01/2024-01-02",
        )

    def test_panel_null_point_value_still_produces_finding(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _panel_payload(), _STEP_REF, _SESSION)
        self.assertIsNone(result["findings"][1]["payload"]["value"])

    def test_empty_panel_series_is_success_empty(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _panel_payload(series=[]), _STEP_REF, _SESSION)
        self.assertEqual(len(result["findings"]), 0)
        self.assertEqual(result["finding_count"], 0)


# ---------------------------------------------------------------------------
# TestObserveExtractorInvalidMetricFrame
# ---------------------------------------------------------------------------


class TestObserveExtractorInvalidMetricFrame(unittest.TestCase):
    def test_unknown_metric_frame_shape_raises_value_error(self) -> None:
        payload = {**_scalar_payload(), "shape": "unexpected_mode"}
        with self.assertRaises(ValueError):
            _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)

    def test_non_metric_frame_raises_value_error(self) -> None:
        payload = dict(_scalar_payload())
        payload["artifact_family"] = "observation"
        with self.assertRaises(ValueError):
            _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)


# ---------------------------------------------------------------------------
# TestObserveExtractorRegistration
# ---------------------------------------------------------------------------


class TestObserveExtractorRegistration(unittest.TestCase):
    def test_default_registry_has_observe_extractor(self) -> None:
        extractor = default_finding_registry.find("metric_frame", None)
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, ObserveArtifactExtractor)

    def test_observation_artifact_type_is_not_registered(self) -> None:
        extractor = default_finding_registry.find("observation", "v1")
        self.assertIsNone(extractor)

    def test_strict_get_resolves_null_schema_version(self) -> None:
        extractor = default_finding_registry.get("metric_frame", None)
        self.assertIsNotNone(extractor)
        self.assertIsInstance(extractor, ObserveArtifactExtractor)

    def test_extractor_name_in_snapshot(self) -> None:
        names = [e["extractor_name"] for e in default_finding_registry.snapshot()]
        self.assertIn("observe_metric_frame_v1", names)

    def test_snapshot_family_is_observe(self) -> None:
        entry = next(
            e
            for e in default_finding_registry.snapshot()
            if e["extractor_name"] == "observe_metric_frame_v1"
        )
        self.assertEqual(entry["family"], "observe")
        self.assertEqual(entry["artifact_type"], "metric_frame")
        self.assertIsNone(entry["artifact_schema_version"])


# ---------------------------------------------------------------------------
# TestObserveExtractorIdentityStability
# ---------------------------------------------------------------------------


class TestObserveExtractorIdentityStability(unittest.TestCase):
    """Replay / idempotency checks using shared testutil helpers."""

    def test_scalar_item_identity_stable(self) -> None:
        assert_finding_id_stable(self, _ART_ID, "observation", "value")

    def test_time_bucket_item_identity_stable(self) -> None:
        bucket_key = "2024-01-01/2024-01-02"
        assert_finding_id_stable(self, _ART_ID, "observation", "buckets", key=bucket_key)

    def test_segment_item_identity_stable(self) -> None:
        seg_key = "region=US"
        assert_finding_id_stable(self, _ART_ID, "observation", "rows", key=seg_key)

    def test_segment_projection_order_excluded(self) -> None:
        # A segment with a stable key must produce the same finding_id regardless
        # of what rank it had in a projection order.
        assert_projection_order_excluded(self, _ART_ID, "observation", "rows", "region=US")

    def test_scalar_finding_id_matches_manual_computation(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _scalar_payload(), _STEP_REF, _SESSION)
        cik, _ = make_item_identity("value")
        expected_id = make_finding_id(_ART_ID, "observation", cik)
        self.assertEqual(result["findings"][0]["finding_id"], expected_id)

    def test_bucket_finding_id_matches_manual_computation(self) -> None:
        result = _EXTRACTOR.extract(_ART_ID, _time_series_payload(), _STEP_REF, _SESSION)
        cik, _ = make_item_identity("buckets", key="2024-01-01/2024-01-02")
        expected_id = make_finding_id(_ART_ID, "observation", cik)
        self.assertEqual(result["findings"][0]["finding_id"], expected_id)


# ---------------------------------------------------------------------------
# TestObserveExtractorValidateForCommit
# ---------------------------------------------------------------------------


class TestObserveExtractorValidateForCommit(unittest.TestCase):
    """validate_for_commit passes for all modes, including success-empty."""

    def _validate(self, payload: dict[str, Any]) -> None:
        result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION)
        validate_for_commit("observe", result)  # must not raise

    def test_validate_scalar_passes(self) -> None:
        self._validate(_scalar_payload())

    def test_validate_scalar_null_value_passes(self) -> None:
        self._validate(_scalar_payload(value=None))

    def test_validate_time_series_non_empty_passes(self) -> None:
        self._validate(_time_series_payload())

    def test_validate_time_series_empty_passes(self) -> None:
        # D4: observe allows success-empty → 0 findings is legal.
        self._validate(_time_series_payload(points=[]))

    def test_validate_segmented_non_empty_passes(self) -> None:
        self._validate(_segmented_payload())

    def test_validate_segmented_empty_passes(self) -> None:
        # D4: observe allows success-empty.
        self._validate(_segmented_payload(series=[]))

    def test_validate_panel_non_empty_passes(self) -> None:
        self._validate(_panel_payload())

    def test_validate_panel_empty_passes(self) -> None:
        self._validate(_panel_payload(series=[]))


# ---------------------------------------------------------------------------
# TestSegmentKeyEscaping
# ---------------------------------------------------------------------------


class TestSegmentKeyEscaping(unittest.TestCase):
    """Segment canonical_item_key must not collide when values contain separators."""

    def _finding_id_for_seg(self, keys: dict[str, Any]) -> str:
        segs = [{"keys": keys, "points": [{"value": 1.0}]}]
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(series=segs), _STEP_REF, _SESSION)
        return result["findings"][0]["finding_id"]

    def test_pipe_in_value_does_not_collide(self) -> None:
        # {"a": "x|b=y"} must NOT produce the same finding_id as {"a": "x", "b": "y"}
        id_single = self._finding_id_for_seg({"a": "x|b=y"})
        id_two = self._finding_id_for_seg({"a": "x", "b": "y"})
        self.assertNotEqual(id_single, id_two)

    def test_equals_in_value_does_not_collide(self) -> None:
        # {"k": "a=b"} must NOT produce the same finding_id as {"k": "a", "extra": "b"}
        id_eq_in_val = self._finding_id_for_seg({"k": "a=b"})
        id_two_keys = self._finding_id_for_seg({"k": "a", "extra": "b"})
        self.assertNotEqual(id_eq_in_val, id_two_keys)

    def test_clean_keys_unaffected(self) -> None:
        # Keys with no special characters must produce the same key as before.
        segs = [{"keys": {"region": "US"}, "points": [{"value": 500.0}]}]
        result = _EXTRACTOR.extract(_ART_ID, _segmented_payload(series=segs), _STEP_REF, _SESSION)
        cik = result["findings"][0]["provenance"]["canonical_item_key"]
        self.assertEqual(cik, "rows:region=US")

    def test_percent_in_value_escaped(self) -> None:
        # A raw "%" in a value must itself be escaped so it cannot masquerade as
        # a percent-encoded sequence inserted by the escaping logic.
        id_pct = self._finding_id_for_seg({"k": "50%"})
        id_digits = self._finding_id_for_seg({"k": "50%25"})
        self.assertNotEqual(id_pct, id_digits)
