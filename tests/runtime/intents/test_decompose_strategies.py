"""Tests for the decomposition strategy dispatcher and quality evaluator."""

from __future__ import annotations

import pytest

from marivo.runtime.intents.decompose_strategies import (
    delta_share_strategy,
    dispatch_decomposition_strategy,
    ratio_decomposition_strategy,
    weighted_decomposition_strategy,
)


class TestDispatchDecompositionStrategy:
    def test_dispatch_sum_returns_delta_share(self):
        result = dispatch_decomposition_strategy(
            aggregation_semantics="sum",
            left_map={"A": 100, "B": 50},
            right_map={"A": 80, "B": 60},
            scope_absolute_delta=10.0,
            dimension="region",
        )
        assert result.method == "delta_share"
        assert result.quality.recommended_use in ("trusted", "exploratory", "reject")

    def test_dispatch_ratio_returns_ratio_decomposition(self):
        result = dispatch_decomposition_strategy(
            aggregation_semantics="ratio",
            left_map={"A": 30, "B": 10},
            right_map={"A": 20, "B": 15},
            scope_absolute_delta=5.0,
            dimension="region",
            numerator_left_map={"A": 30, "B": 10},
            numerator_right_map={"A": 20, "B": 15},
            denominator_left_map={"A": 100, "B": 50},
            denominator_right_map={"A": 100, "B": 50},
            scope_ratio_delta=0.05,
        )
        assert result.method == "ratio_decomposition"
        assert result.quality.confidence_grade == "medium"
        assert result.quality.recommended_use == "exploratory"

    def test_dispatch_weighted_returns_weighted_decomposition(self):
        result = dispatch_decomposition_strategy(
            aggregation_semantics="weighted_average",
            left_map={"A": 300, "B": 50},
            right_map={"A": 200, "B": 90},
            scope_absolute_delta=60.0,
            dimension="region",
            numerator_left_map={"A": 300, "B": 50},
            numerator_right_map={"A": 200, "B": 90},
            weight_left_map={"A": 100, "B": 50},
            weight_right_map={"A": 80, "B": 60},
            scope_weighted_delta=0.5,
        )
        assert result.method == "weighted_decomposition"
        assert result.quality.confidence_grade == "medium"

    def test_dispatch_unknown_semantics_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown aggregation_semantics"):
            dispatch_decomposition_strategy(
                aggregation_semantics="unknown",
                left_map={},
                right_map={},
                scope_absolute_delta=0.0,
                dimension="region",
            )


class TestDeltaShareStrategy:
    def test_basic_delta_share(self):
        result = delta_share_strategy(
            left_map={"A": 100, "B": 50},
            right_map={"A": 80, "B": 60},
            scope_absolute_delta=10.0,
            dimension="region",
        )
        assert result.method == "delta_share"
        # A: 100-80=20, B: 50-60=-10 => total explained=10 == scope_delta
        assert len(result.rows) == 2
        a_row = next(r for r in result.rows if r["key"] == "A")
        b_row = next(r for r in result.rows if r["key"] == "B")
        assert a_row["absolute_contribution"] == 20.0
        assert b_row["absolute_contribution"] == -10.0
        assert result.unexplained_absolute_delta == 0.0
        assert result.unexplained_share == 0.0
        assert result.quality.reconciliation_status == "reconciled"

    def test_current_only_presence(self):
        result = delta_share_strategy(
            left_map={"NEW": 50},
            right_map={},
            scope_absolute_delta=50.0,
            dimension="region",
        )
        assert len(result.rows) == 1
        assert result.rows[0]["presence"] == "current_only"
        assert result.rows[0]["absolute_contribution"] == 50.0

    def test_baseline_only_presence(self):
        result = delta_share_strategy(
            left_map={},
            right_map={"OLD": 30},
            scope_absolute_delta=-30.0,
            dimension="region",
        )
        assert len(result.rows) == 1
        assert result.rows[0]["presence"] == "baseline_only"
        assert result.rows[0]["absolute_contribution"] == -30.0

    def test_null_scope_delta_issues_data_incomplete(self):
        result = delta_share_strategy(
            left_map={"A": 100},
            right_map={"A": 80},
            scope_absolute_delta=None,
            dimension="region",
        )
        assert any(i["code"] == "data_incomplete" for i in result.issues)
        assert result.unexplained_absolute_delta is None
        assert result.unexplained_share is None

    def test_empty_rows_raises_not_attributable(self):
        with pytest.raises(ValueError, match="NOT_ATTRIBUTABLE"):
            delta_share_strategy(
                left_map={},
                right_map={},
                scope_absolute_delta=0.0,
                dimension="region",
            )

    def test_reconciliation_gap_exceeds_5_percent(self):
        result = delta_share_strategy(
            left_map={"A": 100},
            right_map={"A": 80},
            scope_absolute_delta=5.0,  # scope says delta=5, but A explains 20
            dimension="region",
        )
        assert result.quality.reconciliation_status == "unreconcilable"
        assert result.quality.confidence_grade == "low"
        assert result.quality.recommended_use == "reject"


