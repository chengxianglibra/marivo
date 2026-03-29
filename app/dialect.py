"""SQL dialect translation: DuckDB-dialect SQL → target engine dialect.

Supported targets: ``duckdb``, ``trino``.

Rewrite strategy:
1. ``_rewrite_cast`` — ``expr::TYPE`` → ``CAST(expr AS TYPE)`` (Trino)
"""

from __future__ import annotations

import re

_SUPPORTED_DIALECTS = {"duckdb", "trino"}


def translate(sql: str, target: str) -> str:
    """Translate DuckDB-dialect SQL to *target* engine dialect.

    Returns the SQL unchanged when *target* is ``'duckdb'``.
    """
    if target not in _SUPPORTED_DIALECTS:
        raise ValueError(f"Unsupported dialect: {target!r}")
    if target == "duckdb":
        return sql

    sql = _rewrite_cast(sql)
    return sql


# ---------------------------------------------------------------------------
# Internal rewriters
# ---------------------------------------------------------------------------


def _rewrite_cast(sql: str) -> str:
    r"""Replace DuckDB shorthand casts ``expr::TYPE`` with ``CAST(expr AS TYPE)``.

    Handles simple identifiers, function calls, and parenthesised sub-expressions
    immediately before ``::TYPE``.

    The regex works right-to-left so nested casts are handled naturally.
    """

    def _replace(m: re.Match) -> str:
        return f"CAST({m.group(1)} AS {m.group(2)})"

    prev = None
    while prev != sql:
        prev = sql
        # Case 1: paren-group before ::TYPE
        sql = _rewrite_cast_paren(sql)
        # Case 2: simple token before ::TYPE
        sql = re.sub(
            r"(\b[A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)",
            _replace,
            sql,
        )
    return sql


def _rewrite_cast_paren(sql: str) -> str:
    """Handle ``…)::TYPE`` by finding the matching open paren."""
    pattern = re.compile(r"\)(\s*)::(\s*)([A-Za-z_][A-Za-z0-9_]*)")
    m = pattern.search(sql)
    if m is None:
        return sql

    close_pos = m.start()  # position of ')'
    type_name = m.group(3)

    # Walk backwards to find the matching '('
    depth = 1
    i = close_pos - 1
    while i >= 0 and depth > 0:
        if sql[i] == ")":
            depth += 1
        elif sql[i] == "(":
            depth -= 1
        i -= 1
    open_pos = i + 1  # position of matching '('

    # Walk further back to capture the function/expression name before '('
    func_start = open_pos
    j = open_pos - 1
    # skip whitespace
    while j >= 0 and sql[j] in (" ", "\t", "\n", "\r"):
        j -= 1
    # If previous char is alphanumeric/underscore, it's a function name or keyword
    if j >= 0 and (sql[j].isalnum() or sql[j] == "_"):
        while j >= 0 and (sql[j].isalnum() or sql[j] == "_"):
            j -= 1
        func_start = j + 1

    expr = sql[func_start : m.start() + 1]  # includes the closing ')'
    replacement = f"CAST({expr} AS {type_name})"
    sql = sql[:func_start] + replacement + sql[m.end() :]
    return sql
