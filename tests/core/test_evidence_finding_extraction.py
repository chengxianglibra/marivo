"""Tests for app.core.evidence.finding_extraction pure functions."""

from __future__ import annotations

import pytest

from marivo.core.evidence.finding_extraction import (
    escape_seg_component,
    extract_compare_findings,
    extract_correlate_findings,
    extract_decompose_findings,
    extract_detect_findings,
    extract_forecast_findings,
    extract_observe_findings,
    extract_test_findings,
    make_canonical_item_key,
    make_finding_id,
    make_item_identity,
    segment_stable_key,
    to_float_or_none,
)

# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def test_make_finding_id_deterministic() -> None:
    id1 = make_finding_id("art_1", "observation", "value")
    id2 = make_finding_id("art_1", "observation", "value")
    assert id1 == id2


def test_make_finding_id_different_inputs() -> None:
    id1 = make_finding_id("art_1", "observation", "value")
    id2 = make_finding_id("art_2", "observation", "value")
    assert id1 != id2


def test_make_finding_id_format() -> None:
    fid = make_finding_id("art_1", "observation", "value")
    assert fid.startswith("fnd_")
    assert len(fid) == 4 + 24  # "fnd_" + 24 hex chars


def test_make_canonical_item_key_key_priority() -> None:
    assert make_canonical_item_key("value", key="k1") == "value:k1"


def test_make_canonical_item_key_index_priority() -> None:
    assert make_canonical_item_key("value", index=0) == "value:0"


def test_make_canonical_item_key_collection_only() -> None:
    assert make_canonical_item_key("value") == "value"


def test_make_canonical_item_key_key_over_index() -> None:
    assert make_canonical_item_key("value", key="k1", index=0) == "value:k1"


def test_make_item_identity_key() -> None:
    cik, ref = make_item_identity("buckets", key="2024-01-01/2024-01-08")
    assert cik == "buckets:2024-01-01/2024-01-08"
    assert ref["collection"] == "buckets"
    assert ref["key"] == "2024-01-01/2024-01-08"
    assert ref["index"] is None


def test_make_item_identity_index() -> None:
    cik, ref = make_item_identity("candidates", index=3)
    assert cik == "candidates:3"
    assert ref["index"] == 3
    assert ref["key"] is None


def test_to_float_or_none_none() -> None:
    assert to_float_or_none(None) is None


def test_to_float_or_none_int() -> None:
    assert to_float_or_none(42) == 42.0


def test_to_float_or_none_str_numeric() -> None:
    assert to_float_or_none("3.14") == 3.14


def test_to_float_or_none_str_non_numeric() -> None:
    assert to_float_or_none("abc") is None


def test_to_float_or_none_bool() -> None:
    assert to_float_or_none(True) == 1.0


# ---------------------------------------------------------------------------
# Segment key helpers
# ---------------------------------------------------------------------------


def test_escape_seg_component_no_special() -> None:
    assert escape_seg_component("hello") == "hello"


def test_escape_seg_component_pipe() -> None:
    assert escape_seg_component("a|b") == "a%7Cb"


def test_escape_seg_component_equals() -> None:
    assert escape_seg_component("a=b") == "a%3Db"


def test_escape_seg_component_percent() -> None:
    assert escape_seg_component("100%") == "100%25"


def test_escape_seg_component_percent_before_pipe() -> None:
    result = escape_seg_component("a%|b")
    assert result == "a%25%7Cb"


def test_segment_stable_key_sorted() -> None:
    keys = {"b": "2", "a": "1"}
    result = segment_stable_key(keys)
    assert result == "a=1|b=2"


def test_segment_stable_key_empty() -> None:
    assert segment_stable_key({}) == ""


# ---------------------------------------------------------------------------
# OBSERVE extraction
# ---------------------------------------------------------------------------


def _metric_frame_payload(
    shape: str,
    *,
    series: list[dict[str, object]] | None = None,
    scope: dict[str, object] | None = None,
    unit: str | None = "USD",
) -> dict[str, object]:
    axes: list[dict[str, str]] = []
    if shape == "time_series":
        axes = [{"kind": "time", "grain": "day"}]
        if series is None:
            series = [
                {
                    "keys": {},
                    "points": [
                        {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 50},
                        {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": None},
                    ],
                }
            ]
    elif shape == "segmented":
        axes = [{"kind": "dimension", "name": "region"}]
        if series is None:
            series = [
                {"keys": {"region": "US"}, "points": [{"value": 50}]},
                {"keys": {"region": "EU"}, "points": [{"value": 60}]},
            ]
    elif shape == "panel":
        axes = [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}]
        if series is None:
            series = [
                {
                    "keys": {"region": "US"},
                    "points": [
                        {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 50}
                    ],
                },
                {
                    "keys": {"region": "EU"},
                    "points": [
                        {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 60}
                    ],
                },
            ]
    else:
        if series is None:
            series = [{"keys": {}, "points": [{"value": 100.5}]}]

    return {
        "artifact_family": "metric_frame",
        "shape": shape,
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.revenue",
            "scope": scope or {"country": "US"},
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-02-01"},
        },
        "axes": axes,
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": unit}],
        "payload": {"series": series},
    }


