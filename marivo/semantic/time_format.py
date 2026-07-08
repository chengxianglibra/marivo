"""Python strptime validation for time fields.

The ``date_format`` parameter of ``@ms.time_dimension`` accepts only Python
strptime strings, validated by :func:`normalize_strptime` at authoring time.
The validated format flows into ibis expressions and is emitted as backend SQL.

For MySQL-family backends (Trino ``date_parse``, MySQL ``STR_TO_DATE``),
several Python strptime directives disagree with MySQL specifiers — most
importantly ``%M`` (Python minute vs MySQL month name).
:func:`marivo.datasource.strptime.python_to_mysql_strptime` translates the
authored Python format to the MySQL form at SQL-emission time so a single
authored format works on every backend. DuckDB and other Python-strptime-native
backends receive the format unchanged.
"""

from __future__ import annotations

import time
from datetime import datetime

_SAMPLE_DT = datetime(2024, 1, 15, 10, 30, 45)


def normalize_strptime(value: str) -> str:
    """Strip whitespace and validate that ``value`` is a Python strptime format.

    Any syntactically valid Python strptime format is accepted. Validation is
    performed by round-tripping a known datetime through ``strftime`` then
    ``strptime``; this rejects unknown directives like ``%Q`` while accepting
    all valid formats regardless of granularity.

    Args:
        value: Candidate format string. Must be ``%``-prefixed and parseable
            by ``time.strptime``.

    Returns:
        The stripped format string.

    Raises:
        ValueError: ``value`` is not ``%``-prefixed or is not a syntactically
            valid Python strptime format.

    Constraints:
        This function performs no translation; it returns the canonical Python
        strptime form. Backend-specific translation (e.g. to MySQL for
        Trino/MySQL ``date_parse``/``STR_TO_DATE``) is applied downstream at
        SQL-emission time by :func:`marivo.datasource.strptime.python_to_mysql_strptime`, not here.

    Example:
        >>> normalize_strptime("%Y%m%d")
        '%Y%m%d'
        >>> normalize_strptime("  %Y-%m-%d  ")
        '%Y-%m-%d'
        >>> normalize_strptime("%Y-%m-%d %H:%M")
        '%Y-%m-%d %H:%M'
    """
    stripped = value.strip()
    if not stripped.startswith("%"):
        raise ValueError(
            f"date_format must be a Python strptime format starting with '%' "
            f"(e.g. '%Y%m%d'); got {value!r}. Shorthand aliases like "
            f"'yyyymmdd' are no longer accepted."
        )
    try:
        sample_str = _SAMPLE_DT.strftime(stripped)
    except ValueError:
        raise ValueError(f"date_format {value!r} is not a valid Python strftime format.") from None
    try:
        time.strptime(sample_str, stripped)
    except ValueError:
        raise ValueError(f"date_format {value!r} is not a valid Python strptime format.") from None
    return stripped
