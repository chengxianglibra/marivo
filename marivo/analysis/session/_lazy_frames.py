"""Bounded committed Frame discovery within one Session metadata snapshot."""

from __future__ import annotations

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError, AnalysisRepair, SessionStateError
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import digest, invalid, parse_timestamp
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import _rows, _text
from marivo.analysis.session._lazy_read_model import (
    PersistedFrameSummary,
    PersistedFrameSummaryPage,
)
from marivo.introspection.live.model import LiveHelpTarget


def _selection_error(parameter: str, received: str) -> SessionStateError:
    return SessionStateError(
        message="The committed Frame directory selection is invalid.",
        expected="limit within 1..100 and the preceding cursor for this Store, Session and limit",
        received=received,
        location=f"session.persisted_frames.{parameter}",
        repair=AnalysisRepair(
            kind="retry",
            action="Start with persisted_frames(limit=20); continue with the same limit and returned next_cursor.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="session.persisted_frames"),
        ),
    )


def persisted_frames(
    local: LocalFrameServices, *, limit: int, cursor: str | None
) -> PersistedFrameSummaryPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _selection_error("limit", "limit must be an integer within 1..100, excluding bool")
    store, session = local.store, local.session_ref
    owner = digest((store.store_id, session, "session.persisted_frames", limit))
    after: tuple[str, str] | None = None
    if cursor is not None:
        if type(cursor) is not str or not cursor or len(cursor) > 16_384:
            raise _selection_error("cursor", "cursor must be bounded nonempty text")
        try:
            position, identity = decode_keyset_cursor(cursor)
            scope, separator, artifact_id = identity.partition(":")
            if (
                type(position) is not str
                or scope != owner
                or not separator
                or not artifact_id
                or encode_keyset_cursor(position, identity) != cursor
            ):
                raise ValueError("cursor scope or encoding differs")
            parse_timestamp(position)
        except (ValueError, RecursionError, AnalysisError):
            raise _selection_error(
                "cursor", "cursor is invalid or belongs to another selection"
            ) from None
        after = position, artifact_id
    with store._read() as conn:
        rows = _rows(
            conn,
            "SELECT artifact_ref,committed_at FROM dataset_artifacts WHERE session_ref=? "
            + ("AND (committed_at,artifact_ref)<(?,?) " if after is not None else "")
            + "ORDER BY committed_at DESC,artifact_ref DESC LIMIT ?",
            (session, *(after or ()), limit + 1),
        )
        selected = rows[:limit]
        items: list[PersistedFrameSummary] = []
        for row in selected:
            artifact_id = _text(row, "artifact_ref")
            manifest = terminal_store.manifest(store, conn, artifact_id)
            if manifest is None or manifest.session_id != session:
                raise invalid("selected committed Frame is absent or belongs to another Session")
            frame = AnalysisFrame._from_stored(
                local,
                manifest.data,
                artifact_id=artifact_id,
                metadata=manifest.metadata,
                method_facts=manifest.method_facts,
                description=manifest.description,
            )
            items.append(
                PersistedFrameSummary(
                    artifact_id=artifact_id,
                    committed_at=parse_timestamp(_text(row, "committed_at")),
                    description=manifest.description,
                    schema=frame.schema,
                )
            )
        has_more = len(rows) > limit
        return PersistedFrameSummaryPage(
            items=tuple(items),
            limit=limit,
            has_more=has_more,
            next_cursor=encode_keyset_cursor(
                _text(selected[-1], "committed_at"),
                owner + ":" + _text(selected[-1], "artifact_ref"),
            )
            if has_more
            else None,
        )
