from __future__ import annotations

import unittest
from datetime import date

from app.analysis_core.calendar_alignment_pairing import (
    build_calendar_annotation_rows,
    resolve_calendar_bucket_pairing,
)
from app.analysis_core.calendar_policy import get_calendar_policy


def _annotation(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
    event_group_id: str | None = None,
    year_relative_event_key: str | None = None,
) -> dict[str, object]:
    day_value = date.fromisoformat(day)
    return {
        "calendar_date": day,
        "weekday": day_value.weekday() + 1,
        "holiday_group_id": holiday_group_id,
        "year_relative_holiday_key": year_relative_holiday_key,
        "event_group_id": event_group_id,
        "year_relative_event_key": year_relative_event_key,
    }


class CalendarAlignmentPairingTests(unittest.TestCase):
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
        self.assertEqual(resolution.comparability_warnings, [])

    def test_holiday_policy_matches_unique_cluster_before_fallback(self) -> None:
        current_window = (date(2026, 4, 4), date(2026, 4, 5))
        baseline_window = (date(2025, 4, 4), date(2025, 4, 5))
        policy = get_calendar_policy("calendar_policy.holiday_yoy")

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

    def test_holiday_policy_uses_relative_key_when_cluster_is_not_unique(self) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 4))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 4))
        policy = get_calendar_policy("calendar_policy.holiday_yoy")

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

    def test_holiday_policy_records_unmapped_and_fallback_when_relative_key_is_missing(
        self,
    ) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 2))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 2))
        policy = get_calendar_policy("calendar_policy.holiday_yoy")

        resolution = resolve_calendar_bucket_pairing(
            current_window=current_window,
            baseline_window=baseline_window,
            matching_strategy=policy.matching_strategy,
            fallback_strategy=policy.fallback_strategy,
            annotation_rows=build_calendar_annotation_rows(
                current_window=current_window,
                baseline_window=baseline_window,
                raw_rows=[
                    _annotation("2026-04-01", holiday_group_id="qingming"),
                    _annotation("2025-04-01"),
                ],
            ),
        )

        self.assertEqual(resolution.bucket_pairing[0]["pairing_reason"], "natural_date_shift")
        self.assertEqual(
            resolution.bucket_pairing[0]["issues"],
            ["holiday_cluster_unmapped", "fallback_applied"],
        )
        self.assertEqual(
            resolution.comparability_warnings,
            ["holiday_cluster_unmapped", "fallback_applied"],
        )

    def test_holiday_policy_records_unmapped_and_fallback_when_cluster_is_missing_in_baseline(
        self,
    ) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 2))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 2))
        policy = get_calendar_policy("calendar_policy.holiday_yoy")

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
        self.assertEqual(
            resolution.comparability_warnings,
            ["holiday_cluster_unmapped", "fallback_applied"],
        )

    def test_holiday_policy_marks_fallback_only_for_buckets_that_downgrade(self) -> None:
        current_window = (date(2026, 4, 1), date(2026, 4, 3))
        baseline_window = (date(2025, 4, 1), date(2025, 4, 3))
        policy = get_calendar_policy("calendar_policy.holiday_yoy")

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
        self.assertEqual(
            resolution.comparability_warnings,
            ["holiday_cluster_unmapped", "fallback_applied"],
        )

    def test_event_policy_uses_event_relative_key(self) -> None:
        current_window = (date(2026, 6, 15), date(2026, 6, 17))
        baseline_window = (date(2026, 5, 15), date(2026, 5, 17))
        policy = get_calendar_policy("calendar_policy.event_mom")

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
                        "2026-06-15",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d-1",
                    ),
                    _annotation(
                        "2026-05-15",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d-1",
                    ),
                    _annotation(
                        "2026-06-16",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d+0",
                    ),
                    _annotation(
                        "2026-05-16",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d+0",
                    ),
                ],
            ),
        )

        self.assertEqual(
            [bucket["pairing_reason"] for bucket in resolution.bucket_pairing],
            ["year_relative_event_key", "year_relative_event_key"],
        )

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
