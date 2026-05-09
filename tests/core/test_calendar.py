"""Tests for app.core.semantic.calendar pure functions."""

from __future__ import annotations

from datetime import date

import pytest

from marivo.core.semantic.calendar import (
    CalendarBaselineGenerationRule,
    CalendarMatchingStep,
    CalendarPolicyResolutionError,
    build_calendar_annotation_rows,
    get_calendar_policy,
    is_rollup_safe,
    list_calendar_policies,
    resolve_calendar_baseline_window,
    resolve_calendar_bucket_pairing,
    resolve_calendar_policy,
    shift_calendar_date,
    strictness_level_for_bucket,
    validate_calendar_policy_ref,
)

# ---------------------------------------------------------------------------
# shift_calendar_date
# ---------------------------------------------------------------------------


def test_shift_calendar_date_year() -> None:
    result = shift_calendar_date(date(2024, 6, 15), years=-1)
    assert result == date(2023, 6, 15)


def test_shift_calendar_date_month_leap() -> None:
    result = shift_calendar_date(date(2024, 1, 31), months=1)
    assert result == date(2024, 2, 29)  # 2024 is a leap year


def test_shift_calendar_date_month_non_leap() -> None:
    result = shift_calendar_date(date(2023, 1, 31), months=1)
    assert result == date(2023, 2, 28)


def test_shift_calendar_date_zero() -> None:
    d = date(2024, 6, 15)
    assert shift_calendar_date(d) == d


# ---------------------------------------------------------------------------
# resolve_calendar_baseline_window
# ---------------------------------------------------------------------------


def test_resolve_calendar_baseline_window_previous_year() -> None:
    rule = CalendarBaselineGenerationRule(
        strategy="previous_year", offset_value=1, offset_unit="year"
    )
    result = resolve_calendar_baseline_window(
        current_window=(date(2024, 1, 1), date(2024, 2, 1)),
        rule=rule,
    )
    assert result == (date(2023, 1, 1), date(2023, 2, 1))


def test_resolve_calendar_baseline_window_previous_period() -> None:
    rule = CalendarBaselineGenerationRule(strategy="previous_period")
    result = resolve_calendar_baseline_window(
        current_window=(date(2024, 2, 1), date(2024, 3, 1)),
        rule=rule,
    )
    assert result == (date(2024, 1, 3), date(2024, 2, 1))


# ---------------------------------------------------------------------------
# build_calendar_annotation_rows
# ---------------------------------------------------------------------------


def test_build_calendar_annotation_rows_empty_raw() -> None:
    result = build_calendar_annotation_rows(
        current_window=(date(2024, 1, 1), date(2024, 1, 3)),
        baseline_window=(date(2023, 1, 1), date(2023, 1, 3)),
        raw_rows=None,
    )
    assert len(result) == 4  # 2 + 2 days


def test_build_calendar_annotation_rows_with_raw() -> None:
    raw = [{"calendar_date": date(2024, 1, 1), "weekday": 1, "holiday_group_id": "new_year"}]
    result = build_calendar_annotation_rows(
        current_window=(date(2024, 1, 1), date(2024, 1, 3)),
        baseline_window=(date(2023, 1, 1), date(2023, 1, 3)),
        raw_rows=raw,
    )
    jan1_rows = [r for r in result if r.calendar_date == date(2024, 1, 1)]
    assert len(jan1_rows) == 1
    assert jan1_rows[0].holiday_group_id == "new_year"


# ---------------------------------------------------------------------------
# resolve_calendar_bucket_pairing
# ---------------------------------------------------------------------------


def test_resolve_calendar_bucket_pairing_natural_date_shift() -> None:
    strategy = (CalendarMatchingStep("natural_date_shift", requires_annotation=False),)
    result = resolve_calendar_bucket_pairing(
        current_window=(date(2024, 1, 1), date(2024, 1, 4)),
        baseline_window=(date(2023, 1, 1), date(2023, 1, 4)),
        matching_strategy=strategy,
        fallback_strategy=(),
        annotation_rows=[],
    )
    assert len(result.bucket_pairing) == 3
    assert result.rollup_safe is True
    assert result.bucket_pairing[0]["baseline_bucket_start"] == "2023-01-01"


