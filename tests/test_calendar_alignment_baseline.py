from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from app.analysis_core.calendar_alignment_baseline import resolve_calendar_baseline_window
from app.analysis_core.calendar_policy import CalendarBaselineGenerationRule


class CalendarAlignmentBaselineTests(unittest.TestCase):
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

    def test_previous_period_month_like_window_is_still_equal_length(self) -> None:
        window = resolve_calendar_baseline_window(
            current_window=(date(2026, 4, 1), date(2026, 5, 1)),
            rule=CalendarBaselineGenerationRule(strategy="previous_period"),
        )

        self.assertEqual(window, (date(2026, 3, 2), date(2026, 4, 1)))

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
