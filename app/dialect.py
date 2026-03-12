"""SQL dialect translation: DuckDB-dialect SQL → target engine dialect.

Supported targets: ``duckdb``, ``trino``, ``spark``.

Rewrite strategy (applied in order):
1. ``_rewrite_filter`` — ``AGG(expr) FILTER (WHERE cond)`` → ``AGG(CASE WHEN …)`` (Spark only)
2. ``_rewrite_cast``   — ``expr::TYPE`` → ``CAST(expr AS TYPE)`` (Trino, Spark)
3. ``_rewrite_schema_ddl`` — ``CREATE SCHEMA`` → ``CREATE DATABASE`` (Spark only)
"""

from __future__ import annotations

import re

_SUPPORTED_DIALECTS = {"duckdb", "trino", "spark"}


def translate(sql: str, target: str) -> str:
    """Translate DuckDB-dialect SQL to *target* engine dialect.

    Returns the SQL unchanged when *target* is ``'duckdb'``.
    """
    if target not in _SUPPORTED_DIALECTS:
        raise ValueError(f"Unsupported dialect: {target!r}")
    if target == "duckdb":
        return sql

    # Order matters: FILTER may be followed by ::TYPE, so rewrite FILTER first.
    if target == "spark":
        sql = _rewrite_filter(sql)
    sql = _rewrite_cast(sql)
    if target == "spark":
        sql = _rewrite_schema_ddl(sql)
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
    # Match: <token>::<TYPE>
    # <token> is one of:
    #   1. a closing paren (we need to find the matching open paren)
    #   2. an identifier/number sequence (e.g. col_name, 123)
    # <TYPE> is one or more word characters (e.g. DOUBLE, INTEGER, VARCHAR)

    def _replace(m: re.Match) -> str:
        return f"CAST({m.group(1)} AS {m.group(2)})"

    # Strategy: iteratively replace the *rightmost* ``expr::TYPE`` first so that
    # chained casts like ``x::DOUBLE::VARCHAR`` resolve inside-out.
    # We loop because a single regex pass may miss nested cases.
    prev = None
    while prev != sql:
        prev = sql
        # Case 1: paren-group before ::TYPE — e.g. ``SUM(x) FILTER(...)::DOUBLE``
        # We locate ``)<whitespace-maybe>::TYPE`` then walk backwards to find the
        # matching open-paren (handling nesting).
        sql = _rewrite_cast_paren(sql)
        # Case 2: simple token before ::TYPE — e.g. ``col::DOUBLE``, ``42::INT``
        sql = re.sub(
            r'(\b[A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)',
            _replace,
            sql,
        )
    return sql


def _rewrite_cast_paren(sql: str) -> str:
    """Handle ``…)::TYPE`` by finding the matching open paren."""
    pattern = re.compile(r'\)(\s*)::(\s*)([A-Za-z_][A-Za-z0-9_]*)')
    m = pattern.search(sql)
    if m is None:
        return sql

    close_pos = m.start()  # position of ')'
    type_name = m.group(3)

    # Walk backwards to find the matching '('
    depth = 1
    i = close_pos - 1
    while i >= 0 and depth > 0:
        if sql[i] == ')':
            depth += 1
        elif sql[i] == '(':
            depth -= 1
        i -= 1
    open_pos = i + 1  # position of matching '('

    # Now walk further back to capture the function/expression name before '('
    # e.g. in ``SUM(clicks) FILTER (WHERE ...)::DOUBLE`` we want everything from SUM
    # But we also need to handle bare parens like ``(a + b)::DOUBLE``
    func_start = open_pos
    j = open_pos - 1
    # skip whitespace
    while j >= 0 and sql[j] in (' ', '\t', '\n', '\r'):
        j -= 1
    # If previous char is alphanumeric/underscore, it's a function name or keyword
    if j >= 0 and (sql[j].isalnum() or sql[j] == '_'):
        while j >= 0 and (sql[j].isalnum() or sql[j] == '_'):
            j -= 1
        func_start = j + 1

    expr = sql[func_start:m.start() + 1]  # includes the closing ')'
    replacement = f"CAST({expr} AS {type_name})"
    sql = sql[:func_start] + replacement + sql[m.end():]
    return sql


