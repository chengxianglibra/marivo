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


def test_extract_observe_scalar() -> None:
    payload = {
        "observation_type": "scalar",
        "metric": "revenue",
        "value": 100.5,
        "unit": "USD",
        "scope": {"region": "US"},
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-02-01"},
        "analytical_metadata": {"data_complete": True, "quality_status": "ready"},
    }
    step_ref = {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    findings = extract_observe_findings("art_1", payload, step_ref)

    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "observation"
    assert f["payload"]["observation_kind"] == "scalar"
    assert f["payload"]["value"] == 100.5
    assert f["subject"]["analysis_axis"] == "scalar"


def test_extract_observe_time_series_empty() -> None:
    payload = {"observation_type": "time_series", "series": [], "metric": "revenue"}
    findings = extract_observe_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    )
    assert findings == []


def test_extract_observe_time_series_with_buckets() -> None:
    payload = {
        "observation_type": "time_series",
        "metric": "revenue",
        "granularity": "day",
        "unit": "USD",
        "series": [
            {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 50},
            {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 60},
        ],
    }
    findings = extract_observe_findings(
        "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
    )
    assert len(findings) == 2
    assert findings[0]["subject"]["analysis_axis"] == "time"
    assert findings[0]["payload"]["bucket_start"] == "2024-01-01"


def test_extract_observe_unknown_type_raises() -> None:
    payload = {"observation_type": "unknown"}
    with pytest.raises(ValueError, match="Unknown observation_type"):
        extract_observe_findings(
            "art_1", payload, {"session_id": "s1", "step_id": "step1", "step_type": "observe"}
        )


# ---------------------------------------------------------------------------
# COMPARE extraction
# ---------------------------------------------------------------------------


def test_extract_compare_scalar_delta() -> None:
    payload = {
        "comparison_type": "scalar_delta",
        "metric": "revenue",
        "current_value": 100,
        "baseline_value": 120,
        "absolute_delta": 20,
        "relative_delta": 0.2,
        "direction": "increase",
        "unit": "USD",
        "resolved_input_summary": {
            "current_scope": {"region": "US"},
            "current_time_scope": {
                "kind": "range",
                "start": "2024-01-01",
                "end": "2024-02-01",
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
    with pytest.raises(ValueError, match="Unknown comparison_type"):
        extract_compare_findings(
            "art_1",
            {"comparison_type": "unknown"},
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
        "dimension": "region",
        "metric": "revenue",
        "compare_ref": {"artifact_id": "cmp_art_1", "comparison_type": "scalar_delta"},
        "rows": [
            {
                "key": "US",
                "absolute_contribution": 15,
                "contribution_share": 0.75,
                "direction": "increase",
            },
            {
                "key": "EU",
                "absolute_contribution": 5,
                "contribution_share": 0.25,
                "direction": "increase",
            },
        ],
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
    with pytest.raises(ValueError, match="missing required 'dimension'"):
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
