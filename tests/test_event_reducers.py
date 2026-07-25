"""Pure Event reducer behavior and invariants."""

from __future__ import annotations

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.intents._event_funnel import (
    FUNNEL_ADDITIVE_COLUMNS,
    reduce_event_funnel,
)
from marivo.analysis.intents._event_time_to_event import reduce_event_time_to_event


def _pattern() -> tuple[mv.EventPattern, mv.PatternStep, mv.PatternStep, mv.PatternStep]:
    cart = mv.step(
        participant=ms.participant_role(
            event=ms.ref.event("commerce.cart_created"),
            name="user",
        ),
        key="cart",
    )
    checkout = mv.step(
        participant=ms.participant_role(
            event=ms.ref.event("commerce.checkout_started"),
            name="user",
        ),
        key="checkout",
    )
    payment = mv.step(
        participant=ms.participant_role(
            event=ms.ref.event("commerce.payment_succeeded"),
            name="buyer",
        ),
        key="payment",
    )
    return mv.sequence(cart, checkout, payment), cart, checkout, payment


def _journey_rows() -> pd.DataFrame:
    assignments = (
        ("j1", ("u1",), "complete", ("c1", "x1", "p1")),
        ("j2", ("u2",), "incomplete", ("c2", "x2", None)),
        ("j3", ("u3",), "coverage_censored", ("c3", "x3", None)),
        ("j4", ("u4",), "coverage_censored", ("c4", None, None)),
    )
    timestamps = {
        "c1": "2026-07-01T00:00:00Z",
        "x1": "2026-07-01T00:05:00Z",
        "p1": "2026-07-01T00:12:00Z",
        "c2": "2026-07-01T01:00:00Z",
        "x2": "2026-07-01T01:10:00Z",
        "c3": "2026-07-01T02:00:00Z",
        "x3": "2026-07-01T02:20:00Z",
        "c4": "2026-07-01T03:00:00Z",
    }
    rows: list[dict[str, object]] = []
    for journey_id, subject, status, event_ids in assignments:
        first_time: pd.Timestamp | None = None
        previous_time: pd.Timestamp | None = None
        for step_key, event_id in zip(("cart", "checkout", "payment"), event_ids, strict=True):
            occurred_at = pd.Timestamp(timestamps[event_id]) if event_id is not None else None
            if first_time is None and occurred_at is not None:
                first_time = occurred_at
            rows.append(
                {
                    "journey_id": journey_id,
                    "completion_status": status,
                    "subject_identity": subject,
                    "step_key": step_key,
                    "event_identity": (event_id,) if event_id is not None else None,
                    "occurred_at": occurred_at,
                    "elapsed_from_start": (
                        occurred_at - first_time
                        if occurred_at is not None and first_time is not None
                        else None
                    ),
                    "elapsed_from_previous": (
                        occurred_at - previous_time
                        if occurred_at is not None and previous_time is not None
                        else None
                    ),
                }
            )
            if occurred_at is not None:
                previous_time = occurred_at
    return pd.DataFrame.from_records(rows)


def _event_coverage_complete() -> dict[str, bool]:
    """Coverage is authoritative per Event, not per aggregate journey status."""
    return {
        "commerce.cart_created": True,
        "commerce.checkout_started": False,
        "commerce.payment_succeeded": True,
    }


def test_funnel_reduction_separates_loss_from_unknown_coverage() -> None:
    pattern, _cart, _checkout, _payment = _pattern()

    reduction = reduce_event_funnel(
        _journey_rows(),
        pattern=pattern,
        event_coverage_complete=_event_coverage_complete(),
    )

    rows = reduction.rows.set_index("step_key")
    assert rows.loc["cart", list(FUNNEL_ADDITIVE_COLUMNS)].to_dict() == {
        "cohort_count": 4,
        "resolved_cohort_count": 4,
        "entry_count": 4,
        "resolved_entry_count": 4,
        "reached_count": 4,
        "lost_count": 0,
        "coverage_censored_count": 0,
    }
    assert pd.isna(rows.loc["cart", "conversion_from_previous"])
    assert pd.isna(rows.loc["cart", "loss_rate_from_previous"])
    assert rows.loc["checkout", list(FUNNEL_ADDITIVE_COLUMNS)].to_dict() == {
        "cohort_count": 4,
        "resolved_cohort_count": 3,
        "entry_count": 4,
        "resolved_entry_count": 3,
        "reached_count": 3,
        "lost_count": 0,
        "coverage_censored_count": 1,
    }
    assert rows.loc["checkout", "conversion_from_previous"] == 1.0
    assert rows.loc["payment", list(FUNNEL_ADDITIVE_COLUMNS)].to_dict() == {
        "cohort_count": 4,
        "resolved_cohort_count": 4,
        "entry_count": 3,
        "resolved_entry_count": 3,
        "reached_count": 1,
        "lost_count": 2,
        "coverage_censored_count": 0,
    }
    assert rows.loc["payment", "conversion_from_first"] == 0.25
    assert rows.loc["payment", "conversion_from_previous"] == pytest.approx(1 / 3)
    assert rows.loc["payment", "loss_rate_from_previous"] == pytest.approx(2 / 3)
    assert reduction.grouped_hash == reduction.ungrouped_hash


