import re

import pytest

from marivo.datasource.strptime import python_to_mysql_strptime
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
