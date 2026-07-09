"""Compiled SQL shape tests for cumulative metric queries."""

from __future__ import annotations

import ibis
import pytest


def _assert_plain_group_by(sql: str) -> None:
    upper = sql.upper()
    assert "GROUP BY" in upper
    assert " OVER " not in upper
    assert "WINDOW " not in upper


@pytest.mark.parametrize("dialect", ["duckdb", "trino", "clickhouse"])
def test_cumulative_sum_flow_query_compiles_without_window_functions(dialect: str) -> None:
    table = ibis.table(
        {
            "event_time": "timestamp",
            "region": "string",
            "amount": "float64",
        },
        name="events",
    )
    query = (
        table.mutate(bucket_start=table.event_time.truncate("D"))
        .group_by(["bucket_start", "region"])
        .aggregate(value=lambda t: t.amount.sum())
    )

    sql = ibis.to_sql(query, dialect=dialect)

    _assert_plain_group_by(sql)


@pytest.mark.parametrize("dialect", ["duckdb", "trino", "clickhouse"])
def test_cumulative_count_distinct_first_seen_query_compiles_without_window_functions(
    dialect: str,
) -> None:
    table = ibis.table(
        {
            "event_time": "timestamp",
            "region": "string",
            "user_id": "int64",
        },
        name="events",
    )
    first_seen = (
        table.filter(table.user_id.notnull())
        .group_by(["user_id", "region"])
        .aggregate(first_ts=lambda t: t.event_time.min())
    )
    query = (
        first_seen.mutate(bucket_start=first_seen.first_ts.truncate("D"))
        .group_by(["bucket_start", "region"])
        .aggregate(value=lambda t: t.count())
    )

    sql = ibis.to_sql(query, dialect=dialect)

    _assert_plain_group_by(sql)


# ---------------------------------------------------------------------------
# Task 12: dialect compile tests for the v2 cumulative shapes.
#
# Each test mirrors the REAL ibis expression shape built in observe.py for one
# of the three cumulative execution paths, and asserts the compiled SQL is
# window-free across duckdb/trino/clickhouse. The shapes are:
#
# 1. grain_to_date period-scoped first-seen dedup
#    (observe.py ~L1862-1868): group_by(key, dims, period_trunc(over)) -> min(over).
# 2. grain_to_date period-bounded seed
#    (observe.py ~L1886-1909): filter first_seen to the first reset period and
#    first_seen_ts < window.start, then group_by(dims) -> count(key).
# 3. trailing memtable-spine expansion join
#    (observe.py ~L1253-1275): cross_join(source, spine_memtable) + half-open
#    range filter + group_by(bucket, dims) -> nunique(key).
#
# The compile assertions are the binding spec: plain GROUP BY (no window
# functions) for the two grain_to_date shapes; JOIN present and no window
# functions for the trailing shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["duckdb", "trino", "clickhouse"])
def test_grain_to_date_period_scoped_first_seen_compiles_without_window_functions(
    dialect: str,
) -> None:
    """period-scoped first-seen: GROUP BY (key, dims, period_trunc(over)) -> min(over).

    Mirrors observe.py's grain_to_date count_distinct dedup: the event time is
    truncated to the reset grain (period_key) and the first-seen timestamp is
    the min event time per (distinct key, slice dims, period_key). An entity
    re-counts once per reset period. This must compile to a plain GROUP BY
    (no window functions) so the reset-partitioned cumsum post-process can
    reuse the result.
    """
    table = ibis.table(
        {
            "event_time": "timestamp",
            "region": "string",
            "user_id": "int64",
        },
        name="events",
    )
    # period_key = truncate(event_time, reset_grain). Month reset here.
    period_key = table.event_time.truncate("M").name("period_key")
    first_seen = (
        table.filter(table.user_id.notnull())
        .group_by(["user_id", "region", period_key])
        .aggregate(first_ts=lambda t: t.event_time.min())
    )

    sql = ibis.to_sql(first_seen, dialect=dialect)
    _assert_plain_group_by(sql)


@pytest.mark.parametrize("dialect", ["duckdb", "trino", "clickhouse"])
def test_grain_to_date_period_bounded_seed_compiles_without_window_functions(
    dialect: str,
) -> None:
    """period-bounded seed: aggregate over [period_start, window_start) per dims.

    Mirrors observe.py's grain_to_date seed query: from the period-scoped
    first_seen relation, filter to rows whose period_key equals the first
    reset period AND whose first_seen_ts precedes the window start, then
    group by the slice dims and count the distinct key. The seed is scoped
    to the first reset period only; entities first-seen in earlier periods
    do not carry in (they reset). Must compile to a plain GROUP BY.
    """
    first_seen = ibis.table(
        {
            "user_id": "int64",
            "region": "string",
            "period_key": "timestamp",
            "first_seen_ts": "timestamp",
        },
        name="first_seen",
    )
    first_period_start = ibis.timestamp("2026-02-01 00:00:00")
    window_start = ibis.timestamp("2026-02-15 00:00:00")
    seed = first_seen.filter(
        (first_seen["period_key"] == first_period_start.cast(first_seen["period_key"].type()))
        & (first_seen["first_seen_ts"] < window_start.cast(first_seen["first_seen_ts"].type()))
    )
    seed_grouped = (
        seed.group_by(["region"])
        .aggregate(value=seed["user_id"].count())
        .order_by(["region"])
        .select(["region", "value"])
    )

    sql = ibis.to_sql(seed_grouped, dialect=dialect)
    _assert_plain_group_by(sql)


@pytest.mark.parametrize("dialect", ["duckdb", "trino", "clickhouse"])
def test_trailing_memtable_spine_expansion_join_compiles(dialect: str) -> None:
    """memtable-spine expansion join: spine CROSS JOIN source ON time in
    (bucket_end-span, bucket_end] then GROUP BY (bucket, dims) count_distinct.

    Mirrors observe.py's trailing count_distinct path: an inline ibis.memtable
    spine (one row per display bucket, with precomputed _span_start/_span_end)
    is cross-joined onto the filtered source, filtered by a half-open range
    predicate so each event fans out to every bucket whose trailing span
    contains it, then grouped by (bucket, dims) with nunique. The compiled SQL
    must be a plain JOIN + GROUP BY + count_distinct (no window functions).
    """
    import pandas as pd

    source = ibis.table(
        {
            "event_time": "timestamp",
            "user_id": "int64",
        },
        name="events",
    )
    # Spine: one row per display bucket. trailing(count=7, unit='day') at day
    # grain -> span = 7 days, span_lead = 6 days, span_end = bucket + 1 day.
    spine_df = pd.DataFrame(
        {"bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])}
    )
    spine = ibis.memtable(spine_df)
    day_seconds = 24 * 60 * 60
    span_lead_seconds = 6 * day_seconds
    spine = spine.mutate(
        _span_start=(spine["bucket_start"] - ibis.interval(seconds=span_lead_seconds)),
        _span_end=(spine["bucket_start"] + ibis.interval(seconds=day_seconds)),
    )
    joined = source.cross_join(spine)
    joined = joined.filter(
        (source["event_time"] >= spine["_span_start"]) & (source["event_time"] < spine["_span_end"])
    )
    aggregated = (
        joined.group_by(["bucket_start"])
        .aggregate(value=joined["user_id"].nunique())
        .order_by(["bucket_start"])
        .select(["bucket_start", "value"])
    )

    sql = ibis.to_sql(aggregated, dialect=dialect)
    upper = sql.upper()
    assert "JOIN" in upper
    assert " OVER " not in upper
    assert "WINDOW " not in upper
