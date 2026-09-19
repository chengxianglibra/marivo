import re

import pytest

from marivo.datasource.strptime import (
    python_to_mysql_strptime,
    python_to_postgres_strptime,
)
from marivo.semantic.time_format import normalize_strptime


def test_normalize_strips_whitespace():
    assert normalize_strptime("  %Y%m%d  ") == "%Y%m%d"


def test_normalize_returns_valid_strptime_unchanged():
    assert normalize_strptime("%Y%m%d") == "%Y%m%d"
    assert normalize_strptime("%Y-%m-%d") == "%Y-%m-%d"
    assert normalize_strptime("%Y/%m/%d") == "%Y/%m/%d"
    assert normalize_strptime("%Y%m%d%H") == "%Y%m%d%H"
    assert normalize_strptime("%Y-%m-%d-%H") == "%Y-%m-%d-%H"
    assert normalize_strptime("%Y%m%d-%H") == "%Y%m%d-%H"
    assert normalize_strptime("%Y%m%dT%H") == "%Y%m%dT%H"
    assert normalize_strptime("%Y%m%d%H%M") == "%Y%m%d%H%M"
    assert normalize_strptime("%Y-%m-%d %H:%M") == "%Y-%m-%d %H:%M"
    assert normalize_strptime("%Y-%m-%d %H:%M:%S") == "%Y-%m-%d %H:%M:%S"
    assert normalize_strptime("%Y%m") == "%Y%m"


def test_normalize_rejects_non_percent_prefixed_input():
    """Shorthand aliases like 'yyyymmdd' are no longer accepted."""
    with pytest.raises(ValueError, match="%"):
        normalize_strptime("yyyymmdd")
    with pytest.raises(ValueError, match="%"):
        normalize_strptime("hh")
    with pytest.raises(ValueError, match="%"):
        normalize_strptime("int")


def test_normalize_rejects_invalid_strptime_syntax():
    with pytest.raises(ValueError):
        normalize_strptime("%Q")  # Not a real strptime directive


# --- python_to_mysql_strptime ---
#
# Trino/Presto ``date_parse`` uses MySQL format specifiers, which disagree with
# Python strptime on several tokens (notably ``%M``: Python = minute, MySQL =
# month name). ``python_to_mysql_strptime`` translates the author's Python
# strptime format into the MySQL form so a single authored format works on
# every backend.


def test_p2m_translates_minute_directive():
    """%M (Python minute) -> %i (MySQL minute). This is the core fix: without
    translation, Trino ``date_parse`` reads %M as month name and malforms."""
    assert python_to_mysql_strptime("%M") == "%i"


def test_p2m_translates_full_month_name():
    assert python_to_mysql_strptime("%B") == "%M"


def test_p2m_translates_full_weekday_name():
    assert python_to_mysql_strptime("%A") == "%W"


def test_p2m_translates_12_hour():
    assert python_to_mysql_strptime("%I") == "%h"


def test_p2m_translates_second():
    assert python_to_mysql_strptime("%S") == "%s"


def test_p2m_passes_through_agreeing_directives():
    """Directives that mean the same in Python strptime and MySQL date_parse
    flow through unchanged."""
    for tok in ["%Y", "%m", "%d", "%H", "%y", "%j", "%b", "%a", "%p", "%f", "%e", "%w", "%U"]:
        assert python_to_mysql_strptime(tok) == tok


def test_p2m_passes_date_only_formats_unchanged():
    assert python_to_mysql_strptime("%Y%m%d") == "%Y%m%d"
    assert python_to_mysql_strptime("%Y-%m-%d") == "%Y-%m-%d"


def test_p2m_translates_full_timestamp_format():
    """The canonical timestamp format: minute (%M->%i) and second (%S->%s)
    translate; date/hour tokens pass through."""
    assert python_to_mysql_strptime("%Y-%m-%d %H:%M:%S") == "%Y-%m-%d %H:%i:%s"


def test_p2m_preserves_literal_percent():
    """%% is a literal percent, not a directive, and is preserved."""
    assert python_to_mysql_strptime("100%%") == "100%%"


def test_p2m_rejects_divergent_directives():
    """Directives whose meaning differs between Python strptime and MySQL
    date_parse must raise rather than silently malform on Trino/Presto."""
    for tok in ["%W", "%u", "%Z", "%z", "%c", "%x", "%X", "%G", "%g", "%V", "%h"]:
        with pytest.raises(ValueError, match=re.escape(tok)):
            python_to_mysql_strptime(tok)


def test_p2m_rejects_unknown_directive():
    with pytest.raises(ValueError):
        python_to_mysql_strptime("%Q")


def test_p2m_rejects_stray_percent():
    with pytest.raises(ValueError, match="stray"):
        python_to_mysql_strptime("%Y-%")


# --- python_to_postgres_strptime ---
#
# PostgreSQL ``TO_TIMESTAMP`` uses template patterns, which agree with Python
# strptime on the calendar fields but not the sub-day ones (Python ``%M`` is
# minute, PostgreSQL ``MM`` is month; minute is ``MI``). The expectations below
# are written from the PostgreSQL template table in the documentation, not from
# the production directive map.


def test_p2p_translates_temporal_directives():
    assert python_to_postgres_strptime("%Y") == "YYYY"
    assert python_to_postgres_strptime("%y") == "YY"
    assert python_to_postgres_strptime("%m") == "MM"
    assert python_to_postgres_strptime("%d") == "DD"
    assert python_to_postgres_strptime("%H") == "HH24"
    assert python_to_postgres_strptime("%I") == "HH12"
    assert python_to_postgres_strptime("%M") == "MI"
    assert python_to_postgres_strptime("%S") == "SS"
    assert python_to_postgres_strptime("%f") == "US"
    assert python_to_postgres_strptime("%j") == "DDD"
    assert python_to_postgres_strptime("%p") == "AM"


def test_p2p_translates_names():
    assert python_to_postgres_strptime("%b") == "Mon"
    assert python_to_postgres_strptime("%B") == "Month"
    assert python_to_postgres_strptime("%a") == "Dy"
    assert python_to_postgres_strptime("%A") == "Day"


def test_p2p_translates_full_timestamp_format():
    assert python_to_postgres_strptime("%Y-%m-%d %H:%M:%S") == "YYYY-MM-DD HH24:MI:SS"
    assert python_to_postgres_strptime("%Y%m%d%H%M%S") == "YYYYMMDDHH24MISS"
    assert python_to_postgres_strptime("%Y-%m-%dT%H:%M:%S") == "YYYY-MM-DDTHH24:MI:SS"


def test_p2p_preserves_literal_percent():
    """``%%`` is a literal percent, not a template pattern."""
    assert python_to_postgres_strptime("100%%") == "100%"


def test_p2p_rejects_directives_without_an_exact_pattern():
    """Every rejected directive either diverges from its PostgreSQL pattern or
    has none, so passing it through would silently change the parsed value."""
    for tok in [
        "%W",
        "%u",
        "%U",
        "%w",
        "%e",
        "%Z",
        "%z",
        "%c",
        "%x",
        "%X",
        "%G",
        "%g",
        "%V",
        "%h",
    ]:
        with pytest.raises(ValueError, match=re.escape(tok)):
            python_to_postgres_strptime(tok)


def test_p2p_rejects_unknown_directive_and_stray_percent():
    with pytest.raises(ValueError):
        python_to_postgres_strptime("%Q")
    with pytest.raises(ValueError, match="stray"):
        python_to_postgres_strptime("%Y-%")
