"""Tests for candidate_set -> anomaly_candidate finding extraction."""

# ruff: noqa: I001
from __future__ import annotations

import unittest
from typing import Any

from marivo.core.evidence.canonical_finding import StepRef
from marivo.runtime.evidence.finding_extractor_registry import (
    default_finding_registry,
    validate_for_commit,
)
from marivo.runtime.evidence.detect_extractor import DetectArtifactExtractor
from tests.finding_identity_testutil import (
    assert_finding_id_stable,
    assert_projection_order_excluded,
    assert_stable_key_beats_index,
)

_ART_ID = "art_detect_test001"
_SESSION_ID = "sess_det_test"
_STEP_REF: StepRef = StepRef(
    session_id=_SESSION_ID,
    step_id="step_det_001",
    step_type="detect",
)

_EXTRACTOR = DetectArtifactExtractor()


def _candidate_set_payload(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "artifact_id": _ART_ID,
        "artifact_family": "candidate_set",
        "shape": "point_anomaly_candidates",
        "subject": {
            "kind": "candidate_scan",
            "metric_ref": "metric.revenue",
            "source_artifact_id": "artifact_source",
            "source_artifact_family": "metric_frame",
            "source_shape": "time_series",
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "score", "value_type": "number", "nullable": False}],
        "capabilities": ["filterable"],
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": ["artifact_source"],
            "strategy": "point_anomaly",
        },
        "payload": {
            "items": items if items is not None else [],
            "scan_summary": {
                "scanned_series_count": 1,
                "total_candidate_count": len(items or []),
            },
            "truncation": {
                "returned_candidate_count": len(items or []),
                "total_candidate_count": len(items or []),
                "truncated": False,
            },
            "quality": {"status": "detectable", "issues": []},
        },
    }


def _point_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "item_id": "2026-01-05T00:00:00Z",
        "source_point_ref": {
            "artifact_id": "artifact_source",
            "series_index": 0,
            "point_index": 4,
            "series_keys": {},
            "point_key": "2026-01-05T00:00:00Z",
        },
        "window": {
            "start": "2026-01-05T00:00:00Z",
            "end": "2026-01-06T00:00:00Z",
        },
        "keys": None,
        "value": 200.0,
        "baseline_value": 111.0,
        "delta_abs": 89.0,
        "delta_pct": 0.8018,
        "score": 2.8,
        "direction": "increase",
    }
    item.update(overrides)
    return item


def test_extract_candidate_set_empty_candidates() -> None:
    result = _EXTRACTOR.extract(_ART_ID, _candidate_set_payload(), _STEP_REF, _SESSION_ID)

    assert result["findings"] == []
    assert result["finding_count"] == 0
    assert result["extractor_name"] == "detect_candidate_set_v1"
    assert result["artifact_schema_version"] is None
    validate_for_commit("detect", result)


def test_extract_candidate_set_item_to_anomaly_candidate() -> None:
    result = _EXTRACTOR.extract(
        _ART_ID,
        _candidate_set_payload([_point_item()]),
        _STEP_REF,
        _SESSION_ID,
    )

    assert result["finding_count"] == 1
    finding = result["findings"][0]
    assert finding["finding_type"] == "anomaly_candidate"
    assert finding["subject"]["metric"] == "metric.revenue"
    assert finding["subject"]["analysis_axis"] == "time"
    assert finding["subject"]["slice"] == {}
    assert finding["subject"]["grain"] == "day"
    assert finding["observed_window"] == {
        "field": "time",
        "start": "2026-01-05T00:00:00Z",
        "end": "2026-01-06T00:00:00Z",
    }
    assert finding["provenance"]["canonical_item_key"] == "candidates:2026-01-05T00:00:00Z"
    assert finding["payload"]["candidate_ref"] == {
        "artifact_id": _ART_ID,
        "item_ref": {
            "collection": "candidates",
            "index": None,
            "key": "2026-01-05T00:00:00Z",
        },
    }
    assert finding["payload"]["source_point_ref"]["artifact_id"] == "artifact_source"
    assert finding["payload"]["source_delta_point_ref"] is None
    assert finding["payload"]["score"] == 2.8
    assert finding["payload"]["current_value"] == 200.0
    assert finding["payload"]["baseline_value"] == 111.0
    assert finding["payload"]["deviation_absolute"] == 89.0
    assert finding["payload"]["deviation_relative"] == 0.8018
    assert finding["payload"]["direction"] == "increase"


