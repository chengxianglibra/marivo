"""Regression: Marivo translates Python strptime formats for Trino/Presto.

Trino and Presto ``date_parse`` use MySQL format specifiers, which agree with
Python strptime on most common tokens (``%Y %m %d %H %S %y %j``) but disagree
on several — most importantly ``%M`` (Python = minute, MySQL = month name;
MySQL minute is ``%i``). ibis translates ``%S``->``%s`` but leaves ``%M``
unchanged, so a Python strptime format like ``%Y-%m-%d %H:%M:%S`` would
malform on Trino at the minute position.

Marivo now translates Python strptime to MySQL at SQL-emission time (via
``python_to_mysql_strptime``), gated on backend dialect, so a single authored
format works on every backend. DuckDB and other Python-strptime-native
backends receive the format unchanged.

History: Marivo previously had a regex patch ``_fix_trino_date_parse`` that
wrongly converted strptime to Joda, causing ``INVALID_FUNCTION_ARGUMENT``.
That patch was removed because ibis emits strptime (not Joda) directly. The
``%M``/``%i`` divergence is now handled by an explicit, dialect-gated
translation layer rather than SQL rewriting.
"""

import ibis

from marivo.analysis.executor.runner import (
    UTC_ZONE,
    _parse_string_column,
    _window_bound_predicates,
)
from marivo.analysis.windows.spec import AbsoluteWindow


class _FakeMeta:
    def __init__(self, data_type, format, parse_kind="strptime"):
        self.data_type = data_type
        self.format = format
        self.parse_kind = parse_kind
        self.required_prefix = None
        self.granularity = None
        self.timezone = None


def test_ibis_emits_strptime_for_trino_dialect():
    """Sanity: ibis emits MySQL/strptime format for ``date_parse`` on Trino
    (not Joda), so no SQL-level rewriting is needed for the date-only case."""
    t = ibis.table([("log_date", "string")], name="t")
    expr = t.log_date.as_date("%Y%m%d")
    sql = ibis.to_sql(expr, dialect="trino")
    assert "%Y%m%d" in sql
    assert "yyyyMMdd" not in sql


def test_parse_string_column_translates_minute_for_trino():
    """%Y-%m-%d %H:%M:%S on Trino emits %i (minute), not %M (month name).

    Without dialect-gated translation, ibis leaves %M unchanged and Trino's
    date_parse reads it as month name, malforming at the minute position.
    ibis may collapse %H:%i:%s to %T; both correctly represent minutes.
    """
    t = ibis.table([("created_at", "string")], name="orders")
    meta = _FakeMeta("string", "%Y-%m-%d %H:%M:%S")
    expr = _parse_string_column(t.created_at, meta, dialect="trino")
    sql = ibis.to_sql(expr, dialect="trino")
    assert "%M" not in sql
    assert "%i" in sql or "%T" in sql


def test_parse_string_column_translates_minute_for_mysql():
    """MySQL STR_TO_DATE uses the same MySQL specifiers as Trino date_parse,
    so %M must translate to %i there too."""
    t = ibis.table([("created_at", "string")], name="orders")
    meta = _FakeMeta("string", "%Y-%m-%d %H:%M:%S")
    expr = _parse_string_column(t.created_at, meta, dialect="mysql")
    sql = ibis.to_sql(expr, dialect="mysql")
    assert "%M" not in sql
    assert "%i" in sql or "%T" in sql


def test_parse_string_column_leaves_format_unchanged_for_duckdb():
    """DuckDB uses Python strptime natively (%M = minute); no translation."""
    t = ibis.table([("created_at", "string")], name="orders")
    meta = _FakeMeta("string", "%Y-%m-%d %H:%M:%S")
    expr = _parse_string_column(t.created_at, meta, dialect="duckdb")
    sql = ibis.to_sql(expr, dialect="duckdb")
    assert "%M" in sql
    assert "%i" not in sql


def test_window_bound_predicates_translate_minute_for_trino():
    """End-to-end partition-pruning path emits a MySQL minute specifier on
    Trino for a minute strptime field. This is the path that would have
    caught the bug."""
    t = ibis.table([("log_ts", "string")], name="orders")
    meta = _FakeMeta("string", "%Y-%m-%d %H:%M:%S")
    lower, upper = _window_bound_predicates(
        t.log_ts,
        AbsoluteWindow(start="2024-10-11T03:00:00", end="2025-07-31T14:00:00"),
        meta,
        report_tz=UTC_ZONE,
        datasource_read_tz=UTC_ZONE,
        dialect="trino",
    )
    sql = ibis.to_sql(t.filter(lower, upper), dialect="trino")
    assert "%M" not in sql
    assert "%i" in sql or "%T" in sql
