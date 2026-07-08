"""Tests for the unified bucketing engine (sub-day epoch-floor support)."""

from datetime import datetime

import ibis

from marivo.analysis.executor.bucketing import bucket_start_expr
from marivo.analysis.windows.grain import Grain


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
    expr = bucket_start_expr(col, Grain(count=10, unit="minute"))
    out = [str(x) for x in con.to_pandas(t.mutate(b=expr))["b"]]
    assert out == [
        "2026-06-03 00:00:00",
        "2026-06-03 00:10:00",
        "2026-06-03 23:50:00",
    ]


def test_count_one_minute_matches_truncate():
    con = ibis.duckdb.connect(":memory:")
    t, col = _local_col()
    dynamic = bucket_start_expr(col, Grain(count=1, unit="minute"))
    truncated = col.truncate("m")
    a = list(con.to_pandas(t.mutate(b=dynamic))["b"])
    b = list(con.to_pandas(t.mutate(b=truncated))["b"])
    assert a == b
