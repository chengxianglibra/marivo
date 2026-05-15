from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb

REGION_CODE = "CN"
BUILD_START = date(2024, 1, 1)


@dataclass(frozen=True, slots=True)
class NoticeSource:
    title: str
    url: str
    published_at: date


@dataclass(frozen=True, slots=True)
class HolidayWindow:
    notice_year: int
    holiday_name: str
    holiday_group_id: str
    start: date
    end: date
    adjusted_workdays: tuple[date, ...]
    primary_source: NoticeSource
    validation_source: NoticeSource


def _d(value: str) -> date:
    return date.fromisoformat(value)


NOTICE_2024 = NoticeSource(
    title="国务院办公厅关于2024年部分节假日安排的通知",
    url="https://www.gov.cn/zhengce/zhengceku/202310/content_6911527.htm",
    published_at=_d("2023-10-25"),
)
NOTICE_2024_VALIDATION = NoticeSource(
    title="图解：国务院办公厅关于2024年部分节假日安排的通知",
    url="https://www.gov.cn/yaowen/liebiao/202310/content_6911540.htm",
    published_at=_d("2023-10-25"),
)
NOTICE_2025 = NoticeSource(
    title="国务院办公厅关于2025年部分节假日安排的通知",
    url="https://www.gov.cn/zhengce/zhengceku/202411/content_6986382.htm",
    published_at=_d("2024-11-12"),
)
NOTICE_2025_VALIDATION = NoticeSource(
    title="假期增2天！春节休8天，五一休5天，国庆中秋连休8天……2025年放假安排来了！",
    url="https://www.gov.cn/zhengce/jiedu/tujie/202411/content_6986385.htm",
    published_at=_d("2024-11-12"),
)
NOTICE_2026 = NoticeSource(
    title="国务院办公厅关于2026年部分节假日安排的通知",
    url="https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
    published_at=_d("2025-11-04"),
)
NOTICE_2026_VALIDATION = NoticeSource(
    title="图解：国务院办公厅关于2026年部分节假日安排的通知",
    url="https://www.gov.cn/yaowen/liebiao/202511/content_7047099.htm",
    published_at=_d("2025-11-04"),
)


