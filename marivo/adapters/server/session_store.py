from __future__ import annotations

import json
import logging
from typing import Any

from marivo.adapters.metadata import MetadataStore
from marivo.contracts.ids import SessionId, UserId
from marivo.contracts.session import SessionEvent, SessionState
from marivo.core.session.rebuild import rebuild_session_state

logger = logging.getLogger(__name__)

_UNIQ_RETRIES = 3

# Event-sourced -> API status mapping
_API_STATUS_MAP: dict[str, str] = {
    "active": "open",
    "terminated": "closed",
}


def _api_status(event_sourced_status: str) -> str:
    """Map an event-sourced session status to the API-facing status.

    The event-sourced domain uses "active" / "terminated"; the API uses
    "open" / "closed".
    """
    return _API_STATUS_MAP.get(event_sourced_status, event_sourced_status)


def _decode_page_token(page_token: str | None) -> int:
    if page_token is None:
        return 0
    try:
        offset = int(page_token)
    except ValueError as error:
        raise ValueError("Invalid page_token. Expected a non-negative integer offset.") from error
    if offset < 0:
        raise ValueError("Invalid page_token. Expected a non-negative integer offset.")
    return offset


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return 25
    if limit <= 0:
        raise ValueError("Invalid limit. Expected a positive integer.")
    return min(limit, 100)


def _session_state_to_dict(state: SessionState) -> dict[str, Any]:
    """Convert a SessionState to the API response dict shape."""
    return {
        "session_id": str(state.session_id),
        "goal": {"question": state.goal},
        "scope": {
            "constraints": state.constraints,
        },
        "owner_user": str(state.owner_user) if state.owner_user else None,
        "lifecycle": {
            "status": _api_status(state.status),
            "terminal_reason": state.terminal_reason,
            "ended_at": state.ended_at,
        },
        "state_summary": {
            "state_view_ref": {
                "session_id": str(state.session_id),
                "view_type": "session_state_view",
            },
        },
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "schema_version": "analysis_session.v1",
    }


def _session_from_row(row: dict[str, Any]) -> dict[str, Any]:
    session_id: str = row["session_id"]
    return {
        "session_id": session_id,
        "goal": {"question": row["goal"]},
        "scope": {
            "constraints": json.loads(row["constraints_json"])
            if row.get("constraints_json")
            else None,
        },
        "owner_user": row.get("owner_user"),
        "lifecycle": {
            "status": row["status"],
            "terminal_reason": row.get("terminal_reason"),
            "ended_at": row.get("ended_at"),
            "rollover_from_session_id": row.get("rollover_from_session_id"),
        },
        "state_summary": {
            "state_view_ref": {
                "session_id": session_id,
                "view_type": "session_state_view",
            },
        },
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at") or row["created_at"],
        "schema_version": "analysis_session.v1",
    }


