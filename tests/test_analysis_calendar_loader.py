from __future__ import annotations

import json
from pathlib import Path

import pytest

from marivo.analysis.calendar.loader import CalendarCache
from marivo.analysis.errors import CalendarNotFoundError, CalendarPolicyError


def test_calendar_cache_loads_project_local_json(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "cn_holidays.json").write_text(
        json.dumps(
            {
                "name": "cn_holidays",
                "timezone": "Asia/Shanghai",
                "holidays": [
                    {"date": "2026-01-01", "holiday_id": "new-year"},
                    {"date": "2026-02-17", "holiday_id": "spring-festival"},
                ],
                "adjusted_workdays": [{"date": "2026-02-14"}],
            }
        ),
        encoding="utf-8",
    )

    cache = CalendarCache(tmp_path)
    calendar = cache.get("cn_holidays")
    assert calendar.name == "cn_holidays"
    assert calendar.timezone == "Asia/Shanghai"
    assert [entry.date for entry in calendar.holidays] == ["2026-01-01", "2026-02-17"]
    assert [entry.date for entry in calendar.adjusted_workdays] == ["2026-02-14"]

    cached = cache.get("cn_holidays")
    assert cached is calendar
    assert cache.list_available() == ["cn_holidays"]


def test_calendar_cache_missing_file_raises(tmp_path):
    cache = CalendarCache(tmp_path)

    with pytest.raises(CalendarNotFoundError):
        cache.get("missing_calendar")


def test_calendar_cache_invalid_file_raises_policy_error(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "bad.json").write_text("{", encoding="utf-8")

    cache = CalendarCache(tmp_path)
    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get("bad")

    assert exc_info.value.details["kind"] == "CalendarFileInvalid"


@pytest.mark.parametrize(
    "calendar_name",
    ["", ".", "..", "../escape", "a/b", r"a\b", "a..b", "a b", "a$b"],
)
def test_calendar_cache_rejects_invalid_calendar_name(tmp_path, calendar_name):
    cache = CalendarCache(tmp_path)

    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get(calendar_name)

    assert exc_info.value.details["kind"] == "CalendarNameInvalid"
    assert exc_info.value.details["calendar_name"] == calendar_name


def test_calendar_cache_read_failure_raises_policy_error_with_read_failed_kind(
    tmp_path, monkeypatch
):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    target_file = calendar_dir / "cn_holidays.json"
    target_file.write_text(
        json.dumps({"name": "cn_holidays", "timezone": "Asia/Shanghai", "holidays": []}),
        encoding="utf-8",
    )

    original_read_text = Path.read_text

    def _broken_read_text(self: Path, *args, **kwargs):
        if self == target_file:
            raise OSError("boom")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _broken_read_text)

    cache = CalendarCache(tmp_path)
    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get("cn_holidays")

    assert exc_info.value.details["kind"] == "CalendarFileReadFailed"
    assert exc_info.value.details["calendar_name"] == "cn_holidays"


def test_calendar_cache_invalid_json_shape_missing_timezone_raises_policy_error(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "bad_shape.json").write_text(
        json.dumps({"name": "bad_shape", "holidays": []}),
        encoding="utf-8",
    )

    cache = CalendarCache(tmp_path)
    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get("bad_shape")

    assert exc_info.value.details["kind"] == "CalendarFileInvalid"
    assert "validation_errors" in exc_info.value.details


def test_calendar_cache_invalid_json_shape_extra_field_raises_policy_error(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "bad_shape_extra.json").write_text(
        json.dumps(
            {
                "name": "bad_shape_extra",
                "timezone": "Asia/Shanghai",
                "holidays": [],
                "unknown_field": "not_allowed",
            }
        ),
        encoding="utf-8",
    )

    cache = CalendarCache(tmp_path)
    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get("bad_shape_extra")

    assert exc_info.value.details["kind"] == "CalendarFileInvalid"
    assert "validation_errors" in exc_info.value.details


def test_calendar_cache_invalid_entry_date_raises_policy_error(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "bad_date.json").write_text(
        json.dumps(
            {
                "name": "bad_date",
                "timezone": "Asia/Shanghai",
                "holidays": [{"date": "2026/01/01"}],
            }
        ),
        encoding="utf-8",
    )

    cache = CalendarCache(tmp_path)
    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get("bad_date")

    assert exc_info.value.details["kind"] == "CalendarFileInvalid"
    assert "validation_errors" in exc_info.value.details


def test_calendar_cache_invalid_timezone_raises_policy_error(tmp_path):
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "bad_timezone.json").write_text(
        json.dumps(
            {
                "name": "bad_timezone",
                "timezone": "Mars/Olympus",
                "holidays": [],
            }
        ),
        encoding="utf-8",
    )

    cache = CalendarCache(tmp_path)
    with pytest.raises(CalendarPolicyError) as exc_info:
        cache.get("bad_timezone")

    assert exc_info.value.details["kind"] == "CalendarFileInvalid"
    assert "validation_errors" in exc_info.value.details
