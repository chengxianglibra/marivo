"""Tests for union-of-columns CandidateSet helpers."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from marivo.analysis.errors import FrameMetaInvalidError
from marivo.analysis.intents._candidate_columns import (
    CANDIDATE_COLUMNS,
    CANDIDATE_DTYPES,
    build_union_columns,
    empty_candidate_frame,
    validate_shape_columns,
)


def test_candidate_columns_have_fixed_order() -> None:
    assert CANDIDATE_COLUMNS == [
        "item_id",
        "score",
        "direction",
        "reason_codes_json",
        "source_refs_json",
        "selector_json",
        "keys_json",
        "window_start",
        "window_end",
        "baseline_window_start",
        "baseline_window_end",
        "axis",
        "axis_semantic_id",
        "peer_scope_json",
        "recommended_followups_json",
    ]


def test_empty_candidate_frame_has_full_schema() -> None:
    df = empty_candidate_frame()
    assert list(df.columns) == CANDIDATE_COLUMNS
    assert len(df) == 0
    for column, dtype in CANDIDATE_DTYPES.items():
        assert str(df[column].dtype) == dtype


def test_build_union_columns_fills_unused_fields_with_neutral_defaults() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 3.5,
            "axis": "country",
            "reason_codes": ["top_k_share=0.6"],
            "source_refs": ["frame_src#axis=country"],
        }
    ]
    df = build_union_columns("driver_axis", rows)
    assert list(df.columns) == CANDIDATE_COLUMNS
    assert df.loc[0, "item_id"] == "cand_0"
    assert df.loc[0, "axis"] == "country"
    assert df.loc[0, "reason_codes_json"] == '["top_k_share=0.6"]'
    assert df.loc[0, "source_refs_json"] == '["frame_src#axis=country"]'
    assert df.loc[0, "selector_json"] == ""
    assert df.loc[0, "keys_json"] == ""
    assert pd.isna(df.loc[0, "axis_semantic_id"])
    assert df.loc[0, "peer_scope_json"] == ""
    assert df.loc[0, "recommended_followups_json"] == "[]"
    assert pd.isna(df.loc[0, "window_start"])
    assert pd.isna(df.loc[0, "direction"])


def test_build_union_columns_serializes_selector_and_keys() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 1.5,
            "selector": {"country": "US", "platform": "mobile"},
            "keys": {"country": "US", "platform": "mobile"},
        }
    ]
    df = build_union_columns("slice", rows)
    assert json.loads(df.loc[0, "selector_json"]) == {
        "country": "US",
        "platform": "mobile",
    }
    assert json.loads(df.loc[0, "keys_json"]) == {
        "country": "US",
        "platform": "mobile",
    }


def test_build_union_columns_keeps_non_ascii_json_readable() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 1.5,
            "selector": {"department": "München Analytics"},
            "keys": {"department": "München Analytics"},
        }
    ]
    df = build_union_columns("slice", rows)

    assert df.loc[0, "selector_json"] == '{"department": "München Analytics"}'
    assert df.loc[0, "keys_json"] == '{"department": "München Analytics"}'
    assert "\\u" not in df.loc[0, "keys_json"]


def test_build_union_columns_serializes_window_to_datetime_columns() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 4.0,
            "direction": "high",
            "window": {"start": "2026-01-15", "end": "2026-01-21"},
        }
    ]
    df = build_union_columns("window", rows)
    assert pd.notna(df.loc[0, "window_start"])
    assert pd.notna(df.loc[0, "window_end"])


def test_validate_shape_columns_passes_for_well_formed_point_anomaly() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 3.5,
            "direction": "high",
            "window": {"start": "2026-01-15", "end": "2026-01-15"},
        }
    ]
    df = build_union_columns("point_anomaly", rows)
    validate_shape_columns("point_anomaly", df)  # must not raise


def test_validate_shape_columns_rejects_missing_required_field() -> None:
    rows = [{"item_id": "cand_0", "score": 3.5}]  # no direction, no window
    df = build_union_columns("point_anomaly", rows)
    with pytest.raises(FrameMetaInvalidError) as exc:
        validate_shape_columns("point_anomaly", df)
    assert exc.value.details.get("shape") == "point_anomaly"


def test_validate_shape_columns_rejects_unexpected_axis_for_point_anomaly() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 3.5,
            "direction": "high",
            "window": {"start": "2026-01-15", "end": "2026-01-15"},
            "axis": "country",
        }
    ]
    df = build_union_columns("point_anomaly", rows)
    with pytest.raises(FrameMetaInvalidError) as exc:
        validate_shape_columns("point_anomaly", df)
    assert exc.value.details.get("column") == "axis"


def test_validate_shape_columns_rejects_invalid_followup_payload() -> None:
    rows = [
        {
            "item_id": "cand_0",
            "score": 3.5,
            "axis": "country",
        }
    ]
    df = build_union_columns("driver_axis", rows)
    df.loc[0, "recommended_followups_json"] = '{"bad": "shape"}'
    with pytest.raises(FrameMetaInvalidError):
        validate_shape_columns("driver_axis", df)


@pytest.mark.parametrize(
    "shape, required_extras, allowed_extras",
    [
        (
            "point_anomaly",
            {"window_start", "window_end", "direction"},
            {"keys_json", "baseline_window_start", "baseline_window_end"},
        ),
        (
            "period_shift",
            {
                "window_start",
                "window_end",
                "baseline_window_start",
                "baseline_window_end",
                "direction",
            },
            {"keys_json"},
        ),
        ("driver_axis", {"axis"}, {"axis_semantic_id"}),
        ("slice", {"selector_json", "keys_json"}, {"window_start", "window_end"}),
        ("window", {"window_start", "window_end"}, {"keys_json"}),
        (
            "cross_sectional_outlier",
            {"keys_json", "direction"},
            {"peer_scope_json"},
        ),
    ],
)
def test_required_and_allowed_columns_per_shape(
    shape: str,
    required_extras: set[str],
    allowed_extras: set[str],
) -> None:
    from marivo.analysis.intents._candidate_columns import (
        ALLOWED_OPTIONAL_COLUMNS_BY_SHAPE,
        REQUIRED_COLUMNS_BY_SHAPE,
    )

    common = {
        "item_id",
        "score",
        "reason_codes_json",
        "source_refs_json",
        "recommended_followups_json",
    }
    assert REQUIRED_COLUMNS_BY_SHAPE[shape] == common | required_extras
    assert ALLOWED_OPTIONAL_COLUMNS_BY_SHAPE[shape] == allowed_extras
