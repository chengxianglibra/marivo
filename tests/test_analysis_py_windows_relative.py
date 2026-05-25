from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis_py.windows as windows
from marivo.analysis_py.errors import (
    TimezoneInvalidError,
    WindowInvalidError,
    WindowRelativeParseError,
)
from marivo.analysis_py.windows.relative import parse_relative_expr
from marivo.analysis_py.windows.resolver import (
    coerce_as_of,
    resolve_to_absolute,
    zoneinfo_from_name,
)
from marivo.analysis_py.windows.spec import RelativeWindow


def test_windows_package_exports_are_usable():
    relative = windows.RelativeWindow(expr="last 7 days")
    normalized = windows.normalize_window_input("last 1 day")
    parsed = windows.parse_relative_expr("mtd")
    tz = windows.zoneinfo_from_name("Asia/Shanghai")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = windows.resolve_to_absolute(relative, as_of=as_of, tz=tz)

    assert isinstance(relative, windows.RelativeWindow)
    assert isinstance(normalized, windows.RelativeWindow)
    assert normalized.expr == "last 1 day"
    assert parsed.op == "to_date"
    assert parsed.unit == "month"
    assert resolved.start == "2026-05-18"
    assert resolved.end == "2026-05-24"


@pytest.mark.parametrize(
    ("expr", "op", "unit", "n"),
    [
        ("last 1 day", "last_n", "day", 1),
        ("last 7 days", "last_n", "day", 7),
        ("last 12 weeks", "last_n", "week", 12),
        ("last 7 months", "last_n", "month", 7),
        ("last 7 quarters", "last_n", "quarter", 7),
        ("last 7 years", "last_n", "year", 7),
        ("this week", "this", "week", None),
        ("this month", "this", "month", None),
        ("this quarter", "this", "quarter", None),
        ("this year", "this", "year", None),
        ("wtd", "to_date", "week", None),
        ("mtd", "to_date", "month", None),
        ("qtd", "to_date", "quarter", None),
        ("ytd", "to_date", "year", None),
        ("today", "today", None, None),
        ("yesterday", "yesterday", None, None),
    ],
)
def test_parse_relative_expr_supported_forms(expr, op, unit, n):
    parsed = parse_relative_expr(expr)
    assert parsed.op == op
    assert parsed.unit == unit
    assert parsed.n == n


@pytest.mark.parametrize("expr", ["", "last 7d", "this hour", "last 2 weeks - 1 day"])
def test_parse_relative_expr_rejects_unsupported_forms(expr):
    with pytest.raises(WindowRelativeParseError):
        parse_relative_expr(expr)


@pytest.mark.parametrize(
    ("expr", "op", "unit", "n"),
    [
        ("  LAST   7   DAYS  ", "last_n", "day", 7),
        ("  ThIs   WeEk  ", "this", "week", None),
        ("  MtD  ", "to_date", "month", None),
        ("  ToDaY  ", "today", None, None),
    ],
)
def test_parse_relative_expr_is_case_insensitive_and_whitespace_normalized(expr, op, unit, n):
    parsed = parse_relative_expr(expr)
    assert parsed.op == op
    assert parsed.unit == unit
    assert parsed.n == n


def test_last_7_days_resolves_to_exactly_7_local_dates():
    window = RelativeWindow(expr="last 7 days")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-05-18"
    assert resolved.end == "2026-05-24"


def test_last_1_day_resolves_to_single_day():
    window = RelativeWindow(expr="last 1 day")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-05-24"
    assert resolved.end == "2026-05-24"


def test_resolved_relative_outputs_are_date_only():
    window = RelativeWindow(expr="mtd", grain="day", tz="Asia/Shanghai")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-05-01"
    assert resolved.end == "2026-05-24"
    assert "T" not in resolved.start
    assert "T" not in resolved.end
    assert resolved.grain == "day"
    assert resolved.tz == "Asia/Shanghai"


@pytest.mark.parametrize("expr", ["this week", "wtd"])
def test_this_week_and_wtd_are_week_to_date(expr):
    window = RelativeWindow(expr=expr)
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-05-18"
    assert resolved.end == "2026-05-24"


def test_coerce_as_of_invalid_iso_raises_structured_error():
    with pytest.raises(WindowInvalidError) as exc_info:
        coerce_as_of("not-an-iso", tz=ZoneInfo("Asia/Shanghai"))
    assert exc_info.value.details["kind"] == "AsOfInvalid"


def test_coerce_as_of_non_string_input_raises_structured_error():
    with pytest.raises(WindowInvalidError) as exc_info:
        coerce_as_of(123, tz=ZoneInfo("Asia/Shanghai"))  # type: ignore[arg-type]
    assert exc_info.value.details["kind"] == "AsOfInvalid"


def test_coerce_as_of_naive_datetime_attaches_timezone():
    out = coerce_as_of("2026-05-24T13:42:11", tz=ZoneInfo("Asia/Shanghai"))
    assert out.tzinfo == ZoneInfo("Asia/Shanghai")
    assert out.isoformat() == "2026-05-24T13:42:11+08:00"


def test_coerce_as_of_aware_datetime_converts_timezone():
    out = coerce_as_of("2026-05-24T13:42:11+00:00", tz=ZoneInfo("Asia/Shanghai"))
    assert out.tzinfo == ZoneInfo("Asia/Shanghai")
    assert out.isoformat() == "2026-05-24T21:42:11+08:00"


def test_zoneinfo_from_name_invalid_raises_structured_error():
    with pytest.raises(TimezoneInvalidError) as exc_info:
        zoneinfo_from_name("Mars/Olympus")
    assert exc_info.value.details["kind"] == "TimezoneNotFound"


def test_zoneinfo_from_name_non_string_input_raises_structured_error():
    with pytest.raises(TimezoneInvalidError) as exc_info:
        zoneinfo_from_name(123)  # type: ignore[arg-type]
    assert exc_info.value.details["kind"] == "TimezoneNameInvalid"


def test_very_large_last_n_years_raises_structured_error():
    window = RelativeWindow(expr="last 50000 years")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    with pytest.raises(WindowInvalidError) as exc_info:
        resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert exc_info.value.details["kind"] == "WindowResolutionOverflow"


def test_this_month_resolves_to_month_to_date_boundaries():
    window = RelativeWindow(expr="this month")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-05-01"
    assert resolved.end == "2026-05-24"


def test_qtd_resolves_to_quarter_to_date_boundaries():
    window = RelativeWindow(expr="qtd")
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-04-01"
    assert resolved.end == "2026-05-24"


@pytest.mark.parametrize("expr", ["ytd", "this year"])
def test_ytd_and_this_year_resolve_to_year_to_date_boundaries(expr):
    window = RelativeWindow(expr=expr)
    as_of = datetime.fromisoformat("2026-05-24T13:42:11+08:00")
    resolved = resolve_to_absolute(window, as_of=as_of, tz=ZoneInfo("Asia/Shanghai"))
    assert resolved.start == "2026-01-01"
    assert resolved.end == "2026-05-24"
