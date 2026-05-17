from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from marivo.core.semantic.calendar import (
    CalendarAnnotationRow,
    CalendarBaselineGenerationRule,
    CalendarMatchingStep,
    CalendarPolicyResolutionError,
    _nearest_same_weekday,
    build_calendar_annotation_rows,
    compare_type_to_alignment_plan,
    get_calendar_policy,
    policy_registry_summary,
    resolve_calendar_baseline_window,
    resolve_calendar_bucket_pairing,
)


def _annotation(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
) -> dict[str, object]:
    return {
        "calendar_date": day,
        "day_kind": "holiday" if holiday_group_id else "adjusted_workday",
        "holiday_group_id": holiday_group_id,
        "year_relative_holiday_key": year_relative_holiday_key,
    }


class CalendarBaselineTests(unittest.TestCase):
    def test_previous_year_shifts_window_by_one_year(self) -> None:
        window = resolve_calendar_baseline_window(
            current_window=(date(2026, 4, 1), date(2026, 5, 1)),
            rule=CalendarBaselineGenerationRule(
                strategy="previous_year",
                offset_value=1,
                offset_unit="year",
            ),
        )

        self.assertEqual(window, (date(2025, 4, 1), date(2025, 5, 1)))

    def test_previous_year_clamps_leap_day_to_february_end(self) -> None:
        window = resolve_calendar_baseline_window(
            current_window=(date(2024, 2, 29), date(2024, 3, 1)),
            rule=CalendarBaselineGenerationRule(
                strategy="previous_year",
                offset_value=1,
                offset_unit="year",
            ),
        )

        self.assertEqual(window, (date(2023, 2, 28), date(2023, 3, 1)))

    def test_previous_period_uses_adjacent_equal_length_window(self) -> None:
        window = resolve_calendar_baseline_window(
            current_window=(date(2026, 4, 10), date(2026, 4, 17)),
            rule=CalendarBaselineGenerationRule(strategy="previous_period"),
        )

        self.assertEqual(window, (date(2026, 4, 3), date(2026, 4, 10)))

    def test_previous_period_week_offset_uses_fixed_week_shift(self) -> None:
        window = resolve_calendar_baseline_window(
            current_window=(date(2026, 4, 6), date(2026, 4, 13)),
            rule=CalendarBaselineGenerationRule(
                strategy="previous_period",
                offset_value=1,
                offset_unit="week",
            ),
        )

        self.assertEqual(window, (date(2026, 3, 30), date(2026, 4, 6)))

    def test_unsupported_strategy_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported calendar baseline strategy"):
            resolve_calendar_baseline_window(
                current_window=(date(2026, 4, 1), date(2026, 5, 1)),
                rule=CalendarBaselineGenerationRule(strategy=cast("Any", "fixed_window")),
            )


