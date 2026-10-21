"""Operation-scoped metadata reads within one SQLite snapshot."""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from datetime import datetime

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    SessionStateError,
)
from marivo.analysis.materialization.contracts import (
    invalid,
    parse_timestamp,
)
from marivo.analysis.materialization.store import _one
from marivo.introspection.live.model import LiveHelpTarget


def aware_datetime(value: str) -> datetime:
    return parse_timestamp(value)


def _selection_error(parameter: str, *, expected: str, received: str) -> SessionStateError:
    return SessionStateError(
        message="The selected Session history argument is outside its supported contract.",
        expected=expected,
        received=received,
        location=f"session.recent.{parameter}",
        repair=AnalysisRepair(
            kind="retry",
            action="Call recent(limit=20) without a cursor; continue with the returned next_cursor.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
        ),
    )


def page_after(limit: int, cursor: str | None) -> tuple[str, str] | None:
    if type(limit) is not int or not 1 <= limit <= 100:
        received = (
            "limit is not an exact integer"
            if type(limit) is not int
            else "limit is outside [1, 100]"
        )
        raise _selection_error(
            "limit", expected="one exact integer within [1, 100]", received=received
        )
    if cursor is None:
        return None
    expected = (
        "None for the first page or the exact next_cursor returned by the same read operation"
    )
    if type(cursor) is not str or not cursor or len(cursor) > 16_384:
        raise _selection_error(
            "cursor", expected=expected, received="cursor is not bounded non-empty text"
        )
    decoded: tuple[str | int, str] | None = None
    with suppress(ValueError, RecursionError):
        decoded = decode_keyset_cursor(cursor)
    if decoded is None:
        raise _selection_error(
            "cursor",
            expected=expected,
            received="cursor is not a supported keyset encoding",
        )
    timestamp, identity = decoded
    identity_bytes: int | None = None
    with suppress(UnicodeEncodeError):
        identity_bytes = len(identity.encode("utf-8"))
    if (
        type(timestamp) is not str
        or not identity
        or identity_bytes is None
        or identity_bytes > 4096
    ):
        raise _selection_error(
            "cursor",
            expected=expected,
            received="cursor does not contain a bounded timestamp and identity",
        )
    parsed: datetime | None = None
    with suppress(AnalysisError):
        parsed = aware_datetime(timestamp)
    if parsed is None or encode_keyset_cursor(timestamp, identity) != cursor:
        raise _selection_error(
            "cursor",
            expected=expected,
            received="cursor is not a canonical timezone-aware continuation",
        )
    return timestamp, identity


def count(conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...]) -> int:
    row = _one(conn, sql, parameters)
    if row is None:
        raise invalid("missing runtime aggregate")
    value: object = row[0]
    if type(value) is not int or value < 0:
        raise invalid("invalid runtime aggregate")
    return value


__all__: list[str] = []
