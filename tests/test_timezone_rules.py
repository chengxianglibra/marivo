"""Runtime timezone intervals follow ZoneInfo at exact transition boundaries."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from marivo.analysis.materialization.timezone_rules import offset_intervals


@pytest.mark.parametrize("year", [2026, 2050])
@pytest.mark.parametrize(
    "name",
    [
        "America/New_York",
        "Australia/Lord_Howe",
        "Europe/Dublin",
        "Africa/Casablanca",
        "Asia/Kathmandu",
    ],
)
def test_intervals_match_zoneinfo_at_boundaries_and_throughout_year(name: str, year: int) -> None:
    zone = ZoneInfo(name)
    start, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    intervals = offset_intervals(zone, start, end)
    assert intervals[0].start == start and intervals[-1].end == end
    for interval in intervals:
        for point in (interval.start, interval.end - timedelta(microseconds=1)):
            expected = point.replace(tzinfo=timezone.utc).astimezone(zone).utcoffset()
            assert expected == timedelta(seconds=interval.seconds)
    for hour in range(int((end - start).total_seconds()) // 3600):
        point = start + timedelta(hours=hour)
        matches = [interval for interval in intervals if interval.start <= point < interval.end]
        assert len(matches) == 1
        assert point.replace(tzinfo=timezone.utc).astimezone(zone).utcoffset() == timedelta(
            seconds=matches[0].seconds
        )


@pytest.mark.parametrize(
    "name,wall,expected",
    [
        ("America/New_York", datetime(2026, 3, 8, 2, 30), 0),
        ("America/New_York", datetime(2026, 11, 1, 1, 30), 2),
        ("Australia/Lord_Howe", datetime(2026, 4, 5, 1, 45), 2),
        ("Australia/Lord_Howe", datetime(2026, 10, 4, 2, 15), 0),
    ],
)
def test_wall_clock_candidate_count_matches_independent_examples(
    name: str, wall: datetime, expected: int
) -> None:
    intervals = offset_intervals(ZoneInfo(name), datetime(2026, 1, 1), datetime(2027, 1, 1))
    assert (
        sum(
            interval.start <= wall - timedelta(seconds=interval.seconds) < interval.end
            for interval in intervals
        )
        == expected
    )