def _rewrite_filter(sql: str) -> str:
    r"""Rewrite ``AGG(expr) FILTER (WHERE cond)`` → ``AGG(CASE WHEN cond THEN expr END)``.

    Handles:
    - Single-arg aggregates: ``AVG(x) FILTER (WHERE y)``
    - ``COUNT(*) FILTER (WHERE cond)`` → ``COUNT(CASE WHEN cond THEN 1 END)``
    - Multi-arg: ``ROUND(AVG(x) FILTER (WHERE y), 2)`` — the FILTER sits inside
      the outer function, so we match the inner aggregate.
    """
    # We use an iterative approach: find the first FILTER (WHERE ...) occurrence,
    # rewrite it, then repeat until none remain.
    while True:
        # Find ``FILTER`` followed by ``(WHERE`` (case-insensitive)
        match = re.search(r'\)\s*FILTER\s*\(\s*WHERE\s+', sql, re.IGNORECASE)
        if match is None:
            break

        # The ')' before FILTER is the end of the aggregate's argument list
        agg_close = match.start()  # position of ')'

        # Find the matching '(' for the FILTER clause
        filter_open = sql.index('(', agg_close + 1)  # the '(' in FILTER (WHERE ...)
        # Now find the matching ')' for this FILTER open paren
        filter_close = _find_matching_close(sql, filter_open)

        # Extract the WHERE condition (everything between WHERE and the closing paren)
        where_start = re.search(r'\(\s*WHERE\s+', sql[filter_open:], re.IGNORECASE)
        cond_start = filter_open + where_start.end()
        condition = sql[cond_start:filter_close]

        # Now find the aggregate function: walk back from agg_close to find matching '('
        agg_open = _find_matching_open(sql, agg_close)

        # Walk further back to find the aggregate function name
        j = agg_open - 1
        while j >= 0 and sql[j] in (' ', '\t', '\n', '\r'):
            j -= 1
        func_end = j + 1
        while j >= 0 and (sql[j].isalnum() or sql[j] == '_'):
            j -= 1
        func_start = j + 1
        func_name = sql[func_start:func_end]

        # Extract the aggregate argument(s)
        agg_args = sql[agg_open + 1:agg_close]

        # Build the replacement
        if agg_args.strip() == '*':
            # COUNT(*) FILTER (WHERE cond) → COUNT(CASE WHEN cond THEN 1 END)
            replacement = f"{func_name}(CASE WHEN {condition} THEN 1 END)"
        else:
            replacement = f"{func_name}(CASE WHEN {condition} THEN {agg_args} END)"

        sql = sql[:func_start] + replacement + sql[filter_close + 1:]

    return sql


def _rewrite_schema_ddl(sql: str) -> str:
    """``CREATE SCHEMA …`` → ``CREATE DATABASE …`` for Spark."""
    return re.sub(
        r'\bCREATE\s+SCHEMA\b',
        'CREATE DATABASE',
        sql,
        flags=re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_matching_close(sql: str, open_pos: int) -> int:
    """Find the matching ')' for the '(' at *open_pos*."""
    depth = 1
    i = open_pos + 1
    while i < len(sql) and depth > 0:
        if sql[i] == '(':
            depth += 1
        elif sql[i] == ')':
            depth -= 1
        i += 1
    return i - 1


def _find_matching_open(sql: str, close_pos: int) -> int:
    """Find the matching '(' for the ')' at *close_pos*."""
    depth = 1
    i = close_pos - 1
    while i >= 0 and depth > 0:
        if sql[i] == ')':
            depth += 1
        elif sql[i] == '(':
            depth -= 1
        i -= 1
    return i + 1
