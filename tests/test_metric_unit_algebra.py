"""Tests for the pure unit-combination algebra."""

from __future__ import annotations

import pytest

from marivo.semantic.unit_algebra import (
    FactorizedUnitV2,
    OpaqueUnitV2,
    UnknownUnitV2,
    divide_unit_states,
    linear_unit,
    linear_units_conflict,
    multiply_unit_states,
    ratio_unit,
    render_unit,
    tier1_unit,
    unit_state,
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


def test_ratio_unit_differing_known_units_form_canonical_quotient() -> None:
    assert ratio_unit("CNY", "{user}") == "CNY/{user}"
    assert ratio_unit("USD.CNY", "s") == "CNY.USD/s"


def test_ratio_unit_unknown_operand_is_unknown() -> None:
    assert ratio_unit("CNY", None) is None
    assert ratio_unit(None, None) is None


def test_nested_ratio_reduces_without_losing_structure() -> None:
    cny_per_request = divide_unit_states(unit_state("CNY"), unit_state("{request}"))
    seconds_per_request = divide_unit_states(unit_state("s"), unit_state("{request}"))

    assert render_unit(divide_unit_states(cny_per_request, seconds_per_request)) == "CNY/s"


def test_dimensionless_is_empty_product() -> None:
    assert ratio_unit("CNY", "1") == "CNY"
    assert ratio_unit("1", "1") == "1"
    assert ratio_unit("1", "{request}") == "1/{request}"


def test_repeated_factors_and_multiplication_are_canonical() -> None:
    multiplied = multiply_unit_states(unit_state("s.CNY"), unit_state("s/{request}"))

    assert render_unit(multiplied) == "CNY.s.s/{request}"


def test_unit_state_distinguishes_factorized_opaque_and_unknown() -> None:
    assert unit_state("CNY/USD") == FactorizedUnitV2(
        schema="metric-unit-algebra/v2",
        numerator=("CNY",),
        denominator=("USD",),
    )
    assert unit_state("CNY/(request)") == OpaqueUnitV2(
        schema="metric-unit-opaque/v2",
        value="CNY/(request)",
    )
    assert unit_state(None) == UnknownUnitV2(schema="metric-unit-unknown/v2")


def test_opaque_unit_is_authoritative_but_cannot_be_divided() -> None:
    assert render_unit(unit_state("CNY/(request)")) == "CNY/(request)"
    assert ratio_unit("CNY/(request)", "s") is None


def test_authoring_invalid_unit_is_rejected_by_algebra() -> None:
    with pytest.raises(ValueError, match="printable ASCII"):
        unit_state("CNY per request")


def test_weighted_average_unit_keeps_value_unit() -> None:
    assert weighted_average_unit("CNY") == "CNY"
    assert weighted_average_unit(None) is None


def test_linear_unit_all_same_known() -> None:
    assert linear_unit(["CNY", "CNY"]) == "CNY"
    assert linear_unit(["USD.CNY", "CNY.USD"]) == "CNY.USD"


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
    assert linear_units_conflict(["USD.CNY", "CNY.USD"]) is False