class TestQualityEvaluation:
    def test_sum_reconciled_within_1_percent(self):
        result = delta_share_strategy(
            left_map={"A": 100, "B": 50},
            right_map={"A": 80, "B": 60},
            scope_absolute_delta=10.0,
            dimension="region",
        )
        assert result.quality.reconciliation_status == "reconciled"
        assert result.quality.confidence_grade == "high"
        assert result.quality.recommended_use == "trusted"

    def test_ratio_quality_is_approximate(self):
        result = ratio_decomposition_strategy(
            left_map={"A": 30},
            right_map={"A": 20},
            scope_absolute_delta=10.0,
            dimension="region",
            numerator_left_map={"A": 30},
            numerator_right_map={"A": 20},
            denominator_left_map={"A": 100},
            denominator_right_map={"A": 100},
            scope_ratio_delta=0.1,
        )
        assert result.quality.reconciliation_status == "approximate"
        assert result.quality.confidence_grade == "medium"
        assert result.quality.recommended_use == "exploratory"

    def test_weighted_quality_is_approximate(self):
        result = weighted_decomposition_strategy(
            left_map={"A": 300},
            right_map={"A": 200},
            scope_absolute_delta=100.0,
            dimension="region",
            numerator_left_map={"A": 300},
            numerator_right_map={"A": 200},
            weight_left_map={"A": 100},
            weight_right_map={"A": 80},
            scope_weighted_delta=0.5,
        )
        assert result.quality.reconciliation_status == "approximate"
        assert result.quality.confidence_grade == "medium"
        assert result.quality.recommended_use == "exploratory"


class TestRatioDecompositionStrategy:
    def test_basic_ratio_decomposition(self):
        result = ratio_decomposition_strategy(
            left_map={"A": 30, "B": 10},
            right_map={"A": 20, "B": 15},
            scope_absolute_delta=5.0,
            dimension="region",
            numerator_left_map={"A": 30, "B": 10},
            numerator_right_map={"A": 20, "B": 15},
            denominator_left_map={"A": 100, "B": 50},
            denominator_right_map={"A": 100, "B": 50},
            scope_ratio_delta=0.05,
        )
        assert result.method == "ratio_decomposition"
        assert len(result.rows) == 2
        # Segment A: ratio 0.20 -> 0.30, delta +0.10
        # numerator_effect = (30-20)/100 = 0.10
        # denominator_effect = -20*(100-100)/(100*100) = 0.0
        a_row = next(r for r in result.rows if r["key"] == "A")
        assert a_row["current_ratio"] == pytest.approx(0.30, abs=1e-6)
        assert a_row["baseline_ratio"] == pytest.approx(0.20, abs=1e-6)
        assert a_row["segment_ratio_delta"] == pytest.approx(0.10, abs=1e-6)
        assert a_row["numerator_contribution"] == pytest.approx(0.10, abs=1e-6)
        assert a_row["denominator_contribution"] == pytest.approx(0.0, abs=1e-6)

    def test_denominator_effect_with_changing_denominator(self):
        result = ratio_decomposition_strategy(
            left_map={"A": 20},
            right_map={"A": 20},
            scope_absolute_delta=0.0,
            dimension="region",
            numerator_left_map={"A": 20},
            numerator_right_map={"A": 20},
            denominator_left_map={"A": 200},
            denominator_right_map={"A": 100},
            scope_ratio_delta=None,
        )
        a_row = result.rows[0]
        # ratio went from 0.20 to 0.10, delta -0.10
        assert a_row["segment_ratio_delta"] == pytest.approx(-0.10, abs=1e-6)
        # numerator_effect = (20-20)/100 = 0 (no numerator change)
        assert a_row["numerator_contribution"] == pytest.approx(0.0, abs=1e-6)
        # denominator_effect = -20*(200-100)/(200*100) = -20*100/20000 = -0.10
        assert a_row["denominator_contribution"] == pytest.approx(-0.10, abs=1e-6)

    def test_empty_rows_raises_not_attributable(self):
        with pytest.raises(ValueError, match="NOT_ATTRIBUTABLE"):
            ratio_decomposition_strategy(
                left_map={},
                right_map={},
                scope_absolute_delta=0.0,
                dimension="region",
                numerator_left_map={},
                numerator_right_map={},
                denominator_left_map={},
                denominator_right_map={},
                scope_ratio_delta=0.0,
            )

    def test_null_denominator_yields_none_ratio(self):
        result = ratio_decomposition_strategy(
            left_map={"A": 30},
            right_map={"A": 20},
            scope_absolute_delta=10.0,
            dimension="region",
            numerator_left_map={"A": 30},
            numerator_right_map={"A": 20},
            denominator_left_map={"A": 0},  # zero denominator
            denominator_right_map={"A": 100},
            scope_ratio_delta=None,
        )
        a_row = result.rows[0]
        assert a_row["current_ratio"] is None  # 30/0 is undefined


