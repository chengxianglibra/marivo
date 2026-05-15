"""Extract per-row value expressions from aggregate SQL definitions.

For inferential analysis (Welch's t-test), we need the per-row value
expression (e.g. ``revenue`` from ``SUM(revenue)``) to compute
AVG / STDDEV_SAMP / etc.  This module provides a helper that strips
the outer aggregate function from a metric's definition_sql.
"""

from __future__ import annotations

import re

_SUM_PREFIX = re.compile(r"^\s*SUM\s*\(", re.IGNORECASE)


def extract_value_expression(definition_sql: str, aggregation_semantics: str) -> str | None:
    """Extract the per-row value expression from an aggregate SQL expression.

    Returns the inner expression for ``aggregation_semantics="sum"`` when
    *definition_sql* matches ``SUM(expr)``.  Returns ``None`` for
    non-sum semantics or expressions that cannot be decomposed.

    Handles nested parentheses (e.g. ``SUM(CASE WHEN ... END)``) by
    balanced-parenthesis matching.  Rejects expressions that contain
    additional content after the closing ``)`` (e.g. ``SUM(a) + SUM(b)``).
    """
    if aggregation_semantics != "sum":
        return None
    m = _SUM_PREFIX.match(definition_sql)
    if not m:
        return None
    # Find the matching closing paren by counting depth.
    rest = definition_sql[m.end() :]
    depth = 1
    i = 0
    while i < len(rest) and depth > 0:
        ch = rest[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    # i now points past the closing paren of SUM(...)
    inner = rest[: i - 1].strip()
    # There must be nothing but whitespace after the closing paren
    tail = rest[i:].strip()
    if tail:
        return None
    return inner if inner else None
