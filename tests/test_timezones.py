"""Timezone names have one canonical identity, independent of host clocks."""

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from marivo._timezones import normalize_timezone, same_timezone


@pytest.mark.parametrize(
    "value",
    (
        "UTC",
        "+00:00",
        "-00:00",
        "UTC+00:00:00",
        "Etc/UTC",
        "Etc/UCT",
        "UCT",
        "Etc/Universal",
        "Universal",
        "Etc/Zulu",
        "Zulu",
        "GMT",
        "Etc/GMT",
        "Etc/GMT0",
        "Etc/GMT+0",
        "Etc/GMT-0",
        "GMT0",
        "GMT+0",
        "GMT-0",
        "Etc/Greenwich",
        "Greenwich",
    ),
)
def test_zero_offsets_and_utc_links_have_one_iana_identity(value: str) -> None:
    result = normalize_timezone(value)
    assert result.name == "UTC"
    assert result.kind == "iana"
    assert isinstance(result.tz, ZoneInfo)
    assert result.tz.key == "UTC"
    assert same_timezone(value, "UTC")


@pytest.mark.parametrize(
    ("value", "name", "seconds"),
    (
        ("Etc/GMT-8", "UTC+08:00", 28800),
        ("Etc/GMT+8", "UTC-08:00", -28800),
        ("+05:45:00", "UTC+05:45", 20700),
        ("UTC-00:30:05", "UTC-00:30:05", -1805),
        ("+23:59:59", "UTC+23:59:59", 86399),
    ),
)
def test_fixed_offsets_have_canonical_names_and_numeric_offsets(
    value: str, name: str, seconds: int
) -> None:
    result = normalize_timezone(value)
    assert result.name == name
    assert result.kind == "fixed_offset"
    assert isinstance(result.tz, timezone)
    assert result.tz.utcoffset(None) == timedelta(seconds=seconds)
    assert same_timezone(value, name)


def test_other_loadable_iana_links_retain_distinct_identities() -> None:
    assert normalize_timezone("PRC").name == "PRC"
    assert normalize_timezone("Asia/Shanghai").name == "Asia/Shanghai"
    assert not same_timezone("PRC", "Asia/Shanghai")


@pytest.mark.parametrize(
    "value",
    (
        "",
        " UTC",
        "UTC ",
        "localtime",
        "/usr/share/zoneinfo/UTC",
        ":UTC",
        "UTC-8",
        "EST5EDT,M3.2.0,M11.1.0",
        "Mars/Olympus",
        "+24:00",
        "+08:60",
        "+08:00:60",
        "Etc/GMT-15",
    ),
)
def test_invalid_timezone_names_are_rejected(value: str) -> None:
    with pytest.raises((ValueError, KeyError)):
        normalize_timezone(value)
