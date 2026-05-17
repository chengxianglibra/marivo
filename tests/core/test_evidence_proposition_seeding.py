"""Tests for app.core.evidence.proposition_seeding pure functions."""

from __future__ import annotations

from marivo.core.evidence.finding_extraction import escape_seg_component
from marivo.core.evidence.proposition_seeding import (
    bilateral_focus_anchor,
    canonical_subject_key,
    decode_seg_component,
    materialize_anomaly_from_candidate,
    materialize_change_from_delta,
    materialize_forecast_from_point,
    parse_correlation_join_basis,
    parse_segment_key,
)

# ---------------------------------------------------------------------------
# decode_seg_component
# ---------------------------------------------------------------------------


def test_decode_seg_component_roundtrip() -> None:
    original = "hello|world=100%"
    encoded = escape_seg_component(original)
    decoded = decode_seg_component(encoded)
    assert decoded == original


def test_decode_seg_component_no_special() -> None:
    assert decode_seg_component("hello") == "hello"


def test_decode_seg_component_percent() -> None:
    assert decode_seg_component("100%25") == "100%"


# ---------------------------------------------------------------------------
# parse_segment_key
# ---------------------------------------------------------------------------


def test_parse_segment_key_valid() -> None:
    result = parse_segment_key("rows:region=US|product=Widget")
    assert result == {"region": "US", "product": "Widget"}


def test_parse_segment_key_not_rows_prefix() -> None:
    assert parse_segment_key("value") is None


def test_parse_segment_key_empty_after_prefix() -> None:
    assert parse_segment_key("rows:") is None


def test_parse_segment_key_malformed_no_equals() -> None:
    assert parse_segment_key("rows:noequals") is None


def test_parse_segment_key_encoded_values() -> None:
    result = parse_segment_key("rows:name=hello%25world")
    assert result == {"name": "hello%world"}


# ---------------------------------------------------------------------------
# parse_correlation_join_basis
# ---------------------------------------------------------------------------


def test_parse_correlation_join_basis_time_aligned() -> None:
    result = parse_correlation_join_basis(
        {"kind": "time_aligned", "grain": "day", "key_fields": ["date"]}
    )
    assert result == {"kind": "time_aligned", "grain": "day", "key_fields": ["date"]}


def test_parse_correlation_join_basis_shared_key() -> None:
    result = parse_correlation_join_basis({"kind": "shared_key", "key_fields": ["user_id"]})
    assert result == {"kind": "shared_key", "key_fields": ["user_id"], "grain": None}


def test_parse_correlation_join_basis_string_returns_none() -> None:
    assert parse_correlation_join_basis("time_aligned") is None


def test_parse_correlation_join_basis_invalid_grain() -> None:
    assert parse_correlation_join_basis({"kind": "time_aligned", "grain": "invalid"}) is None


def test_parse_correlation_join_basis_invalid_kind() -> None:
    assert parse_correlation_join_basis({"kind": "invalid"}) is None


# ---------------------------------------------------------------------------
# canonical_subject_key
# ---------------------------------------------------------------------------


def test_canonical_subject_key_deterministic() -> None:
    s = {"metric": "revenue", "entity": None, "grain": "day", "slice": {}}
    assert canonical_subject_key(s) == canonical_subject_key(s)


def test_canonical_subject_key_ordering() -> None:
    s1 = {"metric": "a", "entity": None, "grain": None, "slice": {}}
    s2 = {"metric": "b", "entity": None, "grain": None, "slice": {}}
    assert canonical_subject_key(s1) < canonical_subject_key(s2)


# ---------------------------------------------------------------------------
# bilateral_focus_anchor
# ---------------------------------------------------------------------------


def test_bilateral_focus_anchor_selects_lexically_smaller() -> None:
    left = {"metric": "a", "entity": None, "grain": None, "slice": {}, "analysis_axis": "x"}
    right = {"metric": "b", "entity": None, "grain": None, "slice": {}, "analysis_axis": "y"}
    result = bilateral_focus_anchor(left, right, "correlation")
    assert result["metric"] == "a"
    assert result["analysis_axis"] == "correlation"


def test_bilateral_focus_anchor_equal_selects_left() -> None:
    s = {"metric": "revenue", "entity": None, "grain": None, "slice": {}}
    left = {**s, "analysis_axis": "x"}
    right = {**s, "analysis_axis": "y"}
    result = bilateral_focus_anchor(left, right, "test")
    assert result["analysis_axis"] == "test"


# ---------------------------------------------------------------------------
# materialize_change_from_delta (T1)
# ---------------------------------------------------------------------------


