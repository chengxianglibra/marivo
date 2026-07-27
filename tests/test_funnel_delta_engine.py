"""Pure funnel delta alignment and target-value tests without a session."""

from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.constraints import ConstraintId
from marivo.analysis.errors import (
    FunnelAttributionUnsupportedError,
    FunnelComparisonMismatchError,
    PatternStepMismatchError,
)
from marivo.analysis.frames.delta import FUNNEL_DELTA_COLUMNS, FunnelDeltaFrameMeta
from marivo.analysis.intents._funnel_delta import build_funnel_delta
from tests.shared_fixtures import pattern_step_for_tests


def _row(step: str, *, lost: int, entries: int) -> dict[str, object]:
    return {
        "step_key": step,
        "cohort_count": entries,
        "resolved_cohort_count": entries,
        "entry_count": entries,
        "resolved_entry_count": entries,
        "reached_count": entries - lost,
        "lost_count": lost,
        "coverage_censored_count": 0,
        "loss_rate_from_previous": lost / entries if step != "cart" else None,
    }


def test_funnel_loss_rate_retains_the_exact_pattern_step() -> None:
    step = pattern_step_for_tests("payment")
    target = mv.funnel_loss_rate(step=step)
    assert target.kind == "funnel_loss_rate"
    assert target.step == step
    assert target.fingerprint == mv.funnel_loss_rate(step=step).fingerprint


@pytest.mark.parametrize("value", [0, "payment", None, ("payment",)])
def test_funnel_loss_rate_rejects_non_pattern_step_selectors(value: object) -> None:
    with pytest.raises(PatternStepMismatchError) as excinfo:
        mv.funnel_loss_rate(step=value)  # type: ignore[arg-type]
    error = excinfo.value
    assert error.kind == "pattern_step_mismatch"
    assert "mv.funnel_loss_rate(step=<PatternStep>)" in error.expected
    assert error.location == "mv.funnel_loss_rate(step)"
    assert error.repair is not None
    assert error.repair.kind == "user_choice"


def test_phase_four_error_kinds_are_published() -> None:
    mismatch = FunnelComparisonMismatchError(
        message="compared funnels differ",
        expected="two compatible EventFrame[funnel] artifacts",
        received="pattern fingerprints differ",
        location="session.compare(current, baseline)",
    )
    unsupported = FunnelAttributionUnsupportedError(
        message="delta is already grouped",
        expected="an ungrouped DeltaFrame[funnel]",
        received="axes=('acquisition_channel',)",
        location="session.attribute(frame)",
    )
    assert mismatch.kind == "funnel_comparison_mismatch"
    assert unsupported.kind == "funnel_attribution_unsupported"


def test_phase_four_constraint_ids_are_published() -> None:
    assert ConstraintId.FUNNEL_COMPARISON_COMPATIBLE == "funnel_comparison_compatible"
    assert ConstraintId.FUNNEL_ATTRIBUTION_TARGET_VALID == "funnel_attribution_target_valid"
    assert ConstraintId.FUNNEL_ATTRIBUTION_RECONCILIATION == "funnel_attribution_reconciliation"


def test_funnel_delta_columns_are_the_published_contract() -> None:
    assert FUNNEL_DELTA_COLUMNS[-3:] == (
        "current_loss_rate_from_previous",
        "baseline_loss_rate_from_previous",
        "loss_rate_from_previous_delta",
    )
    assert "alignment" not in FunnelDeltaFrameMeta.model_fields


def test_ungrouped_alignment_pairs_rows_by_step_key() -> None:
    delta = build_funnel_delta(
        current=pd.DataFrame(
            [_row("cart", lost=0, entries=100), _row("payment", lost=40, entries=100)]
        ),
        baseline=pd.DataFrame(
            [_row("cart", lost=0, entries=80), _row("payment", lost=24, entries=80)]
        ),
        axis_columns=(),
        step_order=("cart", "payment"),
    )
    payment = delta.rows.loc[delta.rows["step_key"] == "payment"].iloc[0]
    assert payment["loss_rate_from_previous_delta"] == pytest.approx(0.1)
    assert delta.zero_filled_tuple_count == 0


def test_grouped_alignment_zero_fills_missing_counts_but_not_rates() -> None:
    current = pd.DataFrame([{**_row("payment", lost=20, entries=50), "channel": "paid"}])
    baseline = pd.DataFrame([{**_row("payment", lost=12, entries=40), "channel": "organic"}])
    delta = build_funnel_delta(
        current=current,
        baseline=baseline,
        axis_columns=("channel",),
        step_order=("payment",),
    )
    organic = delta.rows.loc[delta.rows["channel"] == "organic"].iloc[0]
    assert organic["current_lost_count"] == 0
    assert pd.isna(organic["current_loss_rate_from_previous"])
    assert pd.isna(organic["loss_rate_from_previous_delta"])
    assert delta.zero_filled_tuple_count == 2


def test_zero_filled_tuple_count_does_not_repeat_one_tuple_for_each_step() -> None:
    current = pd.DataFrame(
        [
            {**_row("cart", lost=0, entries=50), "channel": "paid"},
            {**_row("payment", lost=20, entries=50), "channel": "paid"},
        ]
    )
    baseline = pd.DataFrame(
        [
            {**_row("cart", lost=0, entries=40), "channel": "organic"},
            {**_row("payment", lost=12, entries=40), "channel": "organic"},
        ]
    )

    delta = build_funnel_delta(
        current=current,
        baseline=baseline,
        axis_columns=("channel",),
        step_order=("cart", "payment"),
    )

    assert delta.zero_filled_tuple_count == 2
    assert len(delta.rows) == 4