def test_extract_observe_scalar() -> None:
    payload = _metric_frame_payload("scalar", scope={"region": "US"})
    step_ref = {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    findings = extract_observe_findings("art_1", payload, step_ref)

    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "observation"
    assert f["payload"]["observation_kind"] == "scalar"
    assert f["payload"]["value"] == 100.5
    assert f["payload"]["unit"] == "USD"
    assert f["subject"]["metric"] == "metric.revenue"
    assert f["subject"]["slice"] == {"region": "US"}
    assert f["subject"]["analysis_axis"] == "scalar"
    assert f["observed_window"] == payload["subject"]["time_scope"]
    assert f["quality"]["quality_status"] is None
    assert f["provenance"]["extractor_name"] == "observe_metric_frame_v1"
    assert f["provenance"]["artifact_schema_version"] is None


def test_extract_observe_time_series_empty() -> None:
    payload = _metric_frame_payload("time_series", series=[])
    findings = extract_observe_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    )
    assert findings == []


def test_extract_observe_time_series_with_buckets() -> None:
    payload = _metric_frame_payload("time_series")
    findings = extract_observe_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    )
    assert len(findings) == 2
    assert findings[0]["subject"]["analysis_axis"] == "time"
    assert findings[0]["subject"]["grain"] == "day"
    assert findings[0]["payload"]["bucket_start"] == "2024-01-01"
    assert findings[1]["payload"]["value"] is None
    assert findings[0]["provenance"]["canonical_item_key"] == "buckets:2024-01-01/2024-01-02"


def test_extract_observe_segmented_from_metric_frame() -> None:
    payload = _metric_frame_payload("segmented", scope={"region": "GLOBAL"})
    findings = extract_observe_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    )
    assert len(findings) == 2
    assert findings[0]["subject"]["analysis_axis"] == "segment"
    assert findings[0]["subject"]["slice"] == {"region": "US"}
    assert findings[0]["payload"]["observation_kind"] == "segment"
    assert findings[0]["payload"]["keys"] == {"region": "US"}


def test_extract_observe_panel_from_metric_frame() -> None:
    payload = _metric_frame_payload("panel", scope={"market": "global"})
    findings = extract_observe_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    )
    assert len(findings) == 2
    assert findings[0]["subject"]["analysis_axis"] == "time"
    assert findings[0]["subject"]["slice"] == {"market": "global", "region": "US"}
    assert findings[0]["payload"]["observation_kind"] == "time_bucket"
    assert findings[0]["provenance"]["canonical_item_key"] == (
        "buckets:region=US|2024-01-01/2024-01-02"
    )


def test_extract_observe_non_metric_frame_raises() -> None:
    payload = {"artifact_family": "observation", "shape": "scalar"}
    with pytest.raises(ValueError, match="artifact_family='metric_frame'"):
        extract_observe_findings(
            "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
        )


def test_extract_observe_unknown_metric_frame_shape_raises() -> None:
    payload = {"artifact_family": "metric_frame", "shape": "unknown"}
    with pytest.raises(ValueError, match="Unknown metric_frame shape"):
        extract_observe_findings(
            "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
        )


# ---------------------------------------------------------------------------
# COMPARE extraction
# ---------------------------------------------------------------------------


def test_extract_compare_scalar_delta() -> None:
    payload = {
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "metric": "revenue",
        "unit": "USD",
        "resolved_input_summary": {
            "current_scope": {"region": "US"},
            "current_time_scope": {
                "field": "time",
                "start": "2024-01-01",
                "end": "2024-02-01",
            },
        },
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "current_value": 100,
                            "baseline_value": 120,
                            "delta_abs": 20,
                            "delta_pct": 0.2,
                            "direction": "increase",
                        }
                    ],
                }
            ],
            "scope": {
                "current_value": 100,
                "baseline_value": 120,
                "delta_abs": 20,
                "delta_pct": 0.2,
                "direction": "increase",
            },
        },
    }
    findings = extract_compare_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "compare"}
    )
    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "delta"
    assert f["payload"]["delta_kind"] == "scalar_delta"
    assert f["payload"]["direction"] == "increase"


def test_extract_compare_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown delta_frame shape"):
        extract_compare_findings(
            "art_1",
            {"artifact_family": "delta_frame", "shape": "unknown"},
            {"session_id": "s1", "step_id": "step1", "step_type": "compare"},
        )


# ---------------------------------------------------------------------------
# DETECT extraction
# ---------------------------------------------------------------------------


def test_extract_detect_empty_candidates() -> None:
    payload = {"metric": "revenue", "candidates": []}
    findings = extract_detect_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "detect"}
    )
    assert findings == []


def test_extract_detect_time_bucket_candidate() -> None:
    payload = {
        "metric": "revenue",
        "granularity": "day",
        "scope": {},
        "candidates": [
            {
                "window": {"start": "2024-01-15", "end": "2024-01-16"},
                "candidate_score": 3.5,
                "flag_level": "high",
                "current_value": 200,
                "baseline_value": 100,
                "deviation_abs": 100,
                "deviation_pct": 1.0,
            },
        ],
    }
    findings = extract_detect_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "detect"}
    )
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "anomaly_candidate"
    assert findings[0]["subject"]["analysis_axis"] == "time"
    assert findings[0]["payload"]["flag_level"] == "high"