def _template_t1() -> dict:
    return {
        "template_id": "T1",
        "template_version": "v1",
        "assessment_type": "change_assessment",
        "derivation_version": "v1",
    }


def test_materialize_change_scalar_increase() -> None:
    finding = {
        "finding_id": "fnd_1",
        "artifact_id": "art_1",
        "payload_json": {
            "direction": "increase",
            "presence": "both",
            "delta_kind": "scalar_delta",
            "unit": "USD",
        },
        "subject_json": {
            "metric": "revenue",
            "entity": None,
            "slice": {},
            "grain": None,
            "analysis_axis": "scalar",
        },
        "step_ref_json": {"session_id": "s1", "step_id": "step1", "step_type": "compare"},
    }
    artifact = {
        "resolved_input_summary": {
            "current_time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-02-01"},
            "baseline_time_scope": {"field": "time", "start": "2023-01-01", "end": "2023-02-01"},
        },
    }
    result = materialize_change_from_delta(
        finding=finding, session_id="s1", template=_template_t1(), artifact_payload=artifact
    )
    assert result is not None
    assert result["proposition_type"] == "change"
    assert result["payload"]["direction_of_interest"] == "increase"
    assert result["payload"]["change_kind"] == "scalar_change"


def test_materialize_change_flat_direction_returns_none() -> None:
    finding = {
        "finding_id": "fnd_1",
        "artifact_id": "art_1",
        "payload_json": {"direction": "flat", "presence": "both", "delta_kind": "scalar_delta"},
        "subject_json": {},
        "step_ref_json": {},
    }
    result = materialize_change_from_delta(
        finding=finding, session_id="s1", template=_template_t1(), artifact_payload={}
    )
    assert result is None


# ---------------------------------------------------------------------------
# materialize_anomaly_from_candidate (T3)
# ---------------------------------------------------------------------------


def _template_t3() -> dict:
    return {
        "template_id": "T3",
        "template_version": "v1",
        "assessment_type": "anomaly_assessment",
        "derivation_version": "v1",
    }


def test_materialize_anomaly_valid() -> None:
    finding = {
        "finding_id": "fnd_1",
        "artifact_id": "art_1",
        "payload_json": {"candidate_ref": {"artifact_id": "art_1", "item_ref": {}}},
        "subject_json": {
            "metric": "revenue",
            "entity": None,
            "slice": {},
            "grain": "day",
            "analysis_axis": "anomaly",
        },
        "observed_window_json": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
        "step_ref_json": {},
    }
    result = materialize_anomaly_from_candidate(
        finding=finding, session_id="s1", template=_template_t3()
    )
    assert result is not None
    assert result["proposition_type"] == "anomaly"


def test_materialize_anomaly_missing_candidate_ref_returns_none() -> None:
    finding = {
        "finding_id": "fnd_1",
        "artifact_id": "art_1",
        "payload_json": {"candidate_ref": None},
        "subject_json": {},
        "observed_window_json": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
        "step_ref_json": {},
    }
    result = materialize_anomaly_from_candidate(
        finding=finding, session_id="s1", template=_template_t3()
    )
    assert result is None


# ---------------------------------------------------------------------------
# materialize_forecast_from_point (T6)
# ---------------------------------------------------------------------------


def _template_t6() -> dict:
    return {
        "template_id": "T6",
        "template_version": "v1",
        "assessment_type": "forecast_assessment",
        "derivation_version": "v1",
    }


def test_materialize_forecast_valid() -> None:
    finding = {
        "finding_id": "fnd_1",
        "artifact_id": "art_1",
        "payload_json": {
            "bucket_start": "2024-02-01",
            "bucket_end": "2024-02-08",
            "horizon_index": 1,
        },
        "subject_json": {
            "metric": "revenue",
            "entity": None,
            "slice": {},
            "grain": None,
            "analysis_axis": "forecast",
        },
        "step_ref_json": {},
    }
    result = materialize_forecast_from_point(
        finding=finding, session_id="s1", template=_template_t6()
    )
    assert result is not None
    assert result["proposition_type"] == "forecast"
    assert result["payload"]["horizon_index"] == 1


def test_materialize_forecast_missing_bucket_start_returns_none() -> None:
    finding = {
        "finding_id": "fnd_1",
        "artifact_id": "art_1",
        "payload_json": {"bucket_start": "", "bucket_end": "2024-02-08", "horizon_index": 1},
        "subject_json": {},
        "step_ref_json": {},
    }
    result = materialize_forecast_from_point(
        finding=finding, session_id="s1", template=_template_t6()
    )
    assert result is None
