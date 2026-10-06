"""Datasource-owned strptime translation and dialect compilation regression."""

import ibis
import pytest

from marivo.datasource.engines import profile_for_backend_name


def test_ibis_emits_strptime_for_trino_dialect():
    """Sanity: ibis emits MySQL/strptime format for ``date_parse`` on Trino
    (not Joda), so no SQL-level rewriting is needed for the date-only case."""
    t = ibis.table([("log_date", "string")], name="t")
    expr = t.log_date.as_date("%Y%m%d")
    sql = ibis.to_sql(expr, dialect="trino")
    assert "%Y%m%d" in sql
    assert "yyyyMMdd" not in sql


@pytest.mark.parametrize(
    "backend,dialect", [("trino", "trino"), ("presto", "trino"), ("mysql", "mysql")]
)
def test_profile_translates_minutes_for_native_dialect(backend, dialect):
    table = ibis.table({"created_at": "string"}, name="orders")
    fmt = profile_for_backend_name(backend).translate_strptime_format("%Y-%m-%d %H:%M:%S")
    assert "%M" not in fmt
    assert "%i" in fmt
    sql = ibis.to_sql(table.created_at.as_timestamp(fmt), dialect=dialect)
    assert "%M" not in sql
    assert "%i" in sql or "%T" in sql


def test_duckdb_profile_preserves_python_minutes():
    assert profile_for_backend_name("duckdb").translate_strptime_format("%M") == "%M"


def test_sqlite_profile_keeps_the_python_format_for_its_own_scalar():
    """SQLite's parser is a connection-local ``datetime.strptime`` scalar, so
    the authored Python format is already what it consumes."""
    assert profile_for_backend_name("sqlite").translate_strptime_format("%M") == "%M"


def test_clickhouse_profile_translates_to_mysql_specifiers():
    """ClickHouse ``parseDateTime`` reads MySQL-family specifiers, and Python
    ``%M`` is minute there while MySQL ``%M`` is a month name."""
    assert (
        profile_for_backend_name("clickhouse").translate_strptime_format("%Y-%m-%d %H:%M:%S")
        == "%Y-%m-%d %H:%i:%s"
    )
