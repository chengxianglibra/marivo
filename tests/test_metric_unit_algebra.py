"""Tests for the pure unit-combination algebra."""

from __future__ import annotations

from marivo.semantic.unit_algebra import (
    linear_unit,
    linear_units_conflict,
    ratio_unit,
    tier1_unit,
    weighted_average_unit,
)


def test_tier1_unit_preserves_for_value_aggs() -> None:
    for agg in ("sum", "min", "max", "mean", "median", "percentile"):
        assert tier1_unit(agg, "CNY") == "CNY"
        assert tier1_unit(agg, None) is None


def test_tier1_unit_counts_are_none() -> None:
    assert tier1_unit("count", "CNY") is None
    assert tier1_unit("count_distinct", "CNY") is None


def test_ratio_unit_same_known_cancels_to_one() -> None:
    assert ratio_unit("CNY", "CNY") == "1"
    assert ratio_unit("{order}", "{order}") == "1"


def test_ratio_unit_differing_or_unknown_is_none() -> None:
    assert ratio_unit("CNY", "{user}") is None
    assert ratio_unit("CNY", None) is None
    assert ratio_unit(None, None) is None


def test_weighted_average_unit_keeps_value_unit() -> None:
    assert weighted_average_unit("CNY") == "CNY"
    assert weighted_average_unit(None) is None


def test_linear_unit_all_same_known() -> None:
    assert linear_unit(["CNY", "CNY"]) == "CNY"


def test_linear_unit_any_none_is_none() -> None:
    assert linear_unit(["CNY", None]) is None
    assert linear_unit([None, None]) is None


def test_linear_unit_distinct_known_is_none() -> None:
    assert linear_unit(["CNY", "{order}"]) is None


def test_linear_units_conflict_only_on_two_distinct_known() -> None:
    assert linear_units_conflict(["CNY", "{order}"]) is True
    assert linear_units_conflict(["CNY", "CNY"]) is False
    assert linear_units_conflict(["CNY", None]) is False
    assert linear_units_conflict([None, None]) is False
