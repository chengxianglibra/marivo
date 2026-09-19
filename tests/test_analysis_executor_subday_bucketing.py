"""Tests for the unified bucketing engine on one civil-midnight anchor."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import ibis
import pandas as pd
import pytest

from marivo.analysis import grain
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.temporal import bucket as bucket_start_expr
from marivo.analysis.operators.rollup import bucket_bounds

DIALECTS = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")

UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}

# Counts whose width divides one civil day.  The grid therefore restarts at
# local midnight, so no engine-specific origin constant is involved.
ADMITTED = (
    ("hour", 3),
    ("hour", 6),
    ("hour", 8),
    ("hour", 12),
    ("minute", 30),
    ("second", 30),
)

ZONES = ("UTC", "Asia/Shanghai", "Asia/Kathmandu", "America/New_York")

# Local wall clocks on ordinary days and on both North-American DST days.
WALLS = (
    datetime(2026, 6, 3, 0, 0, 0),
    datetime(2026, 6, 3, 3, 30, 0),
    datetime(2026, 3, 8, 1, 30, 0),
    datetime(2026, 3, 8, 6, 30, 0),
    datetime(2026, 11, 1, 1, 30, 0),
    datetime(2026, 11, 1, 6, 30, 0),
    datetime(2026, 6, 3, 23, 59, 59),
)


def civil_bucket(wall: datetime, unit: str, count: int) -> datetime:
    """Derive one naive bucket start from stdlib field arithmetic alone.

    The oracle rebuilds the civil day boundary and adds the floored in-day
    offset; it never calls the production expression or any bucket oracle.
    """
    width = count * UNIT_SECONDS[unit]
    seconds_of_day = wall.hour * 3600 + wall.minute * 60 + wall.second
    offset = (seconds_of_day // width) * width
    return datetime(wall.year, wall.month, wall.day) + timedelta(seconds=offset)


def _local_col():
    t = ibis.memtable(
        {
            "ts": [
                datetime(2026, 6, 3, 0, 7, 30),
                datetime(2026, 6, 3, 0, 12, 0),
                datetime(2026, 6, 3, 23, 55, 0),
            ]
        }
    )
    return t, t.ts


def test_subday_bucket_floors_to_local_midnight_anchor():
    con = ibis.duckdb.connect(":memory:")
    t, col = _local_col()
    expr = bucket_start_expr(col, grain("minute", count=10))
    out = [str(x) for x in con.to_pandas(t.mutate(b=expr))["b"]]
    assert out == [
        "2026-06-03 00:00:00",
        "2026-06-03 00:10:00",
        "2026-06-03 23:50:00",
    ]


def test_count_one_minute_matches_truncate():
    con = ibis.duckdb.connect(":memory:")
    t, col = _local_col()
    dynamic = bucket_start_expr(col, grain("minute"))
    truncated = col.truncate("m")
    a = list(con.to_pandas(t.mutate(b=dynamic))["b"])
    b = list(con.to_pandas(t.mutate(b=truncated))["b"])
    assert a == b


@pytest.mark.parametrize("unit,count", ADMITTED)
@pytest.mark.parametrize("dialect", DIALECTS)
def test_multi_unit_bucket_compiles_to_native_scalar_sql(
    unit: str, count: int, dialect: str
) -> None:
    """The civil-midnight expression must stay native scalar SQL everywhere.

    Engine bucket primitives own their own origins, so their presence would mean
    the anchor was delegated to the engine again.
    """
    table = ibis.table({"ts": "timestamp"}, name="probe")
    expression = bucket_start_expr(table.ts, grain(unit, count=count))
    sql = ibis.to_sql(table.select(b=expression), dialect=dialect).upper()
    assert sql
    for primitive in ("TIME_BUCKET", "DATE_BIN", "TOSTARTOFINTERVAL"):
        assert primitive not in sql


@pytest.mark.parametrize("unit,count", ADMITTED)
@pytest.mark.parametrize("engine", ["duckdb", "sqlite"])
def test_multi_unit_bucket_matches_the_civil_oracle(unit: str, count: int, engine: str) -> None:
    """Both local engines execute the generated expression against the oracle grid."""
    connection = (
        ibis.duckdb.connect(":memory:") if engine == "duckdb" else ibis.sqlite.connect(":memory:")
    )
    try:
        table = connection.create_table(
            f"bucket_probe_{unit}_{count}",
            ibis.memtable({"ts": WALLS}, schema={"ts": "timestamp"}),
        )
        actual = connection.to_pandas(
            table.select(b=bucket_start_expr(table.ts, grain(unit, count=count)))
        )["b"].tolist()
    finally:
        connection.disconnect()
    assert [pd.Timestamp(value).to_pydatetime() for value in actual] == [
        civil_bucket(wall, unit, count) for wall in WALLS
    ]


def test_multi_unit_bucket_never_lands_on_a_continuous_epoch_grid() -> None:
    """A continuous origin would place 10:30 on 09:00; the civil grid uses 06:00."""
    connection = ibis.duckdb.connect(":memory:")
    try:
        table = connection.create_table(
            "epoch_probe",
            ibis.memtable(
                {
                    "ts": [datetime(2026, 6, 3, 3, 30, 0), datetime(2026, 6, 3, 10, 30, 0)],
                },
                schema={"ts": "timestamp"},
            ),
        )
        actual = connection.to_pandas(
            table.select(b=bucket_start_expr(table.ts, grain("hour", count=6)))
        )["b"].tolist()
    finally:
        connection.disconnect()
    assert [pd.Timestamp(value).to_pydatetime() for value in actual] == [
        datetime(2026, 6, 3, 0, 0, 0),
        datetime(2026, 6, 3, 6, 0, 0),
    ]


@pytest.mark.parametrize("unit,token", [("hour", "h"), ("minute", "m"), ("second", "s")])
def test_count_one_subday_grain_still_uses_native_truncate(unit: str, token: str) -> None:
    """count == 1 keeps the civil-unit truncate path, so both must agree exactly."""
    connection = ibis.duckdb.connect(":memory:")
    try:
        table = connection.create_table(
            f"regression_probe_{unit}",
            ibis.memtable({"ts": WALLS}, schema={"ts": "timestamp"}),
        )
        actual = connection.to_pandas(table.select(b=bucket_start_expr(table.ts, grain(unit))))[
            "b"
        ].tolist()
        truncated = connection.to_pandas(table.select(b=table.ts.truncate(token)))["b"].tolist()
    finally:
        connection.disconnect()
    assert list(actual) == list(truncated)
    assert [pd.Timestamp(value).to_pydatetime() for value in actual] == [
        civil_bucket(wall, unit, 1) for wall in WALLS
    ]


@pytest.mark.parametrize("unit,count", ADMITTED)
def test_bucket_bounds_matches_the_civil_grid_for_naive_wall_clocks(unit: str, count: int) -> None:
    """The persisted fold coordinate is a naive boundary-zone wall clock.

    Retained continuation reads back naive civil values, so the local oracle must
    reproduce exactly the same grid the source expression produced.
    """
    width = count * UNIT_SECONDS[unit]
    for wall in WALLS:
        start, end = bucket_bounds(pd.Timestamp(wall), grain(unit, count=count), None)
        assert start == pd.Timestamp(civil_bucket(wall, unit, count))
        assert start.tzinfo is None and end.tzinfo is None
        assert (end - start).total_seconds() == width


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("unit,count", [("hour", 6), ("hour", 12)])
def test_bucket_bounds_keeps_an_aware_coordinate_on_its_own_civil_day(
    zone: str, unit: str, count: int
) -> None:
    """An aware coordinate is bucketed in its own zone on the civil-midnight grid.

    The retired continuous origin drifted off the civil day whenever the zone
    offset changed inside a grid, so the expectation is recomputed from
    ``datetime``/``timedelta`` civil arithmetic rather than from an origin.
    """
    for wall in WALLS:
        stamp = pd.Timestamp(wall).tz_localize(zone, ambiguous=True)
        start, end = bucket_bounds(stamp, grain(unit, count=count), None)
        naive_start = civil_bucket(wall, unit, count)
        assert start == pd.Timestamp(naive_start).tz_localize(zone, ambiguous=True)
        assert end == pd.Timestamp(naive_start + timedelta(seconds=count * 3600)).tz_localize(
            zone, ambiguous=True
        )


@pytest.mark.parametrize(
    "zone,month,day,expected",
    [
        ("UTC", 3, 8, 6 * 3600),
        ("UTC", 11, 1, 6 * 3600),
        ("Asia/Kathmandu", 3, 8, 6 * 3600),
        ("Asia/Kathmandu", 11, 1, 6 * 3600),
        ("America/New_York", 3, 8, 5 * 3600),
        ("America/New_York", 11, 1, 7 * 3600),
    ],
)
def test_bucket_bounds_reports_the_civil_width_of_a_transition_bucket(
    zone: str, month: int, day: int, expected: int
) -> None:
    """A 6-hour bucket spanning a transition keeps its civil endpoints.

    The documented consequence is a 5-hour spring-forward bucket and a 7-hour
    fall-back bucket; both still start at local midnight of that day.
    """
    stamp = pd.Timestamp(datetime(2026, month, day, 3, 0)).tz_localize(zone)
    start, end = bucket_bounds(stamp, grain("hour", count=6), None)
    assert start == pd.Timestamp(datetime(2026, month, day)).tz_localize(zone)
    assert (end - start).total_seconds() == expected


@pytest.mark.parametrize(
    "zone,day,first_start_hour,offset_hours",
    [
        # Midnight itself is skipped on these days, so the first bucket starts at
        # 01:00 local.  The retired normalize() leaked a raw pytz
        # NonExistentTimeError here instead of resolving anything.
        ("America/Havana", "2026-03-08", 1, -4),
        ("America/Santiago", "2026-09-06", 1, -3),
        # A two-o'clock transition is unchanged: midnight still exists at 00:00.
        ("America/New_York", "2026-03-08", 0, -5),
        ("America/New_York", "2026-11-01", 0, -4),
    ],
)
@pytest.mark.parametrize(
    "unit,count,first_end_hour",
    [("hour", 6, 6), ("hour", 12, 12), ("day", 1, 0)],
)
def test_bucket_bounds_never_leaks_a_third_party_transition_error(
    zone: str,
    day: str,
    first_start_hour: int,
    offset_hours: int,
    unit: str,
    count: int,
    first_end_hour: int,
) -> None:
    """A transition landing on local midnight must resolve, not raise pytz.

    The grid stays on civil fields, so the first bucket of such a day starts at
    the first existing instant (01:00 local) with the post-transition offset,
    rather than a raw third-party error or a silently shifted wall clock.  Its
    end is the next civil grid point, not the start shifted by real seconds, so
    a six-hour bucket of such a day keeps six civil hours (five real ones).
    """
    stamp = pd.Timestamp(day + " 02:00").tz_localize(
        zone, ambiguous=True, nonexistent="shift_forward"
    )
    start, end = bucket_bounds(stamp, grain(unit, count=count), None)
    assert (start.hour, start.minute) == (first_start_hour, 0)
    assert start.utcoffset() == timedelta(hours=offset_hours)
    assert (end.hour, end.minute) == (first_end_hour, 0)
    assert start < end


# Hand-computed UTC instants of one day's six-hour grid.  Each zone's own
# transition table gives the local grid points, and the offset in force at each
# one converts them to UTC.  Havana jumps 00:00 -> 01:00 local (-05:00 ->
# -04:00) at 05:00Z, Santiago jumps 00:00 -> 01:00 local (-04:00 -> -03:00) at
# 04:00Z, and Troll jumps 01:00 -> 03:00 local (+00:00 -> +02:00) at 01:00Z.
# The skipped local midnight means the first grid point of those days is 01:00
# (Havana, Santiago) or 00:00 (Troll, whose jump is at 01:00), so the first
# bucket is genuinely short while every later bucket is six wall-clock hours.
SIX_HOUR_GRID_UTC = (
    # 01:00, 06:00, 12:00, 18:00 local and the next local midnight.
    ("America/Havana", "2026-03-08", 5, ((0, 0), (5, 0), (11, 0), (17, 0), (23, 0))),
    ("America/Santiago", "2026-09-06", 4, ((0, 0), (5, 0), (11, 0), (17, 0), (23, 0))),
    # 00:00, 06:00, 12:00, 18:00 local and the next local midnight.
    ("Antarctica/Troll", "2026-03-29", 0, ((0, 0), (4, 0), (10, 0), (16, 0), (22, 0))),
    # A two-o'clock transition still starts the day at local midnight.
    ("America/New_York", "2026-03-08", 5, ((0, 0), (5, 0), (11, 0), (17, 0), (23, 0))),
    ("America/New_York", "2026-11-01", 4, ((0, 0), (7, 0), (13, 0), (19, 0), (25, 0))),
)


@pytest.mark.parametrize("zone,day,first_utc_hour,offsets", SIX_HOUR_GRID_UTC)
def test_six_hour_buckets_of_a_transition_day_partition(
    zone: str, day: str, first_utc_hour: int, offsets: tuple[tuple[int, int], ...]
) -> None:
    """Adjacent six-hour buckets meet exactly: no overlap and no gap.

    The oracle is the hand-computed UTC grid above, converted back with
    ``zoneinfo`` alone; no production bucketing helper takes part.  A retired
    implementation derived the end by shifting the start, so on a day whose
    local midnight is skipped the first bucket ended at 07:00 local and
    overlapped the 06:00 bucket by one hour.
    """
    anchor = pd.Timestamp(day, tz="UTC") + timedelta(hours=first_utc_hour)
    expected = [anchor + timedelta(hours=hour, minutes=minute) for hour, minute in offsets]
    for index, bucket_start in enumerate(expected[:-1]):
        probe = bucket_start + (expected[index + 1] - bucket_start) / 2
        start, end = bucket_bounds(probe.tz_convert(zone), grain("hour", count=6), None)
        assert start.tz_convert("UTC") == bucket_start
        assert end.tz_convert("UTC") == expected[index + 1]
        assert start < end


# Hand-computed UTC buckets for a one-hour spring gap, each derived from the
# zone's own transition instant and offsets.
#
# America/Adak jumps 02:00 -> 03:00 local (-10:00 -> -09:00) at 12:00Z on
# 2026-03-08, so the 02:00 grid point of a two-hour bucket is skipped and
# resolves onto the transition, 03:00 local = 12:00Z.
#   wall 01:00 = 11:00Z -> [00:00 local = 10:00Z, skipped 02:00 -> 12:00Z)
#   wall 03:00 = 12:00Z -> [skipped 02:00 -> 12:00Z, 04:00 local = 13:00Z)
#   wall 04:00 = 13:00Z -> [04:00 local = 13:00Z, 06:00 local = 15:00Z)
#
# Australia/Lord_Howe jumps 02:00 -> 02:30 local (+10:30 -> +11:00) at 15:30Z
# on 2026-10-03, so its 02:00 grid point is skipped and resolves onto 02:30
# local = 15:30Z.
#   wall 01:30 = 15:00Z -> [01:00 local = 14:30Z, skipped 02:00 -> 15:30Z)
#   wall 02:30 = 15:30Z -> [skipped 02:00 -> 15:30Z, 03:00 local = 16:00Z)
#   wall 03:00 = 16:00Z -> [03:00 local = 16:00Z, 04:00 local = 17:00Z)
#
# A resolver that rounds a skipped label backward by the pre-transition offset
# can place an instant outside its own bucket, which these must not do.
HOUR_GAP_BUCKETS_UTC = (
    (
        "America/Adak",
        "2026-03-08",
        "hour",
        2,
        (("11:00", "10:00", "12:00"), ("12:00", "12:00", "13:00"), ("13:00", "13:00", "15:00")),
    ),
    (
        "Australia/Lord_Howe",
        "2026-10-03",
        "hour",
        1,
        (("15:00", "14:30", "15:30"), ("15:30", "15:30", "16:00"), ("16:00", "16:00", "17:00")),
    ),
)


@pytest.mark.parametrize("zone,day,unit,count,instants", HOUR_GAP_BUCKETS_UTC)
def test_buckets_across_an_hour_wide_gap_contain_their_instants(
    zone: str,
    day: str,
    unit: str,
    count: int,
    instants: tuple[tuple[str, str, str], ...],
) -> None:
    """A one-hour spring gap must not push an instant out of its own bucket.

    The oracle is the hand-computed UTC triple ``(instant, bucket start, bucket
    end)`` above.  Consecutive buckets share an endpoint exactly, so the grid
    stays a partition even where a grid point is skipped.
    """
    for instant_text, start_text, end_text in instants:
        instant = pd.Timestamp(f"{day} {instant_text}", tz="UTC")
        start, end = bucket_bounds(instant.tz_convert(zone), grain(unit, count=count), None)
        assert start.tz_convert("UTC") == pd.Timestamp(f"{day} {start_text}", tz="UTC")
        assert end.tz_convert("UTC") == pd.Timestamp(f"{day} {end_text}", tz="UTC")
        assert start <= instant.tz_convert(zone) < end


@pytest.mark.parametrize(
    "zone,day,first_grid_local_hour,next_day",
    [
        ("America/Havana", "2026-03-08", 1, "2026-03-09"),
        ("America/Santiago", "2026-09-06", 1, "2026-09-07"),
        ("Antarctica/Troll", "2026-03-29", 0, "2026-03-30"),
    ],
)
def test_day_buckets_of_a_midnight_skip_day_end_at_the_next_civil_midnight(
    zone: str, day: str, first_grid_local_hour: int, next_day: str
) -> None:
    """A day bucket ends at the next civil midnight, not twenty-four real hours on.

    Hand-computed: the first existing instant of the transition day is
    ``first_grid_local_hour`` local, and the next civil midnight is hour zero of
    the following day in the zone's own post-transition offset.
    """
    zoneinfo_zone = ZoneInfo(zone)
    start, end = bucket_bounds(
        pd.Timestamp(day + " 12:00", tz=zoneinfo_zone), grain("day", count=1), None
    )
    assert start == pd.Timestamp(f"{day} {first_grid_local_hour:02d}:00", tz=zoneinfo_zone)
    assert end == pd.Timestamp(next_day, tz=zoneinfo_zone)
    assert start < end


def test_divisor_hour_counts_are_admitted_and_neighbours_are_not() -> None:
    """Every divisor of 24 hours is admitted; a non-divisor has no unique anchor."""
    table = ibis.table({"ts": "timestamp"}, name="probe")
    for count in (1, 2, 3, 4, 6, 8, 12, 24):
        assert bucket_start_expr(table.ts, grain("hour", count=count)) is not None
    for count in (5, 7, 9, 11, 13, 23, 25):
        with pytest.raises(DatasetCompilationError) as failure:
            bucket_start_expr(table.ts, grain("hour", count=count))
        assert f"{count}hour" in str(failure.value)
        assert "24 hours" in str(failure.value)


@pytest.mark.parametrize(
    "unit,count", [("hour", 7), ("minute", 1000), ("second", 100000), ("minute", 7)]
)
def test_bucket_bounds_rejects_a_non_divisor_width(unit: str, count: int) -> None:
    """The local fold oracle fails closed instead of silently choosing an origin."""
    with pytest.raises(DatasetCompilationError) as failure:
        bucket_bounds(
            pd.Timestamp(datetime(2026, 6, 3, 3, 0)).tz_localize("Asia/Shanghai"),
            grain(unit, count=count),
            None,
        )
    assert f"{count}{unit}" in str(failure.value)
