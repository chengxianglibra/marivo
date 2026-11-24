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
    ratio_unit,
    render_unit,
    tier1_unit,
    unit_state,
)


def test_tier1_unit_preserves_for_value_aggs() -> None:
    for agg in ("sum", "min", "max", "mean", "median", "percentile"):
        assert tier1_unit(agg, "CNY") == "CNY"
        assert tier1_unit(agg, None) is None


@pytest.mark.parametrize("agg", ("count", "count_distinct", "approx_count_distinct"))
def test_tier1_unit_counts_are_known_unit_one(agg: str) -> None:
    assert tier1_unit(agg, "CNY") == "1"
    assert tier1_unit(agg, None) == "1"


def test_ratio_unit_renders_canonical_quotients() -> None:
    assert ratio_unit("USD.CNY", "s") == "CNY.USD/s"
    assert ratio_unit("{order}", "{order}") == "1"
    assert ratio_unit("1", "{request}") == "1/{request}"


def test_ratio_unit_is_unknown_for_an_opaque_or_unknown_operand() -> None:
    assert ratio_unit("CNY/(request)", "s") is None
    assert ratio_unit("CNY", None) is None


def test_nested_ratio_reduces_without_losing_structure() -> None:
    cny_per_request = divide_unit_states(unit_state("CNY"), unit_state("{request}"))
    seconds_per_request = divide_unit_states(unit_state("s"), unit_state("{request}"))

    assert render_unit(divide_unit_states(cny_per_request, seconds_per_request)) == "CNY/s"


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


def test_authoring_invalid_unit_is_rejected_by_algebra() -> None:
    with pytest.raises(ValueError):
        unit_state("CNY per request")


def test_linear_units_distinguish_canonical_matches_conflicts_and_unknowns() -> None:
    compatible = ("USD.CNY", "CNY.USD")
    assert linear_unit(compatible) == "CNY.USD"
    assert not linear_units_conflict(compatible)

    conflicting = ("CNY", "{order}")
    assert linear_unit(conflicting) is None
    assert linear_units_conflict(conflicting)

    incomplete = ("CNY", None)
    assert linear_unit(incomplete) is None
    assert not linear_units_conflict(incomplete)