HOLIDAY_WINDOWS: tuple[HolidayWindow, ...] = (
    HolidayWindow(
        notice_year=2024,
        holiday_name="元旦",
        holiday_group_id="new_year",
        start=_d("2023-12-30"),
        end=_d("2024-01-01"),
        adjusted_workdays=(),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2024,
        holiday_name="春节",
        holiday_group_id="spring_festival",
        start=_d("2024-02-10"),
        end=_d("2024-02-17"),
        adjusted_workdays=(_d("2024-02-04"), _d("2024-02-18")),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2024,
        holiday_name="清明节",
        holiday_group_id="qingming",
        start=_d("2024-04-04"),
        end=_d("2024-04-06"),
        adjusted_workdays=(_d("2024-04-07"),),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2024,
        holiday_name="劳动节",
        holiday_group_id="labor_day",
        start=_d("2024-05-01"),
        end=_d("2024-05-05"),
        adjusted_workdays=(_d("2024-04-28"), _d("2024-05-11")),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2024,
        holiday_name="端午节",
        holiday_group_id="dragon_boat",
        start=_d("2024-06-10"),
        end=_d("2024-06-10"),
        adjusted_workdays=(),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2024,
        holiday_name="中秋节",
        holiday_group_id="mid_autumn",
        start=_d("2024-09-15"),
        end=_d("2024-09-17"),
        adjusted_workdays=(_d("2024-09-14"),),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2024,
        holiday_name="国庆节",
        holiday_group_id="national_day",
        start=_d("2024-10-01"),
        end=_d("2024-10-07"),
        adjusted_workdays=(_d("2024-09-29"), _d("2024-10-12")),
        primary_source=NOTICE_2024,
        validation_source=NOTICE_2024_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2025,
        holiday_name="元旦",
        holiday_group_id="new_year",
        start=_d("2025-01-01"),
        end=_d("2025-01-01"),
        adjusted_workdays=(),
        primary_source=NOTICE_2025,
        validation_source=NOTICE_2025_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2025,
        holiday_name="春节",
        holiday_group_id="spring_festival",
        start=_d("2025-01-28"),
        end=_d("2025-02-04"),
        adjusted_workdays=(_d("2025-01-26"), _d("2025-02-08")),
        primary_source=NOTICE_2025,
        validation_source=NOTICE_2025_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2025,
        holiday_name="清明节",
        holiday_group_id="qingming",
        start=_d("2025-04-04"),
        end=_d("2025-04-06"),
        adjusted_workdays=(),
        primary_source=NOTICE_2025,
        validation_source=NOTICE_2025_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2025,
        holiday_name="劳动节",
        holiday_group_id="labor_day",
        start=_d("2025-05-01"),
        end=_d("2025-05-05"),
        adjusted_workdays=(_d("2025-04-27"),),
        primary_source=NOTICE_2025,
        validation_source=NOTICE_2025_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2025,
        holiday_name="端午节",
        holiday_group_id="dragon_boat",
        start=_d("2025-05-31"),
        end=_d("2025-06-02"),
        adjusted_workdays=(),
        primary_source=NOTICE_2025,
        validation_source=NOTICE_2025_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2025,
        holiday_name="国庆节、中秋节",
        holiday_group_id="national_day",
        start=_d("2025-10-01"),
        end=_d("2025-10-08"),
        adjusted_workdays=(_d("2025-09-28"), _d("2025-10-11")),
        primary_source=NOTICE_2025,
        validation_source=NOTICE_2025_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="元旦",
        holiday_group_id="new_year",
        start=_d("2026-01-01"),
        end=_d("2026-01-03"),
        adjusted_workdays=(_d("2026-01-04"),),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="春节",
        holiday_group_id="spring_festival",
        start=_d("2026-02-15"),
        end=_d("2026-02-23"),
        adjusted_workdays=(_d("2026-02-14"), _d("2026-02-28")),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="清明节",
        holiday_group_id="qingming",
        start=_d("2026-04-04"),
        end=_d("2026-04-06"),
        adjusted_workdays=(),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="劳动节",
        holiday_group_id="labor_day",
        start=_d("2026-05-01"),
        end=_d("2026-05-05"),
        adjusted_workdays=(_d("2026-05-09"),),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="端午节",
        holiday_group_id="dragon_boat",
        start=_d("2026-06-19"),
        end=_d("2026-06-21"),
        adjusted_workdays=(),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="中秋节",
        holiday_group_id="mid_autumn",
        start=_d("2026-09-25"),
        end=_d("2026-09-27"),
        adjusted_workdays=(),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
    HolidayWindow(
        notice_year=2026,
        holiday_name="国庆节、中秋节",
        holiday_group_id="national_day",
        start=_d("2026-10-01"),
        end=_d("2026-10-07"),
        adjusted_workdays=(_d("2026-09-20"), _d("2026-10-10")),
        primary_source=NOTICE_2026,
        validation_source=NOTICE_2026_VALIDATION,
    ),
)


def _format_offset(offset: int) -> str:
    return f"{offset:+d}"


def _iter_days(start: date, end: date) -> list[date]:
    day_count = (end - start).days + 1
    return [start + timedelta(days=offset) for offset in range(day_count)]


def _calendar_version(start: date, end: date) -> str:
    return f"cn_public_holiday_{start:%Y%m%d}_{end:%Y%m%d}_v1"


def _resolved_calendar_version(start: date, end: date) -> str:
    return f"calendar_data_cn_{start:%Y%m%d}_{end:%Y%m%d}_v1"


def _validate_schedule() -> None:
    holiday_dates: dict[date, str] = {}
    adjusted_dates: dict[date, str] = {}
    for window in HOLIDAY_WINDOWS:
        for holiday_date in _iter_days(window.start, window.end):
            existing = holiday_dates.get(holiday_date)
            if existing is not None:
                raise ValueError(
                    f"duplicate holiday date {holiday_date.isoformat()} in {existing} and "
                    f"{window.holiday_name}"
                )
            holiday_dates[holiday_date] = window.holiday_name
        for workday in window.adjusted_workdays:
            existing = adjusted_dates.get(workday)
            if existing is not None:
                raise ValueError(
                    f"duplicate adjusted workday {workday.isoformat()} in {existing} and "
                    f"{window.holiday_name}"
                )
            if workday.weekday() < 5:
                raise ValueError(
                    f"adjusted workday {workday.isoformat()} for {window.holiday_name} is not "
                    "a weekend"
                )
            adjusted_dates[workday] = window.holiday_name
    overlap = set(holiday_dates).intersection(adjusted_dates)
    if overlap:
        raise ValueError(f"holiday dates overlap adjusted workdays: {sorted(overlap)!r}")


def _build_notice_day_rows(
    start: date, end: date, calendar_version: str
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for window in HOLIDAY_WINDOWS:
        for offset, holiday_date in enumerate(_iter_days(window.start, window.end)):
            if not (start <= holiday_date <= end):
                continue
            rows.append(
                (
                    holiday_date.isoformat(),
                    REGION_CODE,
                    calendar_version,
                    window.notice_year,
                    "holiday",
                    window.holiday_name,
                    window.holiday_group_id,
                    offset,
                    None,
                    window.primary_source.title,
                    window.primary_source.url,
                    window.primary_source.published_at.isoformat(),
                    window.validation_source.title,
                    window.validation_source.url,
                    window.validation_source.published_at.isoformat(),
                    "matched",
                )
            )
        for adjusted_workday in window.adjusted_workdays:
            if not (start <= adjusted_workday <= end):
                continue
            rows.append(
                (
                    adjusted_workday.isoformat(),
                    REGION_CODE,
                    calendar_version,
                    window.notice_year,
                    "adjusted_workday",
                    window.holiday_name,
                    window.holiday_group_id,
                    None,
                    adjusted_workday.weekday() + 1,
                    window.primary_source.title,
                    window.primary_source.url,
                    window.primary_source.published_at.isoformat(),
                    window.validation_source.title,
                    window.validation_source.url,
                    window.validation_source.published_at.isoformat(),
                    "matched",
                )
            )
    rows.sort(key=lambda row: (row[0], row[4], row[3]))
    return rows


def _build_calendar_rows(start: date, end: date, calendar_version: str) -> list[tuple[object, ...]]:
    holiday_by_date: dict[date, tuple[str, str, str]] = {}
    adjusted_workdays = {
        workday for window in HOLIDAY_WINDOWS for workday in window.adjusted_workdays
    }
    for window in HOLIDAY_WINDOWS:
        for offset, holiday_date in enumerate(_iter_days(window.start, window.end)):
            holiday_by_date[holiday_date] = (
                window.holiday_name,
                window.holiday_group_id,
                f"{window.holiday_group_id}_d{_format_offset(offset)}",
            )

    rows: list[tuple[object, ...]] = []
    for calendar_date in _iter_days(start, end):
        weekday = calendar_date.weekday() + 1
        is_weekend = weekday >= 6
        holiday_annotation = holiday_by_date.get(calendar_date)
        if holiday_annotation is not None:
            holiday_name, holiday_group_id, year_relative_holiday_key = holiday_annotation
            is_workday = False
        else:
            holiday_name = None
            holiday_group_id = None
            year_relative_holiday_key = None
            is_workday = (not is_weekend) or calendar_date in adjusted_workdays
        rows.append(
            (
                calendar_date.isoformat(),
                REGION_CODE,
                calendar_version,
                weekday,
                is_weekend,
                is_workday,
                holiday_name,
                holiday_group_id,
                year_relative_holiday_key,
            )
        )
    return rows


CSV_COLUMNS = (
    "calendar_date",
    "region_code",
    "weekday",
    "is_weekend",
    "is_workday",
    "holiday_name",
    "holiday_group_id",
    "year_relative_holiday_key",
)


def _write_csv(output_path: Path, calendar_rows: list[tuple[object, ...]]) -> None:
    """Write calendar rows to a CSV file (calendar_version column excluded)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in calendar_rows:
            # calendar_rows tuple layout: (calendar_date, region_code, calendar_version,
            #   weekday, is_weekend, is_workday, holiday_name, holiday_group_id,
            #   year_relative_holiday_key)
            # CSV excludes calendar_version at index 2.
            writer.writerow(row[:2] + row[3:])


def _write_tables(
    db_path: Path, notice_rows: list[tuple[object, ...]], calendar_rows: list[tuple[object, ...]]
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        connection.execute("DROP TABLE IF EXISTS analytics.cn_holiday_notice_days")
        connection.execute(
            """
            CREATE TABLE analytics.cn_holiday_notice_days (
                calendar_date DATE NOT NULL,
                region_code VARCHAR NOT NULL,
                calendar_version VARCHAR NOT NULL,
                notice_year INTEGER NOT NULL,
                day_kind VARCHAR NOT NULL,
                holiday_name VARCHAR NOT NULL,
                holiday_group_id VARCHAR NOT NULL,
                relative_day_offset INTEGER,
                adjusted_workday_weekday INTEGER,
                primary_notice_title VARCHAR NOT NULL,
                primary_notice_url VARCHAR NOT NULL,
                primary_notice_published_at DATE NOT NULL,
                validation_notice_title VARCHAR NOT NULL,
                validation_notice_url VARCHAR NOT NULL,
                validation_notice_published_at DATE NOT NULL,
                validation_status VARCHAR NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO analytics.cn_holiday_notice_days VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            notice_rows,
        )

        connection.execute("DROP TABLE IF EXISTS analytics.cn_public_holiday")
        connection.execute(
            """
            CREATE TABLE analytics.cn_public_holiday (
                calendar_date DATE NOT NULL,
                region_code VARCHAR NOT NULL,
                calendar_version VARCHAR NOT NULL,
                weekday INTEGER NOT NULL,
                is_weekend BOOLEAN NOT NULL,
                is_workday BOOLEAN NOT NULL,
                holiday_name VARCHAR,
                holiday_group_id VARCHAR,
                year_relative_holiday_key VARCHAR
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO analytics.cn_public_holiday VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            calendar_rows,
        )
    finally:
        connection.close()


def _verify_tables(db_path: Path, start: date, end: date, calendar_version: str) -> None:
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        expected_days = (end - start).days + 1
        row_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM analytics.cn_public_holiday
            WHERE region_code = ? AND calendar_version = ?
            """,
            [REGION_CODE, calendar_version],
        ).fetchone()
        if row_count is None or row_count[0] != expected_days:
            raise ValueError(
                f"expected {expected_days} calendar rows, found {row_count[0] if row_count else 'none'}"
            )
        duplicate_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT calendar_date
                FROM analytics.cn_public_holiday
                WHERE region_code = ? AND calendar_version = ?
                GROUP BY calendar_date
                HAVING COUNT(*) > 1
            )
            """,
            [REGION_CODE, calendar_version],
        ).fetchone()
        if duplicate_count is None or duplicate_count[0] != 0:
            raise ValueError("cn_public_holiday contains duplicate calendar_date rows")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build deterministic CN holiday calendar tables in DuckDB or CSV."
    )
    parser.add_argument(
        "--format",
        choices=["duckdb", "csv"],
        default="csv",
        help="Output format: 'duckdb' for DuckDB tables, 'csv' for CSV file (default: csv).",
    )
    parser.add_argument(
        "--db-path",
        default="marivo.duckdb",
        help="DuckDB file to create or update (used with --format duckdb).",
    )
    parser.add_argument(
        "--csv-output",
        default="calendar_data.csv",
        help="CSV output file path (used with --format csv, default: calendar_data.csv).",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Inclusive calendar end date, defaults to today.",
    )
    args = parser.parse_args()

    end_date = _d(args.end_date)
    if end_date < BUILD_START:
        raise ValueError("end-date must be on or after 2024-01-01")
    max_supported = date(max(window.notice_year for window in HOLIDAY_WINDOWS), 12, 31)
    if end_date > max_supported:
        raise ValueError(
            f"end-date {end_date.isoformat()} exceeds supported schedule "
            f"{max_supported.isoformat()}"
        )

    _validate_schedule()
    calendar_version = _calendar_version(BUILD_START, end_date)
    notice_rows = _build_notice_day_rows(BUILD_START, end_date, calendar_version)
    calendar_rows = _build_calendar_rows(BUILD_START, end_date, calendar_version)

    if args.format == "csv":
        csv_path = Path(args.csv_output)
        _write_csv(csv_path, calendar_rows)
        print(f"csv_path={csv_path.resolve()}")
    else:
        db_path = Path(args.db_path)
        _write_tables(db_path, notice_rows, calendar_rows)
        _verify_tables(db_path, BUILD_START, end_date, calendar_version)
        print(f"db_path={db_path.resolve()}")

    print(f"calendar_version={calendar_version}")
    print(f"resolved_calendar_version={_resolved_calendar_version(BUILD_START, end_date)}")
    print(f"effective_start={BUILD_START.isoformat()}")
    print(f"effective_end={(end_date + timedelta(days=1)).isoformat()}")
    print(f"notice_rows={len(notice_rows)}")
    print(f"calendar_rows={len(calendar_rows)}")


if __name__ == "__main__":
    main()
