from __future__ import annotations

import unittest

from scripts.build_cn_calendar import _build_calendar_rows, _d


class BuildCnCalendarTest(unittest.TestCase):
    def test_2026_notice_dates_match_official_schedule(self) -> None:
        rows = _build_calendar_rows(
            _d("2026-01-01"),
            _d("2026-12-31"),
            "cn_public_holiday_20240101_20261231_v1",
        )
        by_date = {row[0]: row for row in rows}

        self.assertEqual(len(rows), 365)

        self.assertEqual(by_date["2026-01-02"][6], "元旦")
        self.assertFalse(by_date["2026-01-02"][5])

        self.assertIsNone(by_date["2026-01-04"][6])
        self.assertTrue(by_date["2026-01-04"][5])

        self.assertTrue(by_date["2026-02-14"][5])
        self.assertEqual(by_date["2026-02-23"][6], "春节")
        self.assertFalse(by_date["2026-02-23"][5])

        self.assertIsNone(by_date["2026-05-09"][6])
        self.assertTrue(by_date["2026-05-09"][5])

        self.assertEqual(by_date["2026-09-26"][6], "中秋节")
        self.assertFalse(by_date["2026-09-26"][5])

        self.assertTrue(by_date["2026-09-20"][5])

        self.assertIsNone(by_date["2026-10-08"][6])
        self.assertTrue(by_date["2026-10-08"][5])


if __name__ == "__main__":
    unittest.main()
