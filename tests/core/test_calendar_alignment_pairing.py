from __future__ import annotations

import unittest
from datetime import date

from marivo.core.semantic.calendar import (
    CalendarAnnotationRow,
    CalendarMatchingStep,
    _nearest_same_weekday,
    build_calendar_annotation_rows,
    get_calendar_policy,
    resolve_calendar_bucket_pairing,
)


def _annotation(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
) -> dict[str, object]:
    day_value = date.fromisoformat(day)
    return {
        "calendar_date": day,
        "weekday": day_value.weekday() + 1,
        "holiday_group_id": holiday_group_id,
        "year_relative_holiday_key": year_relative_holiday_key,
    }


class CalendarAlignmentPairingTests(unittest.TestCase):
    def test_nearest_same_weekday_picks_closest_candidate_within_max_shift(self) -> None:
        target_day = date(2025, 4, 9)

        candidate = _nearest_same_weekday(
            target_day=target_day,
            baseline_window=(date(2025, 4, 2), date(2025, 4, 11)),
            weekday=3,
            tie_breaker="prefer_backward",
            max_shift_days=7,
        )

        self.assertEqual(candidate, date(2025, 4, 9))

    def test_nearest_same_weekday_accepts_candidate_at_max_shift_boundary(self) -> None:
        target_day = date(2025, 4, 9)

        candidate = _nearest_same_weekday(
            target_day=target_day,
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
        self.assertTrue(
            all(not bucket["is_reused_baseline_bucket"] for bucket in resolution.bucket_pairing)
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
        self.assertFalse(resolution.bucket_pairing[0]["is_reused_baseline_bucket"])
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

    def test_holiday_policy_records_unmapped_and_fallback_when_cluster_is_missing_in_baseline(
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

    def test_holiday_policy_marks_fallback_only_for_buckets_that_downgrade(self) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 3))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 3))
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
                    ),
                    _annotation(
                        "2026-04-02",
                        holiday_group_id="labour_day",
                        year_relative_holiday_key="labour_day_d-2",
                    ),
                    _annotation(
                        "2025-04-01",
                        holiday_group_id="other_holiday",
                        year_relative_holiday_key="other_holiday_d-3",
                    ),
                    _annotation(
                        "2025-04-02",
                        holiday_group_id="labour_day",
                        year_relative_holiday_key="labour_day_d-2",
                    ),
                ],
            ),
        )

        self.assertEqual(
            resolution.bucket_pairing[0]["issues"],
            ["holiday_cluster_unmapped", "fallback_applied"],
        )
        self.assertEqual(resolution.bucket_pairing[1]["issues"], [])
        self.assertEqual(resolution.bucket_pairing[0]["strictness_level"], "reused_baseline")
        self.assertEqual(resolution.bucket_pairing[1]["strictness_level"], "reused_baseline")
        self.assertTrue(resolution.bucket_pairing[0]["is_reused_baseline_bucket"])
        self.assertTrue(resolution.bucket_pairing[1]["is_reused_baseline_bucket"])
        self.assertFalse(resolution.rollup_safe)
        self.assertEqual(
            resolution.comparability_warnings,
            ["holiday_cluster_unmapped", "fallback_applied"],
        )

    def test_weekday_policy_falls_back_to_natural_shift_when_same_weekday_match_exceeds_max_shift(
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
        self.assertTrue(resolution.rollup_safe)

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

    def test_weekday_policy_marks_reused_baseline_bucket_and_rollup_unsafe(self) -> None:
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


class BuildAnnotationRowsMultiHolidayTest(unittest.TestCase):
    def test_build_annotation_rows_merges_multi_holiday_date(self):
        """Same date with two holidays should merge into one row with extras."""
        current_window = (date(2024, 10, 1), date(2024, 10, 8))
        baseline_window = (date(2023, 9, 30), date(2023, 10, 7))
        raw_rows = [
            {
                "calendar_date": "2024-10-01",
                "weekday": 2,
                "is_weekend": 0,
                "is_workday": 0,
                "holiday_name": "国庆节",
                "holiday_group_id": "national_day",
                "year_relative_holiday_key": "national_day_2024",
            },
            {
                "calendar_date": "2024-10-01",
                "weekday": 2,
                "is_weekend": 0,
                "is_workday": 0,
                "holiday_name": "中秋节",
                "holiday_group_id": "mid_autumn",
                "year_relative_holiday_key": "mid_autumn_2024",
            },
        ]
        rows = build_calendar_annotation_rows(
            current_window=current_window,
            baseline_window=baseline_window,
            raw_rows=raw_rows,
        )
        oct1_row = next(r for r in rows if r.calendar_date == date(2024, 10, 1))
        self.assertEqual(oct1_row.holiday_group_id, "national_day")
        self.assertIn("mid_autumn", oct1_row.extra_holiday_group_ids)
        self.assertIn("mid_autumn_2024", oct1_row.extra_year_relative_holiday_keys)


class HolidayClusterExtraMatchTest(unittest.TestCase):
    def test_holiday_cluster_matches_via_extra_holiday_group_id(self):
        """holiday_cluster matcher should find matches in extra_holiday_group_ids."""
        current_window = (date(2024, 10, 1), date(2024, 10, 2))
        baseline_window = (date(2023, 9, 29), date(2023, 10, 1))
        strategy = (
            CalendarMatchingStep(matcher="holiday_cluster", requires_annotation=True),
            CalendarMatchingStep(matcher="natural_date_shift", requires_annotation=False),
        )
        annotation_rows = [
            CalendarAnnotationRow(
                calendar_date=date(2024, 10, 1),
                weekday=2,
                holiday_group_id="national_day",
                year_relative_holiday_key="national_day_2024",
                extra_holiday_group_ids=("mid_autumn",),
                extra_year_relative_holiday_keys=("mid_autumn_2024",),
            ),
            CalendarAnnotationRow(
                calendar_date=date(2023, 9, 30),
                weekday=6,
                holiday_group_id="mid_autumn",
                year_relative_holiday_key="mid_autumn_2023",
            ),
            CalendarAnnotationRow(
                calendar_date=date(2023, 10, 1),
                weekday=7,
                holiday_group_id="national_day",
                year_relative_holiday_key="national_day_2023",
            ),
        ]
        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=strategy,
            fallback_strategy=("natural_date_shift",),
            annotation_rows=annotation_rows,
        )
        oct1_pairing = resolution.bucket_pairing[0]
        self.assertEqual(oct1_pairing["pairing_reason"], "holiday_cluster")

    def test_holiday_cluster_fallback_to_extra_when_primary_unmapped(self):
        """When primary holiday_group_id is unmapped, match via extras."""
        current_window = (date(2024, 10, 1), date(2024, 10, 2))
        baseline_window = (date(2023, 9, 29), date(2023, 10, 1))
        strategy = (
            CalendarMatchingStep(matcher="holiday_cluster", requires_annotation=True),
            CalendarMatchingStep(matcher="natural_date_shift", requires_annotation=False),
        )
        annotation_rows = [
            # primary is unmapped "some_unknown", but extra is "mid_autumn" which exists in baseline
            CalendarAnnotationRow(
                calendar_date=date(2024, 10, 1),
                weekday=2,
                holiday_group_id="some_unknown",
                year_relative_holiday_key=None,
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
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=strategy,
            fallback_strategy=("natural_date_shift",),
            annotation_rows=annotation_rows,
        )
        oct1_pairing = resolution.bucket_pairing[0]
        self.assertEqual(oct1_pairing["pairing_reason"], "holiday_cluster")
        self.assertEqual(oct1_pairing["baseline_bucket_start"], "2023-09-30")


class YearRelativeHolidayKeyExtraMatchTest(unittest.TestCase):
    def test_year_relative_holiday_key_matches_via_extra(self):
        """year_relative_holiday_key matcher should find matches in extras."""
        current_window = (date(2024, 10, 1), date(2024, 10, 2))
        baseline_window = (date(2023, 9, 29), date(2023, 10, 1))
        strategy = (
            CalendarMatchingStep(matcher="year_relative_holiday_key", requires_annotation=True),
            CalendarMatchingStep(matcher="natural_date_shift", requires_annotation=False),
        )
        annotation_rows = [
            CalendarAnnotationRow(
                calendar_date=date(2024, 10, 1),
                weekday=2,
                holiday_group_id="national_day",
                year_relative_holiday_key="national_day_2024",
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
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=strategy,
            fallback_strategy=("natural_date_shift",),
            annotation_rows=annotation_rows,
        )
        # national_day_2024 won't match baseline, mid_autumn_2024 also won't match (2023 ≠ 2024)
        oct1_pairing = resolution.bucket_pairing[0]
        self.assertEqual(oct1_pairing["pairing_reason"], "natural_date_shift")