# ---------------------------------------------------------------------------
# DECOMPOSE extraction
# ---------------------------------------------------------------------------


def test_extract_decompose_basic() -> None:
    payload = {
        "metric": "revenue",
        "axes": [{"kind": "dimension", "name": "region"}],
        "compare_ref": {"artifact_id": "cmp_art_1", "shape": "scalar_delta"},
        "payload": {
            "series": [
                {
                    "keys": {"region": "US"},
                    "points": [
                        {
                            "contribution_abs": 15,
                            "contribution_pct": 0.75,
                            "direction": "increase",
                        }
                    ],
                },
                {
                    "keys": {"region": "EU"},
                    "points": [
                        {
                            "contribution_abs": 5,
                            "contribution_pct": 0.25,
                            "direction": "increase",
                        }
                    ],
                },
            ]
        },
    }
    findings = extract_decompose_findings(
        "art_1",
        payload,
        {"session_id": "s1", "step_id": "step1", "step_type": "decompose"},
        session_id="s1",
    )
    assert len(findings) == 2
    assert findings[0]["finding_type"] == "decomposition_item"
    assert findings[0]["payload"]["rank"] == 1
    assert findings[1]["payload"]["rank"] == 2
    assert findings[0]["payload"]["scope_delta_ref"]["session_id"] == "s1"


def test_extract_decompose_missing_dimension_raises() -> None:
    with pytest.raises(ValueError, match="missing required dimension axis"):
        extract_decompose_findings(
            "art_1",
            {"compare_ref": {"artifact_id": "a"}},
            {"session_id": "s1", "step_id": "step1", "step_type": "decompose"},
            session_id="s1",
        )


# ---------------------------------------------------------------------------
# CORRELATE extraction
# ---------------------------------------------------------------------------


def test_extract_correlate_basic() -> None:
    payload = {
        "left_metric": "revenue",
        "left_ref": {"artifact_id": "left_art"},
        "right_ref": {"artifact_id": "right_art"},
        "statistic": {"method": "spearman", "coefficient": 0.85, "p_value": 0.01, "n_pairs": 30},
        "analytical_metadata": {"pairing_rule": "time_aligned"},
    }
    findings = extract_correlate_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "correlate"}
    )
    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "correlation_result"
    assert f["payload"]["coefficient"] == 0.85


# ---------------------------------------------------------------------------
# FORECAST extraction
# ---------------------------------------------------------------------------


def test_extract_forecast_basic() -> None:
    payload = {
        "metric": "revenue",
        "forecast": [
            {
                "window": {"start": "2024-02-01", "end": "2024-02-08"},
                "point_forecast": 110,
                "bucket_index": 1,
            },
            {
                "window": {"start": "2024-02-08", "end": "2024-02-15"},
                "point_forecast": 115,
                "bucket_index": 2,
            },
        ],
    }
    findings = extract_forecast_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "forecast"}
    )
    assert len(findings) == 2
    assert findings[0]["finding_type"] == "forecast_point"
    assert findings[0]["payload"]["horizon_index"] == 1
    assert findings[0]["payload"]["predicted_value"] == 110.0


def test_extract_forecast_panel_series_keys_disambiguate_findings() -> None:
    payload = {
        "metric": "revenue",
        "forecast": [
            {
                "keys": {"region": "US"},
                "points": [
                    {
                        "window": {"start": "2024-02-01", "end": "2024-02-08"},
                        "point_forecast": 110,
                        "bucket_index": 1,
                    }
                ],
            },
            {
                "keys": {"region": "EU"},
                "points": [
                    {
                        "window": {"start": "2024-02-01", "end": "2024-02-08"},
                        "point_forecast": 115,
                        "bucket_index": 1,
                    }
                ],
            },
        ],
    }

    findings = extract_forecast_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "forecast"}
    )

    assert len(findings) == 2
    assert findings[0]["finding_id"] != findings[1]["finding_id"]
    assert findings[0]["subject"]["slice"] == {"region": "US"}
    assert findings[1]["subject"]["slice"] == {"region": "EU"}


# ---------------------------------------------------------------------------
# TEST extraction
# ---------------------------------------------------------------------------


def test_extract_test_basic() -> None:
    payload = {
        "method": "welch_t",
        "current_ref": {"artifact_id": "left_art"},
        "baseline_ref": {"artifact_id": "right_art"},
        "statistic": {"name": "t", "value": 2.5},
        "estimate": {"value": 0.15},
        "hypothesis": {"alpha": 0.05},
        "decision": {"reject_null": True},
        "p_value": 0.02,
    }
    findings = extract_test_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "test"}
    )
    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "test_result"
    assert f["payload"]["method"] == "welch_t"
    assert f["payload"]["alpha"] == 0.05
    assert f["payload"]["reject_null"] is True
