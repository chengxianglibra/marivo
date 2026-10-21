"""Datasource-owned strptime translation and dialect compilation regression."""

import ibis


def test_ibis_emits_strptime_for_trino_dialect():
    """Sanity: ibis emits MySQL/strptime format for ``date_parse`` on Trino
    (not Joda), so no SQL-level rewriting is needed for the date-only case."""
    t = ibis.table([("log_date", "string")], name="t")
    expr = t.log_date.as_date("%Y%m%d")
    sql = ibis.to_sql(expr, dialect="trino")
    assert "%Y%m%d" in sql
    assert "yyyyMMdd" not in sql