class TestWeightedDecompositionStrategy:
    def test_basic_weighted_decomposition(self):
        # AOV-like scenario: gmv/order_count by region
        # Region A: gmv 300/100 orders => aov=3.0 (curr), gmv 200/80 => aov=2.5 (base)
        # Region B: gmv 50/50 => aov=1.0 (curr), gmv 90/60 => aov=1.5 (base)
        # W_curr = 150, W_base = 140
        result = weighted_decomposition_strategy(
            left_map={"A": 300, "B": 50},
            right_map={"A": 200, "B": 90},
            scope_absolute_delta=60.0,
            dimension="region",
            numerator_left_map={"A": 300, "B": 50},
            numerator_right_map={"A": 200, "B": 90},
            weight_left_map={"A": 100, "B": 50},
            weight_right_map={"A": 80, "B": 60},
            scope_weighted_delta=None,
        )
        assert result.method == "weighted_decomposition"
        assert len(result.rows) == 2

        a_row = next(r for r in result.rows if r["key"] == "A")
        assert a_row["current_weighted_value"] == pytest.approx(3.0, abs=1e-6)
        assert a_row["baseline_weighted_value"] == pytest.approx(2.5, abs=1e-6)
        # within_effect = (3.0-2.5) * (100/150) = 0.3333
        assert a_row["within_effect"] == pytest.approx(0.3333, abs=1e-3)
        # mix_effect = (100/150 - 80/140) * 2.5 = 0.2381
        assert a_row["mix_effect"] == pytest.approx(0.2381, abs=1e-3)

    def test_weight_shares_computed(self):
        result = weighted_decomposition_strategy(
            left_map={"A": 100},
            right_map={"A": 80},
            scope_absolute_delta=20.0,
            dimension="region",
            numerator_left_map={"A": 100},
            numerator_right_map={"A": 80},
            weight_left_map={"A": 50},
            weight_right_map={"A": 40},
            scope_weighted_delta=None,
        )
        a_row = result.rows[0]
        assert a_row["current_weight_share"] == pytest.approx(1.0, abs=1e-6)
        assert a_row["baseline_weight_share"] == pytest.approx(1.0, abs=1e-6)

    def test_fallback_when_component_maps_missing(self):
        result = weighted_decomposition_strategy(
            left_map={"A": 300},
            right_map={"A": 200},
            scope_absolute_delta=100.0,
            dimension="region",
        )
        assert result.method == "weighted_decomposition"
        assert any(i["code"] == "strategy_fallback" for i in result.issues)

    def test_empty_rows_raises_not_attributable(self):
        with pytest.raises(ValueError, match="NOT_ATTRIBUTABLE"):
            weighted_decomposition_strategy(
                left_map={},
                right_map={},
                scope_absolute_delta=0.0,
                dimension="region",
                numerator_left_map={},
                numerator_right_map={},
                weight_left_map={},
                weight_right_map={},
                scope_weighted_delta=0.0,
            )
