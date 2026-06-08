"""Canonical Python strptime validation for time field formats.

The ``date_format`` parameter of ``@ms.time_field`` accepts only Python
strptime strings. This module owns the single validation function used
by the decorator and the semantic validator. It performs NO format
translation; the format string flows ibis -> backend SQL unchanged.
"""

from __future__ import annotations

import time


def normalize_strptime(value: str) -> str:
    """Strip whitespace and validate that ``value`` is a Python strptime format.

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
    """
    stripped = value.strip()
    if not stripped.startswith("%"):
        raise ValueError(
            f"date_format must be a Python strptime format starting with '%' "
            f"(e.g. '%Y%m%d'); got {value!r}. Shorthand aliases like "
            f"'yyyymmdd' are no longer accepted."
        )
    # Probe with canonical date/datetime strings covering the supported
    # formats (compact, dashed, slashed, with/without time). A format is
    # accepted if at least one probe parses cleanly with no trailing data.
    # This rejects incomplete formats like '%Y%m' (which leaves '01'
    # unconsumed against '20240101') and unknown directives like '%Q'.
    probes = (
        "20240101",  # %Y%m%d
        "2024-01-01",  # %Y-%m-%d
        "2024/01/01",  # %Y/%m/%d
        "2024010100",  # %Y%m%d%H
        "2024-01-01-00",  # %Y-%m-%d-%H
        "20240101-00",  # %Y%m%d-%H
        "20240101T00",  # %Y%m%dT%H
        "2024-01-01 00:00:00",  # %Y-%m-%d %H:%M:%S
    )
    for probe in probes:
        try:
            time.strptime(probe, stripped)
        except ValueError:
            continue
        else:
            return stripped
    raise ValueError(
        f"date_format {value!r} is not a valid Python strptime format or is not "
        f"parseable as a complete date/time (supported examples: "
        f"'%Y%m%d', '%Y-%m-%d', '%Y%m%d%H', '%Y-%m-%d %H:%M:%S')."
    )