def test_grouped_funnel_preserves_null_group_and_reconciles() -> None:
    pattern, _cart, _checkout, _payment = _pattern()
    axes = pd.DataFrame(
        {
            "subject_identity": [("u1",), ("u2",), ("u3",), ("u4",)],
            "channel": ["paid", "paid", "organic", None],
        }
    )

    reduction = reduce_event_funnel(
        _journey_rows(),
        pattern=pattern,
        event_coverage_complete=_event_coverage_complete(),
        axis_values=axes,
        axis_columns=("channel",),
    )

    assert reduction.grouped_hash == reduction.ungrouped_hash
    assert reduction.rows["channel"].isna().any()
    null_rows = reduction.rows.loc[reduction.rows["channel"].isna()].set_index("step_key")
    assert null_rows.loc["checkout", "resolved_entry_count"] == 0
    assert pd.isna(null_rows.loc["checkout", "conversion_from_previous"])
    totals = reduction.rows.groupby("step_key")[list(FUNNEL_ADDITIVE_COLUMNS)].sum()
    expected = reduction.ungrouped_rows.set_index("step_key")[list(FUNNEL_ADDITIVE_COLUMNS)]
    pd.testing.assert_frame_equal(
        totals.sort_index(),
        expected.sort_index(),
        check_dtype=False,
    )


def test_funnel_rejects_non_dense_and_duplicate_subject_journeys() -> None:
    pattern, _cart, _checkout, _payment = _pattern()
    rows = _journey_rows()
    with pytest.raises(ValueError, match="exactly one row per PatternStep"):
        reduce_event_funnel(
            rows.iloc[:-1].copy(),
            pattern=pattern,
            event_coverage_complete=_event_coverage_complete(),
        )

    duplicate_subject = rows.copy()
    duplicate_subject.loc[duplicate_subject["journey_id"] == "j4", "subject_identity"] = pd.Series(
        [("u1",)] * 3,
        index=duplicate_subject.index[duplicate_subject["journey_id"] == "j4"],
    )
    with pytest.raises(ValueError, match="at most one journey per subject"):
        reduce_event_funnel(
            duplicate_subject,
            pattern=pattern,
            event_coverage_complete=_event_coverage_complete(),
        )


def test_time_to_event_projects_persisted_assignments_and_statuses() -> None:
    pattern, _cart, checkout, payment = _pattern()

    result = reduce_event_time_to_event(
        _journey_rows(),
        pattern=pattern,
        start_step=checkout,
        end_step=payment,
    ).set_index("journey_id")

    assert set(result.index) == {"j1", "j2", "j3"}
    assert result.loc["j1", "duration"] == pd.Timedelta(minutes=7)
    assert result.loc["j1", "completion_status"] == "complete"
    assert result.loc["j2", "completion_status"] == "incomplete"
    assert result.loc["j3", "completion_status"] == "coverage_censored"
    assert pd.isna(result.loc["j2", "end_time"])
    assert pd.isna(result.loc["j2", "end_event_identity"])
    assert pd.isna(result.loc["j2", "duration"])


def test_time_to_event_retains_multiple_attempts_for_one_subject() -> None:
    pattern, _cart, checkout, payment = _pattern()
    rows = _journey_rows()
    repeated_attempt = rows.loc[rows["journey_id"] == "j1"].copy()
    repeated_attempt["journey_id"] = "j5"
    repeated_attempt["event_identity"] = repeated_attempt["event_identity"].map(
        lambda value: (f"{value[0]}_attempt_2",) if value is not None else None
    )
    rows = pd.concat((rows, repeated_attempt), ignore_index=True)

    result = reduce_event_time_to_event(
        rows,
        pattern=pattern,
        start_step=checkout,
        end_step=payment,
    )

    assert set(result["journey_id"]) == {"j1", "j2", "j3", "j5"}
    assert result.loc[result["journey_id"] == "j5", "subject_identity"].iloc[0] == ("u1",)


def test_time_to_event_requires_exact_ordered_pattern_steps() -> None:
    pattern, cart, checkout, payment = _pattern()
    with pytest.raises(ValueError, match="must precede"):
        reduce_event_time_to_event(
            _journey_rows(),
            pattern=pattern,
            start_step=payment,
            end_step=checkout,
        )
    same_key_different_definition = mv.step(
        participant=ms.participant_role(
            event=ms.ref.event("commerce.another_checkout"),
            name="user",
        ),
        key="checkout",
    )
    with pytest.raises(ValueError, match="exactly once"):
        reduce_event_time_to_event(
            _journey_rows(),
            pattern=pattern,
            start_step=same_key_different_definition,
            end_step=payment,
        )
    with pytest.raises(ValueError, match="must precede"):
        reduce_event_time_to_event(
            _journey_rows(),
            pattern=pattern,
            start_step=cart,
            end_step=cart,
        )
