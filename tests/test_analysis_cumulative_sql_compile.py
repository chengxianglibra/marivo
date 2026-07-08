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
