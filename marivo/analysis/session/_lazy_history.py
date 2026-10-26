"""Bounded current-generation Session history without activation or recovery."""

from __future__ import annotations

import sqlite3

from marivo.analysis._pages import encode_keyset_cursor
from marivo.analysis.errors import AnalysisRepair, SessionNotFoundError
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.prior_generations import (
    discover_prior_generations,
    prior_generation_guidance,
)
from marivo.analysis.materialization.store import SessionStore, _one, _rows, _text
from marivo.analysis.session._lazy_read_model import (
    SessionSummary,
    SessionSummaryPage,
)
from marivo.analysis.session._lazy_runtime_reads import (
    aware_datetime,
    count,
)
from marivo.introspection.live.model import LiveHelpTarget


def not_found(
    layout: MaterializationLayout,
    session_id: str,
    *,
    location: str,
    candidates: tuple[str, ...] = (),
) -> SessionNotFoundError:
    message = f"The selected Session ID does not exist in this project's current v{layout.generation} Store."
    action = "Read recent() and use a returned current-generation Session ID."
    prior = discover_prior_generations(layout)
    if prior:
        message += (
            " This ID may belong to an older generation; its ownership has not been verified."
        )
        action += " " + prior_generation_guidance(prior)
    return SessionNotFoundError(
        message=message,
        expected="an exact current-generation Session ID from recent().items",
        received=session_id,
        location=location,
        repair=AnalysisRepair(
            kind="inspect",
            action=action,
            help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
            candidates=candidates,
        ),
    )


def _summary(store: SessionStore, conn: sqlite3.Connection, row: sqlite3.Row) -> SessionSummary:
    record = store._session(row)
    return SessionSummary(
        id=record.session_ref,
        name=record.name,
        question=record.question,
        created_at=aware_datetime(record.created_at),
        updated_at=aware_datetime(record.updated_at),
        artifact_count=count(
            conn,
            "SELECT count(*) FROM dataset_artifacts WHERE session_ref=?",
            (record.session_ref,),
        ),
    )


def recent(
    store: SessionStore, *, limit: int = 20, after: tuple[str, str] | None
) -> SessionSummaryPage:
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


def inspect(store: SessionStore, session_id: str) -> SessionSummary:
    with store._read() as conn:
        row = _one(conn, "SELECT * FROM sessions WHERE session_ref=?", (session_id,))
        if row is None:
            names = _rows(
                conn,
                "SELECT session_ref FROM sessions ORDER BY updated_at DESC,session_ref DESC LIMIT 10",
            )
            raise not_found(
                store.layout,
                session_id,
                location="session.inspect",
                candidates=tuple(_text(value, "session_ref") for value in names),
            )
        return _summary(store, conn, row)


__all__: list[str] = []
