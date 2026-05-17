from __future__ import annotations

import unittest

from scripts.build_cn_calendar import _build_calendar_rows, _d


class BuildCnCalendarTest(unittest.TestCase):
    def test_2026_calendar_rows_no_version_region(self) -> None:
        rows = _build_calendar_rows(
            _d("2026-01-01"),
            _d("2026-12-31"),
        )
        # New tuple layout: (calendar_date, weekday, is_weekend, is_workday,
        #                     holiday_name, holiday_group_id, year_relative_holiday_key)
        # No region_code or calendar_version columns
        by_date = {row[0]: row for row in rows}

        # At least one row per day — 365 for 2026
        unique_dates = len({row[0] for row in rows})
        self.assertEqual(unique_dates, 365)

        # Jan 2: holiday_name="元旦", holiday_group_id="new_year", is_workday=False
        self.assertEqual(by_date["2026-01-02"][4], "元旦")
        self.assertFalse(by_date["2026-01-02"][3])

        # Jan 4: adjusted workday, no holiday
        self.assertIsNone(by_date["2026-01-04"][4])
        self.assertTrue(by_date["2026-01-04"][3])

        # Feb 14: adjusted workday
        self.assertTrue(by_date["2026-02-14"][3])
        # Feb 23: Spring Festival holiday
        self.assertEqual(by_date["2026-02-23"][4], "春节")
        self.assertFalse(by_date["2026-02-23"][3])

        # May 9: adjusted workday
        self.assertIsNone(by_date["2026-05-09"][4])
        self.assertTrue(by_date["2026-05-09"][3])

        # Sep 26: Mid-Autumn
        self.assertEqual(by_date["2026-09-26"][4], "中秋节")
        self.assertFalse(by_date["2026-09-26"][3])

        # Sep 20: adjusted workday
        self.assertTrue(by_date["2026-09-20"][3])

        # Oct 8: regular workday
        self.assertIsNone(by_date["2026-10-08"][4])
        self.assertTrue(by_date["2026-10-08"][3])

    def test_2024_holiday_rows_have_group_id(self) -> None:
        rows = _build_calendar_rows(
            _d("2024-01-01"),
            _d("2024-12-31"),
        )
        # 2024-09-15 is mid_autumn start, 2024-10-01 is national_day start
        mid_autumn_rows = [row for row in rows if row[5] == "mid_autumn"]
        national_day_rows = [row for row in rows if row[5] == "national_day"]
        self.assertGreater(len(mid_autumn_rows), 0)
        self.assertGreater(len(national_day_rows), 0)
        # Verify holiday rows have correct fields
        first_mid = mid_autumn_rows[0]
        self.assertEqual(first_mid[4], "中秋节")

    def test_non_holiday_rows_have_empty_holiday_group_id(self) -> None:
        rows = _build_calendar_rows(
            _d("2026-01-01"),
            _d("2026-12-31"),
        )
        non_holiday_rows = [row for row in rows if row[4] is None]
        for row in non_holiday_rows:
            self.assertEqual(
                row[5], "", f"Non-holiday row for {row[0]} should have empty holiday_group_id"
            )


if __name__ == "__main__":
    unittest.main()