class CalendarPolicyTests(unittest.TestCase):
    def test_registry_summary_groups_published_policies_by_basis(self) -> None:
        summary = policy_registry_summary()
        yoy_summary = policy_registry_summary(comparison_basis="yoy")
        mom_summary = policy_registry_summary(comparison_basis="mom")
        wow_summary = policy_registry_summary(comparison_basis="wow")

        self.assertEqual(len(summary), 6)
        self.assertEqual(
            [item["policy_ref"] for item in yoy_summary],
            [
                "calendar_policy.natural_yoy",
                "calendar_policy.weekday_yoy",
                "calendar_policy.calendar_yoy",
            ],
        )
        self.assertEqual(
            [item["policy_ref"] for item in mom_summary],
            [
                "calendar_policy.natural_mom",
                "calendar_policy.weekday_mom",
            ],
        )
        self.assertEqual(
            [item["policy_ref"] for item in wow_summary],
            ["calendar_policy.weekday_wow"],
        )

    def test_calendar_yoy_policy_uses_holiday_then_weekday_fallback(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        self.assertEqual(policy.comparison_basis, "yoy")
        self.assertEqual(policy.resolved_alignment_mode, "calendar_aware")
        self.assertEqual(
            [step.matcher for step in policy.matching_strategy],
            [
                "holiday_cluster",
                "year_relative_holiday_key",
                "same_weekday_nearest",
                "natural_date_shift",
            ],
        )
        self.assertEqual(policy.fallback_strategy, ("same_weekday_nearest", "natural_date_shift"))
        self.assertEqual(policy.matching_strategy[2].tie_breaker, "prefer_backward")
        self.assertEqual(policy.matching_strategy[2].max_shift_days, 3)
        self.assertEqual(
            policy.coverage_behavior, "warn_when_calendar_annotation_missing_or_fallback_used"
        )

    def test_compare_type_to_alignment_plan_maps_supported_types(self) -> None:
        self.assertIsNone(compare_type_to_alignment_plan("normal"))
        self.assertTrue(compare_type_to_alignment_plan("holiday_aligned").requires_calendar_data)
        self.assertFalse(compare_type_to_alignment_plan("weekday_aligned").requires_calendar_data)
        self.assertEqual(
            compare_type_to_alignment_plan("holiday_and_weekday_aligned").resolved_alignment_mode,
            "holiday_and_weekday_aligned",
        )

    def test_compare_type_to_alignment_plan_rejects_unknown_ref(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            compare_type_to_alignment_plan("not_real")

        self.assertEqual(ctx.exception.code, "compare_type_unknown")


class CalendarPairingTests(unittest.TestCase):
    def test_nearest_same_weekday_picks_candidate_within_max_shift(self) -> None:
        candidate = _nearest_same_weekday(
            target_day=date(2025, 4, 9),
            baseline_window=(date(2025, 4, 2), date(2025, 4, 11)),
            weekday=3,
            tie_breaker="prefer_backward",
            max_shift_days=7,
        )

        self.assertEqual(candidate, date(2025, 4, 9))

    def test_nearest_same_weekday_accepts_candidate_at_max_shift_boundary(self) -> None:
        candidate = _nearest_same_weekday(
            target_day=date(2025, 4, 9),
            baseline_window=(date(2025, 4, 2), date(2025, 4, 3)),
            weekday=3,
            tie_breaker="prefer_backward",
            max_shift_days=7,
        )

        self.assertEqual(candidate, date(2025, 4, 2))

    def test_natural_policy_uses_natural_date_shift(self) -> None:
        current_window = (date(2026, 4, 10), date(2026, 4, 13))
        baseline_window = (date(2025, 4, 10), date(2025, 4, 13))
        policy = get_calendar_policy("calendar_policy.natural_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=None,
            ),
        )

        self.assertEqual(
            [bucket["pairing_reason"] for bucket in resolution.bucket_pairing],
            ["natural_date_shift", "natural_date_shift", "natural_date_shift"],
        )
        self.assertTrue(
            all(bucket["strictness_level"] == "strict" for bucket in resolution.bucket_pairing)
        )
        self.assertTrue(resolution.rollup_safe)
        self.assertEqual(resolution.comparability_warnings, [])

    def test_holiday_policy_matches_unique_cluster_before_fallback(self) -> None:
        current_window = (date(2026, 4, 4), date(2026, 4, 5))
        baseline_window = (date(2025, 4, 4), date(2025, 4, 5))
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=[
                    _annotation(
                        "2026-04-04",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d+0",
                    ),
                    _annotation(
                        "2025-04-04",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d+0",
                    ),
                ],
            ),
        )

        self.assertEqual(resolution.bucket_pairing[0]["pairing_reason"], "holiday_cluster")
        self.assertEqual(resolution.bucket_pairing[0]["baseline_bucket_start"], "2025-04-04")
        self.assertEqual(resolution.bucket_pairing[0]["issues"], [])
        self.assertEqual(resolution.bucket_pairing[0]["strictness_level"], "strict")
        self.assertTrue(resolution.rollup_safe)

    def test_holiday_policy_uses_relative_key_when_cluster_is_not_unique(self) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 4))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 4))
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=[
                    _annotation(
                        "2026-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _annotation(
                        "2026-04-02",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-2",
                    ),
                    _annotation(
                        "2026-04-03",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-1",
                    ),
                    _annotation(
                        "2025-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _annotation(
                        "2025-04-02",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-2",
                    ),
                    _annotation(
                        "2025-04-03",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-1",
                    ),
                ],
            ),
        )

        self.assertEqual(
            [bucket["pairing_reason"] for bucket in resolution.bucket_pairing],
            [
                "year_relative_holiday_key",
                "year_relative_holiday_key",
                "year_relative_holiday_key",
            ],
        )
        self.assertEqual(resolution.comparability_warnings, [])

    def test_holiday_policy_records_unmapped_and_fallback_when_cluster_is_missing(
        self,
    ) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 2))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 2))
        policy = get_calendar_policy("calendar_policy.calendar_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=[
                    _annotation(
                        "2026-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _annotation("2025-04-01"),
                ],
            ),
        )

        self.assertEqual(resolution.bucket_pairing[0]["pairing_reason"], "natural_date_shift")
        self.assertEqual(
            resolution.bucket_pairing[0]["issues"],
            ["holiday_cluster_unmapped", "fallback_applied"],
        )
        self.assertEqual(resolution.bucket_pairing[0]["strictness_level"], "fallback")
        self.assertFalse(resolution.rollup_safe)
        self.assertEqual(
            resolution.comparability_warnings,
            ["holiday_cluster_unmapped", "fallback_applied"],
        )

    def test_weekday_policy_falls_back_to_natural_shift_when_same_weekday_exceeds_max_shift(
        self,
    ) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 2))
        baseline_window = (date(2025, 4, 5), date(2025, 4, 6))
        policy = get_calendar_policy("calendar_policy.weekday_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=None,
            ),
        )

        self.assertEqual(resolution.bucket_pairing[0]["pairing_reason"], "natural_date_shift")
        self.assertEqual(resolution.bucket_pairing[0]["baseline_bucket_start"], "2025-04-05")
        self.assertEqual(resolution.bucket_pairing[0]["issues"], [])
        self.assertEqual(resolution.bucket_pairing[0]["strictness_level"], "strict")

    def test_weekday_wow_leaves_bucket_unpaired_when_max_shift_blocks_match(self) -> None:
        current_window = (date(2026, 4, 7), date(2026, 4, 8))
        baseline_window = (date(2026, 4, 3), date(2026, 4, 10))
        policy = get_calendar_policy("calendar_policy.weekday_wow")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=None,
            ),
        )

        self.assertIsNone(resolution.bucket_pairing[0]["baseline_bucket_start"])
        self.assertEqual(
            resolution.bucket_pairing[0]["issues"],
            ["alignment_coverage_insufficient"],
        )
        self.assertEqual(resolution.bucket_pairing[0]["strictness_level"], "coverage_incomplete")
        self.assertFalse(resolution.rollup_safe)

    def test_unpaired_bucket_records_coverage_issue(self) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 4))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 3))
        policy = get_calendar_policy("calendar_policy.natural_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=None,
            ),
        )

        self.assertIsNone(resolution.bucket_pairing[-1]["baseline_bucket_start"])
        self.assertEqual(
            resolution.bucket_pairing[-1]["issues"],
            ["alignment_coverage_insufficient"],
        )
        self.assertEqual(resolution.bucket_pairing[-1]["strictness_level"], "coverage_incomplete")
        self.assertFalse(resolution.rollup_safe)

    def test_reused_baseline_bucket_marks_rollup_unsafe(self) -> None:
        current_window = (date(2026, 1, 1), date(2026, 1, 3))
        baseline_window = (date(2025, 1, 1), date(2025, 1, 3))
        policy = get_calendar_policy("calendar_policy.weekday_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=None,
            ),
        )

        self.assertEqual(
            [bucket["baseline_bucket_start"] for bucket in resolution.bucket_pairing],
            ["2025-01-02", "2025-01-02"],
        )
        self.assertTrue(
            all(bucket["is_reused_baseline_bucket"] for bucket in resolution.bucket_pairing)
        )
        self.assertTrue(
            all(
                bucket["strictness_level"] == "reused_baseline"
                for bucket in resolution.bucket_pairing
            )
        )
        self.assertFalse(resolution.rollup_safe)


