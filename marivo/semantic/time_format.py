"""Canonical Python strptime validation for time field formats.

The ``date_format`` parameter of ``@ms.time_dimension`` accepts only Python
strptime strings. This module owns the single validation function used
by the decorator and the semantic validator. It performs NO format
translation; the format string flows ibis -> backend SQL unchanged.
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
        No format translation is performed. The validated string flows
        ibis -> backend SQL unchanged.

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