def test_panel_candidate_uses_keys_and_window_identity_when_item_id_missing() -> None:
    result = _EXTRACTOR.extract(
        _ART_ID,
        _candidate_set_payload(
            [
                _point_item(
                    item_id=None,
                    keys={"country": "US", "device": "iOS"},
                    source_point_ref={
                        "artifact_id": "artifact_source",
                        "series_index": 2,
                        "point_index": 4,
                        "series_keys": {"country": "US", "device": "iOS"},
                        "point_key": "2026-01-05T00:00:00Z",
                    },
                )
            ]
        ),
        _STEP_REF,
        _SESSION_ID,
    )

    finding = result["findings"][0]
    assert finding["subject"]["analysis_axis"] == "panel"
    assert finding["subject"]["slice"] == {"country": "US", "device": "iOS"}
    assert (
        finding["provenance"]["canonical_item_key"]
        == "candidates:2026-01-05T00:00:00Z|country=US|device=iOS"
    )


def test_window_only_candidate_uses_window_identity_when_item_id_missing() -> None:
    result = _EXTRACTOR.extract(
        _ART_ID,
        _candidate_set_payload([_point_item(item_id="")]),
        _STEP_REF,
        _SESSION_ID,
    )

    finding = result["findings"][0]
    assert finding["subject"]["analysis_axis"] == "time"
    assert finding["provenance"]["canonical_item_key"] == "candidates:2026-01-05T00:00:00Z"


def test_candidate_without_item_id_or_window_falls_back_to_index() -> None:
    result = _EXTRACTOR.extract(
        _ART_ID,
        _candidate_set_payload(
            [
                _point_item(
                    item_id=None,
                    window=None,
                    keys=None,
                    direction="sideways",
                    source_point_ref=None,
                )
            ]
        ),
        _STEP_REF,
        _SESSION_ID,
    )

    finding = result["findings"][0]
    assert finding["subject"]["analysis_axis"] == "scalar"
    assert finding["observed_window"] is None
    assert finding["provenance"]["canonical_item_key"] == "candidates:0"
    assert finding["provenance"]["artifact_item_ref"] == {
        "collection": "candidates",
        "index": 0,
        "key": None,
    }
    assert finding["payload"]["direction"] is None


def test_period_shift_source_delta_ref_is_mapped() -> None:
    item = _point_item(
        source_point_ref=None,
        source_delta_point_ref={
            "artifact_id": "artifact_delta",
            "series_index": 1,
            "point_index": 3,
            "series_keys": {"country": "US"},
            "point_key": "2026-01-05T00:00:00Z",
        },
        direction="decrease",
    )
    payload = _candidate_set_payload([item])
    payload["shape"] = "period_shift_candidates"
    payload["subject"]["source_artifact_family"] = "delta_frame"
    payload["subject"]["source_shape"] = "time_series_delta"

    result = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION_ID)

    finding_payload = result["findings"][0]["payload"]
    assert finding_payload["source_point_ref"] is None
    assert finding_payload["source_delta_point_ref"]["artifact_id"] == "artifact_delta"
    assert finding_payload["direction"] == "decrease"


def test_finding_id_stable_on_replay() -> None:
    payload = _candidate_set_payload([_point_item()])
    result1 = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION_ID)
    result2 = _EXTRACTOR.extract(_ART_ID, payload, _STEP_REF, _SESSION_ID)
    assert result1["findings"][0]["finding_id"] == result2["findings"][0]["finding_id"]


def test_finding_identity_helpers_still_apply() -> None:
    tc = unittest.TestCase()
    assert_finding_id_stable(
        tc,
        _ART_ID,
        "anomaly_candidate",
        "candidates",
        key="2026-01-05T00:00:00Z",
    )
    assert_stable_key_beats_index(tc, "candidates", "2026-01-05T00:00:00Z", 0)
    assert_projection_order_excluded(
        tc,
        _ART_ID,
        "anomaly_candidate",
        "candidates",
        "2026-01-05T00:00:00Z",
    )


def test_registered_under_candidate_set_none_version() -> None:
    assert ("candidate_set", None) in default_finding_registry.registered_keys()
    assert isinstance(default_finding_registry.find("candidate_set", None), DetectArtifactExtractor)
    assert default_finding_registry.find("anomaly_candidates", "v1") is None
    assert ("anomaly_candidates", "v1") not in default_finding_registry.registered_keys()


def test_extractor_class_vars_correct() -> None:
    assert DetectArtifactExtractor.artifact_type == "candidate_set"
    assert DetectArtifactExtractor.artifact_schema_version is None
    assert DetectArtifactExtractor.family == "detect"
    assert DetectArtifactExtractor.extractor_name == "detect_candidate_set_v1"
    assert DetectArtifactExtractor.finding_schema_version == "v1"