def test_resolve_calendar_bucket_pairing_with_fallback() -> None:
    strategy = (
        CalendarMatchingStep("holiday_cluster", requires_annotation=True),
        CalendarMatchingStep("natural_date_shift", requires_annotation=False),
    )
    result = resolve_calendar_bucket_pairing(
        current_window=(date(2024, 1, 1), date(2024, 1, 4)),
        baseline_window=(date(2023, 1, 1), date(2023, 1, 4)),
        matching_strategy=strategy,
        fallback_strategy=("natural_date_shift",),
        annotation_rows=[],
    )
    for pairing in result.bucket_pairing:
        assert pairing["baseline_bucket_start"] is not None


# ---------------------------------------------------------------------------
# strictness_level_for_bucket
# ---------------------------------------------------------------------------


def test_strictness_level_strict() -> None:
    assert strictness_level_for_bucket(issues=[], is_reused_baseline_bucket=False) == "strict"


def test_strictness_level_coverage_incomplete() -> None:
    assert (
        strictness_level_for_bucket(
            issues=["alignment_coverage_insufficient"], is_reused_baseline_bucket=False
        )
        == "coverage_incomplete"
    )


def test_strictness_level_reused_baseline() -> None:
    assert (
        strictness_level_for_bucket(issues=[], is_reused_baseline_bucket=True) == "reused_baseline"
    )


def test_strictness_level_fallback() -> None:
    assert (
        strictness_level_for_bucket(issues=["fallback_applied"], is_reused_baseline_bucket=False)
        == "fallback"
    )


# ---------------------------------------------------------------------------
# is_rollup_safe
# ---------------------------------------------------------------------------


def test_is_rollup_safe_all_strict() -> None:
    pairings = [{"strictness_level": "strict"}, {"strictness_level": "strict"}]
    assert is_rollup_safe(pairings) is True


def test_is_rollup_safe_non_strict() -> None:
    pairings = [{"strictness_level": "strict"}, {"strictness_level": "fallback"}]
    assert is_rollup_safe(pairings) is False


# ---------------------------------------------------------------------------
# get_calendar_policy
# ---------------------------------------------------------------------------


def test_get_calendar_policy_valid() -> None:
    policy = get_calendar_policy("calendar_policy.natural_yoy")
    assert policy.comparison_basis == "yoy"


def test_get_calendar_policy_invalid_raises() -> None:
    with pytest.raises(CalendarPolicyResolutionError):
        get_calendar_policy("calendar_policy.invalid")


# ---------------------------------------------------------------------------
# list_calendar_policies
# ---------------------------------------------------------------------------


def test_list_calendar_policies_count() -> None:
    policies = list_calendar_policies()
    assert len(policies) == 7


# ---------------------------------------------------------------------------
# validate_calendar_policy_ref
# ---------------------------------------------------------------------------


def test_validate_calendar_policy_ref_none() -> None:
    assert validate_calendar_policy_ref(None) is None


def test_validate_calendar_policy_ref_valid() -> None:
    assert (
        validate_calendar_policy_ref("calendar_policy.natural_yoy") == "calendar_policy.natural_yoy"
    )


def test_validate_calendar_policy_ref_basis_mismatch() -> None:
    with pytest.raises(CalendarPolicyResolutionError, match="not valid for comparison_basis"):
        validate_calendar_policy_ref("calendar_policy.natural_yoy", comparison_basis="wow")


# ---------------------------------------------------------------------------
# resolve_calendar_policy
# ---------------------------------------------------------------------------


def test_resolve_calendar_policy_explicit() -> None:
    result = resolve_calendar_policy(explicit_policy_ref="calendar_policy.natural_yoy")
    assert result is not None
    assert result.resolution_source == "explicit_request"


def test_resolve_calendar_policy_none_when_no_sources() -> None:
    result = resolve_calendar_policy()
    assert result is None


def test_resolve_calendar_policy_required_raises() -> None:
    with pytest.raises(CalendarPolicyResolutionError, match="required but none could be resolved"):
        resolve_calendar_policy(required=True)
