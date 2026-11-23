"""Typed fields from the Session Store SQLite boundary."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from marivo._kernel.compat import UTC
from marivo.analysis.model.receipts import invalid


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _cell(row: sqlite3.Row, name: str) -> object:
    value: object = row[name]
    return value


def _text(row: sqlite3.Row, name: str) -> str:
    value = _cell(row, name)
    if type(value) is not str or not value:
        raise invalid(f"invalid {name} relation field")
    return value


def _optional(row: sqlite3.Row, name: str) -> str | None:
    value = _cell(row, name)
    if value is None:
        return None
    if type(value) is not str:
        raise invalid(f"invalid nullable {name} relation field")
    return value


def _rows(
    conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> tuple[sqlite3.Row, ...]:
    result: list[sqlite3.Row] = []
    for value in conn.execute(sql, parameters):
        if not isinstance(value, sqlite3.Row):
            raise invalid("invalid SQLite row decoder")
        result.append(value)
    return tuple(result)


def _one(
    conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> sqlite3.Row | None:
    rows = _rows(conn, sql, parameters)
    if len(rows) > 1:
        raise invalid("duplicate selected metadata")
    return None if not rows else rows[0]
