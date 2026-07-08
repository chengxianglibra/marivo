"""Python strptime to MySQL/Trino format translation.

This module lives in ``marivo.datasource`` because format translation is an
engine concern (MySQL-family backends use MySQL format specifiers, not Python
strptime).
"""

from __future__ import annotations

# Python strptime directives that agree with MySQL ``date_parse`` specifiers
# are passed through unchanged (value maps to itself). Divergent directives
# are remapped to their MySQL equivalent so the authored Python format works
# on Trino/MySQL without per-backend authoring.
_PYTHON_TO_MYSQL_DIRECTIVES: dict[str, str] = {
    "Y": "Y",
    "m": "m",
    "d": "d",
    "H": "H",
    "y": "y",
    "j": "j",
    "b": "b",
    "a": "a",
    "p": "p",
    "f": "f",
    "e": "e",
    "w": "w",
    "U": "U",
    "M": "i",  # minute (Python) -> minute (MySQL); MySQL %M is month name
    "B": "M",  # full month name (Python) -> month name (MySQL)
    "A": "W",  # full weekday name (Python) -> weekday name (MySQL)
    "I": "h",  # 12-hour (Python) -> 12-hour (MySQL)
    "S": "s",  # second (Python) -> second (MySQL)
}

# Directives valid in Python strptime but divergent or absent in MySQL
# ``date_parse``. Rejecting these prevents silent malformation on Trino/MySQL
# (e.g. Python %W is week-number, MySQL %W is weekday name).
_PYTHON_DIRECTIVES_UNSAFE_FOR_MYSQL: frozenset[str] = frozenset(
    {"W", "u", "Z", "z", "c", "x", "X", "G", "g", "V", "h"}
)


def python_to_mysql_strptime(fmt: str) -> str:
    """Translate a Python strptime format to MySQL/Trino ``date_parse`` syntax.

    Trino ``date_parse`` and MySQL ``STR_TO_DATE`` accept MySQL format
    specifiers, which agree with Python strptime on most common tokens but
    disagree on several. The critical divergence is ``%M``: Python strptime
    reads it as *minute*, while MySQL reads it as *month name* (minute is
    ``%i``). Without translation, ``date_parse(col, '%Y-%m-%d %H:%M:%S')``
    malforms at the minute position. This function rewrites divergent
    directives so a single authored Python strptime format runs correctly on
    Trino/MySQL.

    Translation is single-pass and token-by-token: each ``%<dir>`` token maps
    independently to its MySQL equivalent, and ``%%`` is preserved as a literal
    percent. Output tokens are never re-translated.

    Args:
        fmt: A validated Python strptime format string (as produced by
            :func:`marivo.semantic.time_format.normalize_strptime`).

    Returns:
        The equivalent MySQL ``date_parse`` format string.

    Raises:
        ValueError: ``fmt`` contains a directive whose Python and MySQL
            meanings diverge (e.g. ``%W``, ``%u``, ``%Z``) or has no MySQL
            equivalent, ends with a stray ``%``, or contains an unknown
            directive. Divergent directives are rejected rather than passed
            through to avoid silent malformation.

    Example:
        >>> python_to_mysql_strptime("%Y-%m-%d %H:%M:%S")
        '%Y-%m-%d %H:%i:%s'
        >>> python_to_mysql_strptime("%Y%m%d")
        '%Y%m%d'
        >>> python_to_mysql_strptime("%M")
        '%i'
    """
    out: list[str] = []
    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            raise ValueError(f"strptime format {fmt!r} ends with a stray '%' directive prefix")
        directive = fmt[i + 1]
        if directive == "%":
            out.append("%%")
            i += 2
            continue
        if directive in _PYTHON_DIRECTIVES_UNSAFE_FOR_MYSQL:
            raise ValueError(
                f"strptime directive %{directive} in {fmt!r} cannot be "
                f"translated to MySQL/Trino date_parse: Python and MySQL "
                f"assign %{directive} different meanings. Restrict the time "
                f"dimension format to supported directives (year/month/day/"
                f"hour/minute/second), or use a native temporal column instead."
            )
        mapped = _PYTHON_TO_MYSQL_DIRECTIVES.get(directive)
        if mapped is None:
            raise ValueError(
                f"strptime directive %{directive} in {fmt!r} is not supported "
                f"for MySQL/Trino date_parse translation"
            )
        out.append("%")
        out.append(mapped)
        i += 2
    return "".join(out)