class SqlSessionStoreAdapter:
    """Wraps ``MetadataStore`` -> ``SessionStore``.

    Implements the ``SessionStore`` port interface using direct
    ``MetadataStore`` SQL queries (absorbs former SessionManager logic):

    * ``append_event`` translates session_created / session_terminated
      events into CRUD INSERT/UPDATE calls.
    * ``load_events`` synthesizes events from the sessions table row.
    * ``list_sessions`` queries sessions filtered by owner.
    * ``list_sessions_paginated`` returns filtered, paginated sessions.
    * ``get_proposition_runtime_status`` derives proposition stage from
      committed canonical DB state.
    """

    def __init__(
        self,
        metadata: MetadataStore,
    ) -> None:
        self._metadata = metadata

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        """Append an event to the session event log.

        Translates ``session_created`` and ``session_terminated`` events
        into CRUD operations on the sessions table.
        """
        if event.event_type == "session_created":
            goal = event.payload.get("goal", "")
            constraints = event.payload.get("constraints")
            budget = event.payload.get("budget")
            raw_filter = event.payload.get("raw_filter")
            owner_user = str(event.actor) if event.actor else ""
            self._metadata.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    goal,
                    constraints_json,
                    budget_json,
                    owner_user,
                    status,
                    raw_filter
                )
                VALUES (?, ?, ?, ?, ?, 'open', ?)
                """,
                [
                    str(session_id),
                    goal,
                    json.dumps(constraints or {}, default=str, sort_keys=True),
                    json.dumps(budget or {}, default=str, sort_keys=True),
                    owner_user,
                    raw_filter,
                ],
            )
            return

        if event.event_type == "session_terminated":
            terminal_reason = event.payload.get("terminal_reason", "user_closed")
            self._metadata.execute(
                f"""
                UPDATE sessions
                SET status = 'closed',
                    terminal_reason = ?,
                    ended_at = {self._metadata.dialect.now_sql()},
                    updated_at = {self._metadata.dialect.now_sql()}
                WHERE session_id = ?
                """,
                [terminal_reason, str(session_id)],
            )
            return

        # Other event types are silently ignored in the CRUD bridge
        # because the sessions table only tracks created/terminated state.
        logger.debug(
            "SqlSessionStoreAdapter.append_event: ignoring event_type=%s "
            "for session_id=%s (CRUD bridge only handles session_created/"
            "session_terminated)",
            event.event_type,
            session_id,
        )

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        """Load events from the session event log.

        Synthesizes events from the sessions table row.  Returns a
        ``session_created`` event for every session, and a
        ``session_terminated`` event if the session is closed.
        Raises NotFoundError when the session does not exist.
        """
        from marivo.contracts.errors import ErrorCode, NotFoundError

        row = self._metadata.query_one(
            "SELECT * FROM sessions WHERE session_id = ?",
            [str(session_id)],
        )
        if row is None:
            raise NotFoundError(
                code=ErrorCode.NOT_FOUND,
                message=f"Session {session_id!r} not found",
            )

        events: list[SessionEvent] = []
        created_payload: dict[str, Any] = {"goal": row["goal"]}
        constraints_json = row.get("constraints_json")
        if constraints_json:
            created_payload["constraints"] = json.loads(constraints_json)
        budget_json = row.get("budget_json")
        if budget_json:
            created_payload["budget"] = json.loads(budget_json)

        owner_user = row.get("owner_user") or ""
        actor = UserId(owner_user) if owner_user else None

        events.append(
            SessionEvent(
                session_id=session_id,
                event_type="session_created",
                timestamp=row["created_at"],
                payload=created_payload,
                actor=actor,
            )
        )

        if row["status"] in ("closed", "terminated"):
            terminal_reason = row.get("terminal_reason", "user_closed")
            ended_at = row.get("ended_at") or row.get("updated_at") or row["created_at"]
            events.append(
                SessionEvent(
                    session_id=session_id,
                    event_type="session_terminated",
                    timestamp=ended_at,
                    payload={"terminal_reason": terminal_reason},
                    actor=None,
                )
            )

        return events

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        """List sessions owned by ``owner``.

        Queries the sessions table filtered by owner_user.
        """
        rows = self._metadata.query_rows(
            "SELECT * FROM sessions WHERE owner_user = ? ORDER BY created_at DESC",
            [str(owner)],
        )
        result: list[SessionState] = []
        for row in rows:
            sid = SessionId(row["session_id"])
            status = "active" if row["status"] == "open" else row["status"]
            if status == "closed":
                status = "terminated"
            constraints_json = row.get("constraints_json")
            budget_json = row.get("budget_json")
            constraints = json.loads(constraints_json) if constraints_json else None
            budget = json.loads(budget_json) if budget_json else None
            owner_user_val = row.get("owner_user") or None
            result.append(
                SessionState(
                    session_id=sid,
                    status=status,
                    goal=row["goal"],
                    owner_user=UserId(owner_user_val) if owner_user_val else None,
                    constraints=constraints,
                    budget=budget,
                    created_at=row["created_at"],
                    updated_at=row.get("updated_at") or row["created_at"],
                )
            )
        return result

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status.

        Derives stage from committed canonical DB state.  v1 does not maintain
        a real queue / claim / lease / retry system.
        """
        from marivo.adapters.server.evidence_repositories import (
            ActionProposalRepository,
            AssessmentRepository,
        )

        row = self._metadata.query_one(
            """
            SELECT proposition_id, session_id, externally_visible_assessment_id
            FROM propositions
            WHERE proposition_id = ? AND session_id = ?
            """,
            [proposition_id, session_id],
        )
        if row is None:
            raise KeyError(f"proposition {proposition_id!r} not found in session {session_id!r}")

        ev_assessment_id: str | None = row.get("externally_visible_assessment_id")

        assessment_repo = AssessmentRepository(self._metadata)
        latest = assessment_repo.get_latest(proposition_id)

        proposals: list[dict[str, Any]] = []
        if latest is not None:
            proposal_repo = ActionProposalRepository(self._metadata)
            proposals = proposal_repo.list_by_assessment(session_id, latest["assessment_id"])

        if ev_assessment_id:
            current_stage = "externally_visible"
            last_successful_stage: str | None = "publish"
        elif latest is not None and proposals:
            current_stage = "publish_ready"
            last_successful_stage = "proposal_refresh"
        elif latest is not None:
            current_stage = "assessment_committed"
            last_successful_stage = "assessment_committed"
        else:
            current_stage = "queued"
            last_successful_stage = None

        return {
            "session_id": session_id,
            "proposition_id": proposition_id,
            "current_stage": current_stage,
            "last_successful_stage": last_successful_stage,
            "current_assessment_id": latest["assessment_id"] if latest is not None else None,
            "current_attempt": None,
            "backlog_state": "none",
            "last_failure_reason": "none",
            "last_failure_at": None,
            "schema_version": "proposition_runtime_status.v1",
        }

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        """Return a paginated list of sessions (server-mode only).

        Supports filtering by status, session_id prefix, and owner_user.
        """
        from marivo.identity import resolve_user

        status = kwargs.get("status")
        session_id = kwargs.get("session_id")
        limit = kwargs.get("limit")
        page_token = kwargs.get("page_token")

        offset = _decode_page_token(page_token)
        normalized_limit = _normalize_limit(limit)

        sql = "SELECT * FROM sessions"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if session_id:
            clauses.append("session_id LIKE ?")
            params.append(f"{session_id}%")
        current_user = resolve_user()
        if current_user is not None:
            clauses.append("owner_user = ?")
            params.append(current_user)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, session_id DESC LIMIT ? OFFSET ?"
        params.extend([normalized_limit + 1, offset])
        rows = self._metadata.query_rows(sql, params)

        has_next_page = len(rows) > normalized_limit
        items = [_session_from_row(row) for row in rows[:normalized_limit]]
        next_page_token = str(offset + normalized_limit) if has_next_page else None
        return {"items": items, "next_page_token": next_page_token}