class CalendarAnnotationRowsTests(unittest.TestCase):
    def test_sparse_rows_derive_weekday_and_fill_missing_dates(self) -> None:
        rows = build_calendar_annotation_rows(
            current_window=(date(2026, 6, 5), date(2026, 6, 6)),
            baseline_window=(date(2025, 6, 6), date(2025, 6, 7)),
            raw_rows=[
                {
                    "calendar_date": "2026-06-05",
                    "day_kind": "holiday",
                    "holiday_group_id": "dragon_boat",
                    "year_relative_holiday_key": "dragon_boat_d+0",
                }
            ],
        )

        rows_by_date = {row.calendar_date: row for row in rows}
        self.assertEqual(rows_by_date[date(2026, 6, 5)].weekday, 5)
        self.assertEqual(rows_by_date[date(2025, 6, 6)].weekday, 5)
        self.assertIsNone(rows_by_date[date(2025, 6, 6)].holiday_group_id)

    def test_multi_holiday_date_merges_primary_and_extra_annotations(self) -> None:
        rows = build_calendar_annotation_rows(
            current_window=(date(2024, 10, 1), date(2024, 10, 8)),
            baseline_window=(date(2023, 9, 30), date(2023, 10, 7)),
            raw_rows=[
                {
                    "calendar_date": "2024-10-01",
                    "day_kind": "holiday",
                    "holiday_name": "国庆节",
                    "holiday_group_id": "national_day",
                    "year_relative_holiday_key": "national_day_2024",
                },
                {
                    "calendar_date": "2024-10-01",
                    "day_kind": "holiday",
                    "holiday_name": "中秋节",
                    "holiday_group_id": "mid_autumn",
                    "year_relative_holiday_key": "mid_autumn_2024",
                },
            ],
        )

        oct1_row = next(row for row in rows if row.calendar_date == date(2024, 10, 1))
        self.assertEqual(oct1_row.holiday_group_id, "national_day")
        self.assertIn("mid_autumn", oct1_row.extra_holiday_group_ids)
        self.assertIn("mid_autumn_2024", oct1_row.extra_year_relative_holiday_keys)

    def test_holiday_cluster_matches_current_extra_holiday_group(self) -> None:
        strategy = (
            CalendarMatchingStep(matcher="holiday_cluster", requires_annotation=True),
            CalendarMatchingStep(matcher="natural_date_shift", requires_annotation=False),
        )
        annotation_rows = [
            CalendarAnnotationRow(
                calendar_date=date(2024, 10, 1),
                weekday=2,
                holiday_group_id="some_unknown",
                extra_holiday_group_ids=("mid_autumn",),
                extra_year_relative_holiday_keys=("mid_autumn_2024",),
            ),
            CalendarAnnotationRow(
                calendar_date=date(2023, 9, 30),
                weekday=6,
                holiday_group_id="mid_autumn",
                year_relative_holiday_key="mid_autumn_2023",
            ),
        ]

        resolution = resolve_calendar_bucket_pairing(
            current_window=(date(2024, 10, 1), date(2024, 10, 2)),
            baseline_window=(date(2023, 9, 29), date(2023, 10, 1)),
            matching_strategy=strategy,
            fallback_strategy=("natural_date_shift",),
            annotation_rows=annotation_rows,
        )

        self.assertEqual(resolution.bucket_pairing[0]["pairing_reason"], "holiday_cluster")
        self.assertEqual(resolution.bucket_pairing[0]["baseline_bucket_start"], "2023-09-30")
