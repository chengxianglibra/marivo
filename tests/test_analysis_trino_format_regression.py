"""Regression: Marivo must not rewrite strptime format to Joda for Trino.

Trino and Presto ``date_parse`` use MySQL/strptime format specifiers
(``%Y %m %d %H %i %S``). Joda pattern format (``yyyyMMdd``) is only
accepted by the separate ``parse_datetime`` function.

Marivo previously had a regex patch ``_fix_trino_date_parse`` that
wrongly converted strptime to Joda, causing ``INVALID_FUNCTION_ARGUMENT``
on both Trino and Presto backends. ibis 12 emits the correct
MySQL/strptime format directly; no translation is needed.
"""

import ibis


def test_ibis_emits_strptime_for_trino_dialect():
    """Sanity: ibis emits MySQL/strptime format for ``date_parse`` on Trino."""
    t = ibis.table([("log_date", "string")], name="t")
    expr = t.log_date.as_date("%Y%m%d")
    sql = ibis.to_sql(expr, dialect="trino")
    assert "%Y%m%d" in sql
    assert "yyyyMMdd" not in sql
