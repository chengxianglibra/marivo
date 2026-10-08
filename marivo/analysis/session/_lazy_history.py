"""Bounded v3 Session history without activation or recovery."""

from __future__ import annotations

import sqlite3

from marivo.analysis._pages import encode_keyset_cursor
from marivo.analysis.errors import AnalysisRepair, SessionNotFoundError
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.store import SessionStore, _one, _rows, _text
from marivo.analysis.session._lazy_read_model import (
    SessionInspection,
    SessionSummary,
    SessionSummaryPage,
)
from marivo.analysis.session._lazy_runtime_reads import (
    aware_datetime,
    count,
    page_after,
    runs_in_snapshot,
)
from marivo.introspection.live.model import LiveHelpTarget


def _summary(store: SessionStore, conn: sqlite3.Connection, row: sqlite3.Row) -> SessionSummary:
    record = store._session(row)
    return SessionSummary(
        id=record.session_ref,
        name=record.name,
        question=record.question,
        created_at=aware_datetime(record.created_at),
        updated_at=aware_datetime(record.updated_at),
        run_count=count(
            conn,
            "SELECT count(*) FROM analysis_action_runs WHERE session_ref=?",
            (record.session_ref,),
        ),
        artifact_count=count(
            conn,
            "SELECT count(*) FROM dataset_artifacts WHERE session_ref=?",
            (record.session_ref,),
        ),
    )


def recent(
    store: SessionStore, *, limit: int = 20, cursor: str | None = None
) -> SessionSummaryPage:
    after = page_after(limit, cursor, operation="recent")
    with store._read() as conn:
        rows = _rows(
            conn,
            "SELECT session_ref,updated_at FROM sessions "
            + ("WHERE (updated_at,session_ref)<(?,?) " if after is not None else "")
            + "ORDER BY updated_at DESC,session_ref DESC LIMIT ?",
            (*(after or ()), limit + 1),
        )
        selected = rows[:limit]
        items = []
        for identity in selected:
            row = _one(
                conn,
                "SELECT * FROM sessions WHERE session_ref=?",
                (_text(identity, "session_ref"),),
            )
            if row is None:
                raise invalid("selected Session history row is absent from the same snapshot")
            items.append(_summary(store, conn, row))
        has_more = len(rows) > limit
        return SessionSummaryPage(
            items=tuple(items),
            limit=limit,
            has_more=has_more,
            next_cursor=encode_keyset_cursor(
                _text(selected[-1], "updated_at"), _text(selected[-1], "session_ref")
            )
            if has_more
            else None,
        )


def inspect(
    store: SessionStore, name: str, *, run_limit: int = 5, run_cursor: str | None = None
) -> SessionInspection:
    page_after(run_limit, run_cursor, operation="inspect")
    with store._read() as conn:
        row = _one(conn, "SELECT * FROM sessions WHERE name=?", (name,))
        if row is None:
            names = _rows(
                conn, "SELECT name FROM sessions ORDER BY updated_at DESC,session_ref DESC LIMIT 10"
            )
            raise SessionNotFoundError(
                message="The selected Session name does not exist in this project.",
                expected="one existing Session name",
                received=name,
                location="session.inspect",
                repair=AnalysisRepair(
                    kind="inspect",
                    action="Read recent() and inspect a returned Session name.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
                    candidates=tuple(_text(value, "name") for value in names),
                ),
            )
        summary = _summary(store, conn, row)
        return SessionInspection(
            summary=summary,
            runs=runs_in_snapshot(store, conn, summary.id, limit=run_limit, cursor=run_cursor),
        )


__all__: list[str] = []