class SqlSessionStore:
    """Event-sourced session store backed by the ``session_events`` table.

    Unlike ``SqlSessionStoreAdapter`` which translates events into CRUD
    operations on the ``sessions`` table, this class appends every event
    as a row in ``session_events`` and rebuilds ``SessionState`` by folding
    over the event log via ``rebuild_session_state``.

    Uses ``MetadataStore`` for SQL execution so it works with both SQLite
    and MySQL backends.

    Concurrent-write safety:
      The ``session_events`` table has a ``UNIQUE(session_id, seq)`` constraint.
      ``append_event`` computes ``seq = MAX(seq) + 1`` and retries up to
      ``_UNIQ_RETRIES`` times on UNIQUE violation (concurrent writers may
      have grabbed the same seq).
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    # ------------------------------------------------------------------
    # Core event-sourced operations
    # ------------------------------------------------------------------

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        """Append an event to the session event log.

        Computes the next sequence number for the session and inserts a
        new row.  If a concurrent writer grabbed the same seq (UNIQUE
        violation), retries up to ``_UNIQ_RETRIES`` times, each in a
        fresh transaction.

        After successfully inserting into ``session_events``, also upserts
        the ``sessions`` table to keep it in sync as a read model /
        materialized view.
        """
        for attempt in range(_UNIQ_RETRIES):
            row = self._metadata.query_one(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_events WHERE session_id = ?",
                [str(session_id)],
            )
            next_seq = (row["max_seq"] if row else 0) + 1

            try:
                self._metadata.execute(
                    "INSERT INTO session_events "
                    "(session_id, seq, event_type, timestamp, actor, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        str(session_id),
                        next_seq,
                        event.event_type,
                        event.timestamp,
                        str(event.actor) if event.actor else None,
                        json.dumps(event.payload, sort_keys=True),
                    ],
                )
                # Dual-write: keep sessions table in sync as a read model.
                self._sync_sessions_read_model(session_id, event)
                return
            except Exception as exc:
                # SQLite raises sqlite3.IntegrityError; MySQL raises
                # mysql.connector.IntegrityError.  Both carry a message
                # mentioning "UNIQUE constraint" or similar.
                msg = str(exc).lower()
                if "unique" in msg and attempt < _UNIQ_RETRIES - 1:
                    logger.debug(
                        "SqlSessionStore: UNIQUE violation on seq=%d for "
                        "session_id=%s, retry %d/%d",
                        next_seq,
                        session_id,
                        attempt + 1,
                        _UNIQ_RETRIES,
                    )
                    continue
                raise

    def append_event_with_connection(
        self, session_id: SessionId, event: SessionEvent, con: Any
    ) -> None:
        """Append an event using an existing connection (shared transaction).

        This allows the event insert to participate in the same database
        transaction as other writes (e.g. artifact commits), guaranteeing
        atomicity.  The caller is responsible for calling ``con.commit()``
        (or rolling back) on the shared connection.

        Unlike :meth:`append_event`, this method does **not** retry on
        UNIQUE violations because the caller owns the transaction and a
        retry would need a fresh transaction to observe the new MAX(seq).

        The MAX(seq) query is executed on *con* so that uncommitted rows
        already inserted in this transaction are visible, ensuring correct
        sequence numbering when appending multiple events.

        After inserting into ``session_events``, also upserts the
        ``sessions`` table on the same connection to keep the read model
        in sync within the shared transaction.
        """
        cursor = self._metadata.execute_sql(
            con,
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_events WHERE session_id = ?",
            [str(session_id)],
        )
        row = cursor.fetchone()
        next_seq = (row[0] if row else 0) + 1

        self._metadata.execute_sql(
            con,
            "INSERT INTO session_events "
            "(session_id, seq, event_type, timestamp, actor, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                str(session_id),
                next_seq,
                event.event_type,
                event.timestamp,
                str(event.actor) if event.actor else None,
                json.dumps(event.payload, sort_keys=True),
            ],
        )

        # Dual-write: keep sessions table in sync on the shared connection.
        self._sync_sessions_read_model_on_connection(session_id, event, con)

    # ------------------------------------------------------------------
    # Dual-write helpers: sync sessions read model
    # ------------------------------------------------------------------

    def _sync_sessions_read_model(self, session_id: SessionId, event: SessionEvent) -> None:
        """Upsert the ``sessions`` table after a successful event append.

        Uses ``MetadataStore.upsert_by_key`` for dialect-safe upserts
        (SQLite ON CONFLICT / MySQL ON DUPLICATE KEY UPDATE).
        """
        if event.event_type == "session_created":
            goal = event.payload.get("goal", "")
            constraints = event.payload.get("constraints")
            budget = event.payload.get("budget")
            raw_filter = event.payload.get("raw_filter")
            owner_user = str(event.actor) if event.actor else ""

            self._metadata.upsert_by_key(
                table="sessions",
                insert_columns=[
                    "session_id",
                    "goal",
                    "constraints_json",
                    "budget_json",
                    "owner_user",
                    "status",
                    "raw_filter",
                    "created_at",
                    "updated_at",
                ],
                values=[
                    str(session_id),
                    goal,
                    json.dumps(constraints or {}, default=str, sort_keys=True),
                    json.dumps(budget or {}, default=str, sort_keys=True),
                    owner_user,
                    "open",
                    raw_filter,
                    event.timestamp,
                    event.timestamp,
                ],
                conflict_columns=["session_id"],
                update_columns=[
                    "goal",
                    "constraints_json",
                    "budget_json",
                    "owner_user",
                    "status",
                    "raw_filter",
                    "updated_at",
                ],
            )
        elif event.event_type == "session_terminated":
            terminal_reason = event.payload.get("terminal_reason", "user_closed")
            self._metadata.execute(
                f"UPDATE sessions "
                f"SET status = 'closed', "
                f"terminal_reason = ?, "
                f"ended_at = {self._metadata.dialect.now_sql()}, "
                f"updated_at = {self._metadata.dialect.now_sql()} "
                f"WHERE session_id = ?",
                [terminal_reason, str(session_id)],
            )

    def _sync_sessions_read_model_on_connection(
        self, session_id: SessionId, event: SessionEvent, con: Any
    ) -> None:
        """Upsert the ``sessions`` table on a shared connection.

        Same logic as :meth:`_sync_sessions_read_model` but uses
        ``execute_sql`` on the caller-provided connection so that the
        dual-write participates in the same transaction.
        """
        dialect = self._metadata.dialect

        if event.event_type == "session_created":
            goal = event.payload.get("goal", "")
            constraints = event.payload.get("constraints")
            budget = event.payload.get("budget")
            raw_filter = event.payload.get("raw_filter")
            owner_user = str(event.actor) if event.actor else ""

            # Use dialect.upsert_sql to generate dialect-aware SQL,
            # then execute on the shared connection.
            upsert_sql = dialect.upsert_sql(
                table="sessions",
                insert_columns=[
                    "session_id",
                    "goal",
                    "constraints_json",
                    "budget_json",
                    "owner_user",
                    "status",
                    "raw_filter",
                    "created_at",
                    "updated_at",
                ],
                conflict_columns=["session_id"],
                update_columns=[
                    "goal",
                    "constraints_json",
                    "budget_json",
                    "owner_user",
                    "status",
                    "raw_filter",
                    "updated_at",
                ],
            )
            # compile_sql handles placeholder replacement (? -> %s for MySQL)
            compiled = dialect.compile_sql(upsert_sql)
            con.execute(
                compiled,
                [
                    str(session_id),
                    goal,
                    json.dumps(constraints or {}, default=str, sort_keys=True),
                    json.dumps(budget or {}, default=str, sort_keys=True),
                    owner_user,
                    "open",
                    raw_filter,
                    event.timestamp,
                    event.timestamp,
                ],
            )
        elif event.event_type == "session_terminated":
            terminal_reason = event.payload.get("terminal_reason", "user_closed")
            update_sql = (
                f"UPDATE sessions "
                f"SET status = 'closed', "
                f"terminal_reason = ?, "
                f"ended_at = {dialect.now_sql()}, "
                f"updated_at = {dialect.now_sql()} "
                f"WHERE session_id = ?"
            )
            compiled = dialect.compile_sql(update_sql)
            con.execute(compiled, [terminal_reason, str(session_id)])

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        """Load all events for ``session_id``, ordered by sequence.

        Raises ``NotFoundError(SESSION_NOT_FOUND)`` when the session has
        no events.
        """
        from marivo.contracts.errors import ErrorCode, NotFoundError

        rows = self._metadata.query_rows(
            "SELECT session_id, event_type, timestamp, actor, payload_json "
            "FROM session_events WHERE session_id = ? ORDER BY seq",
            [str(session_id)],
        )
        if not rows:
            raise NotFoundError(
                code=ErrorCode.SESSION_NOT_FOUND,
                message=f"Session {session_id!r} not found",
            )
        return [
            SessionEvent(
                session_id=SessionId(r["session_id"]),
                event_type=r["event_type"],
                timestamp=r["timestamp"],
                payload=json.loads(r["payload_json"]),
                actor=UserId(r["actor"]) if r.get("actor") else None,
            )
            for r in rows
        ]

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        """List sessions owned by ``owner``, rebuilt from event log.

        Owner invariant: a session's owner is the ``actor`` of its
        ``session_created`` event.
        """
        rows = self._metadata.query_rows(
            "SELECT session_id FROM session_events "
            "WHERE event_type = 'session_created' AND actor = ? "
            "ORDER BY timestamp ASC, session_id ASC",
            [str(owner)],
        )
        result: list[SessionState] = []
        for row in rows:
            events = self.load_events(SessionId(row["session_id"]))
            result.append(rebuild_session_state(events))
        return result

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status.

        Delegates to the same logic as ``SqlSessionStoreAdapter`` since
        proposition tracking lives in the evidence pipeline tables, not
        in the session event log.
        """
        from marivo.adapters.server.evidence_repositories import (
            ActionProposalRepository,
            AssessmentRepository,
        )

        row = self._metadata.query_one(
            """
            SELECT proposition_id, session_id, externally_visible_assessment_id
            FROM propositions
            WHERE proposition_id = ? AND session_id = ?
            """,
            [proposition_id, session_id],
        )
        if row is None:
            raise KeyError(f"proposition {proposition_id!r} not found in session {session_id!r}")

        ev_assessment_id: str | None = row.get("externally_visible_assessment_id")

        assessment_repo = AssessmentRepository(self._metadata)
        latest = assessment_repo.get_latest(proposition_id)

        proposals: list[dict[str, Any]] = []
        if latest is not None:
            proposal_repo = ActionProposalRepository(self._metadata)
            proposals = proposal_repo.list_by_assessment(session_id, latest["assessment_id"])

        if ev_assessment_id:
            current_stage = "externally_visible"
            last_successful_stage: str | None = "publish"
        elif latest is not None and proposals:
            current_stage = "publish_ready"
            last_successful_stage = "proposal_refresh"
        elif latest is not None:
            current_stage = "assessment_committed"
            last_successful_stage = "assessment_committed"
        else:
            current_stage = "queued"
            last_successful_stage = None

        return {
            "session_id": session_id,
            "proposition_id": proposition_id,
            "current_stage": current_stage,
            "last_successful_stage": last_successful_stage,
            "current_assessment_id": latest["assessment_id"] if latest is not None else None,
            "current_attempt": None,
            "backlog_state": "none",
            "last_failure_reason": "none",
            "last_failure_at": None,
            "schema_version": "proposition_runtime_status.v1",
        }

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        """Return a paginated list of sessions (server-mode only).

        Supports filtering by status, session_id prefix, and owner_user.
        Rebuilds each session from its event log rather than querying
        the CRUD ``sessions`` table.
        """
        from marivo.identity import resolve_user

        status = kwargs.get("status")
        session_id = kwargs.get("session_id")
        limit = kwargs.get("limit")
        page_token = kwargs.get("page_token")

        offset = _decode_page_token(page_token)
        normalized_limit = _normalize_limit(limit)

        # Collect candidate session_ids from session_created events
        sql = (
            "SELECT session_id, timestamp FROM session_events WHERE event_type = 'session_created'"
        )
        clauses: list[str] = []
        params: list[Any] = []

        current_user = resolve_user()
        if current_user is not None:
            clauses.append("actor = ?")
            params.append(current_user)
        if session_id:
            clauses.append("session_id LIKE ?")
            params.append(f"{session_id}%")
        if clauses:
            sql += " AND " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, session_id DESC"

        created_rows = self._metadata.query_rows(sql, params)

        # Filter by status (requires rebuilding state) then paginate
        matching: list[SessionState] = []
        for row in created_rows:
            sid = SessionId(row["session_id"])
            events = self.load_events(sid)
            state = rebuild_session_state(events)
            if status and _api_status(state.status) != status:
                continue
            matching.append(state)

        has_next_page = len(matching) > offset + normalized_limit
        page_items = matching[offset : offset + normalized_limit]
        items = [_session_state_to_dict(s) for s in page_items]
        next_page_token = str(offset + normalized_limit) if has_next_page else None
        return {"items": items, "next_page_token": next_page_token}
